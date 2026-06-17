"""core.index — индексация репозитория: файлы, символы, чанки, YAML-ключи, MD-секции.

build_index(root) обходит дерево файлов и строит структуру данных для поиска.
Символы извлекаются через LanguageAdapter (по умолчанию — RegexAdapter).
YAML/MD парсинг — встроенный (stdlib, без PyYAML).
"""
import re
from pathlib import Path

__all__ = [
    "CODE_EXT", "INDEX_EXT", "INDEX_NAMES",
    "SECRET_EXT", "SECRET_HINTS", "SKIP_DIRS",
    "MAX_FILE_BYTES", "MAX_LINE_LEN", "WIN", "OVERLAP",
    "parse_yaml_keys", "parse_md_sections", "build_index",
]

# Настоящий исходный код — отсюда извлекаем таблицу символов (def/class/...).
# Шаблоны/доки/конфиги исключены: иначе `type X`/`const X` из .tpl/.html/прозы
# засоряют символы мусором ('to', 'here', 'defined').
CODE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".cs", ".kt", ".swift", ".scala", ".sh", ".bash",
}
# Что индексируем (для поиска): код + конфиги/инфра + документация.
INDEX_EXT = CODE_EXT | {
    ".yaml", ".yml", ".tpl", ".json", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".xml", ".html", ".md", ".txt", ".sql", ".tf", ".gradle",
}
# Файлы без расширения, которые всё равно индексируем по имени.
INDEX_NAMES = {"Makefile", "Dockerfile", "Jenkinsfile"}
# Никогда не индексируем: секреты и бинарь (чтобы не утекли в облако).
SECRET_EXT = {".pem", ".p12", ".pfx", ".key", ".crt", ".cer", ".der", ".jks",
              ".srl", ".keystore", ".gpg", ".asc"}
SECRET_HINTS = ("credential", "secret", "password", "private", "id_rsa", ".env")
# Каталоги, которые пропускаем целиком.
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "__pycache__",
             ".venv", "venv", ".idea", ".gradle", ".groundqa_sandbox"}
MAX_FILE_BYTES = 400_000   # очень большие файлы (бандлы/дампы) пропускаем
MAX_LINE_LEN = 2000        # минифицированный код: строки-монстры -> пропуск

WIN = 25          # размер чанка в строках
OVERLAP = 6       # перекрытие чанков


def _skip_reason(path: Path) -> str | None:
    """Почему файл не индексируем (None = индексируем)."""
    name = path.name
    low = name.lower()
    if any(part in SKIP_DIRS for part in path.parts):
        return "dir"
    if path.suffix.lower() in SECRET_EXT or any(h in low for h in SECRET_HINTS):
        return "secret"
    if path.suffix not in INDEX_EXT and name not in INDEX_NAMES:
        return "ext"
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return "too_big"
    except OSError:
        return "stat"
    return None


# --- Структурный слой: индекс путей YAML-ключей (stdlib, без PyYAML) --------
_YAML_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_.\-/]+):\s*(.*)$")
_YAML_LIST_RE = re.compile(r"^(\s*)-\s+(.*)$")


def _yaml_strip_comment(s: str) -> str:
    """Убрать инлайн-комментарий ' #...' вне кавычек (грубо, но достаточно)."""
    in_q = None
    out = []
    for i, ch in enumerate(s):
        if in_q:
            out.append(ch)
            if ch == in_q:
                in_q = None
        elif ch in "\"'":
            in_q = ch
            out.append(ch)
        elif ch == "#" and (i == 0 or s[i - 1] == " "):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _yaml_entry(rel: str, lineno: int, path: str, value) -> dict:
    segs = path.split(".")
    return {"file": rel, "line": lineno, "path": path, "leaf": segs[-1],
            "segs": segs, "value": value, "depth": len(segs), "root": segs[0]}


def parse_yaml_keys(rel: str, text: str) -> list[dict]:
    """Карта ключей YAML по отступам. Списочный элемент '- name: x' даёт путь
    '<родитель>[].name'; скалярный '- x' — путь '<родитель>[]' со значением x."""
    entries: list[dict] = []
    stack: list[tuple[int, str]] = []  # (отступ, ключ) — текущий путь
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m_list = _YAML_LIST_RE.match(raw)
        if m_list:
            indent = len(m_list.group(1))
            content = m_list.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            parent = ".".join(k for _, k in stack)
            base = (parent + "[]") if parent else "[]"
            mk = _YAML_KEY_RE.match(content)
            stack.append((indent, "[]"))  # сам элемент списка
            if mk:
                key = mk.group(2)
                val = _yaml_strip_comment(mk.group(3)).strip()
                entries.append(_yaml_entry(rel, lineno, base + "." + key,
                                           val or None))
                stack.append((indent + 2, key))  # ключ внутри '- ' (на 2 правее)
            else:
                val = _yaml_strip_comment(content).strip()
                entries.append(_yaml_entry(rel, lineno, base, val or None))
            continue
        m = _YAML_KEY_RE.match(raw)
        if not m:
            continue
        indent = len(m.group(1))
        key = m.group(2)
        val = _yaml_strip_comment(m.group(3)).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = (".".join(k for _, k in stack) + "." + key) if stack else key
        entries.append(_yaml_entry(rel, lineno, path, val or None))
        stack.append((indent, key))
    return entries


_MD_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def parse_md_sections(rel: str, text: str) -> list[dict]:
    """Карта секций Markdown: заголовок -> (строка, уровень, тело до следующего
    заголовка того же/высшего уровня)."""
    lines = text.splitlines()
    heads = []  # (lineno, level, title)
    for i, ln in enumerate(lines, start=1):
        m = _MD_HEAD_RE.match(ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    sections = []
    for idx, (lineno, level, title) in enumerate(heads):
        end = len(lines)
        for l2, lvl2, _t in heads[idx + 1:]:
            if lvl2 <= level:
                end = l2 - 1
                break
        body = lines[lineno:end]  # строки после заголовка (1-based: со lineno+1)
        sections.append({"file": rel, "title": title, "line": lineno,
                         "level": level, "body": body, "body_start": lineno + 1})
    return sections


def build_index(root: Path, adapters=None) -> dict:
    """Построить индекс репозитория.

    adapters: list[LanguageAdapter] | None — если None, используется RegexAdapter.
    """
    if adapters is None:
        from greenlock.adapters import detect_adapters
        adapters = detect_adapters()

    # Маппинг расширение → адаптер (первый подходящий)
    ext_to_adapter = {}
    for adapter in adapters:
        for ext in adapter.extensions:
            if ext not in ext_to_adapter:
                ext_to_adapter[ext] = adapter

    files: dict[str, str] = {}
    symbols: dict[str, list] = {}
    chunks: list[dict] = []
    yaml_keys: list[dict] = []
    md_sections: list[dict] = []
    skipped: dict[str, int] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        reason = _skip_reason(path)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped["binary"] = skipped.get("binary", 0) + 1
            continue
        if any(len(ln) > MAX_LINE_LEN for ln in text.splitlines()[:50]):
            skipped["minified"] = skipped.get("minified", 0) + 1
            continue
        rel = str(path.relative_to(root))
        files[rel] = text
        lines = text.splitlines()
        suffix = path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            yaml_keys.extend(parse_yaml_keys(rel, text))
        elif suffix in (".md", ".markdown"):
            md_sections.extend(parse_md_sections(rel, text))
        # Символы через адаптер
        adapter = ext_to_adapter.get(suffix)
        if adapter:
            result = adapter.parse(rel, text)
            for sym in result.symbols:
                symbols.setdefault(sym["name"], []).append((sym["file"], sym["line"]))
        # Чанкование
        i = 0
        while i < len(lines):
            seg = lines[i:i + WIN]
            chunks.append({"rel": rel, "start": i + 1, "lines": seg,
                           "text": "\n".join(seg)})
            if i + WIN >= len(lines):
                break
            i += WIN - OVERLAP
    return {"files": files, "symbols": symbols, "chunks": chunks,
            "yaml_keys": yaml_keys, "md_sections": md_sections,
            "skipped": skipped}
