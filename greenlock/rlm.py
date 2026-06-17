"""core.rlm — управление RLM-карточками знаний.

Включает детерминированное извлечение verified-полей, хэширование спанов,
запрос к большой модели для advisory-описаний, кэширование и валидацию.
"""
import hashlib
import json
import logging
import re
from pathlib import Path
from greenlock.adapters import detect_adapters
from greenlock.qa import generate
from greenlock.citations import verify_citations

logger = logging.getLogger(__name__)

__all__ = ["get_symbol_span_hash", "extract_verified_fields", "get_or_build_card", "rlm_cache_path"]


def rlm_cache_path(repo: str) -> Path:
    """Путь к JSON-файлу кэша RLM-карточек."""
    cache_dir = Path(__file__).parent.parent / ".groundqa_cache"
    cache_dir.mkdir(exist_ok=True)
    tag = hashlib.sha1(str(Path(repo).resolve()).encode()).hexdigest()[:12]
    return cache_dir / f"rlm_cards_{tag}.json"


def get_symbol_span_hash(content: str, span_start: int, span_end: int) -> str:
    """Вычислить стабильный MD5-хэш тела символа."""
    if span_start is None or span_end is None:
        return ""
    lines = content.splitlines()
    if 1 <= span_start <= span_end <= len(lines):
        body = "\n".join(lines[span_start - 1 : span_end])
        return hashlib.md5(body.encode("utf-8")).hexdigest()
    return ""


def extract_params(signature: str) -> list[str]:
    """Грубое извлечение списка имён параметров из сигнатуры."""
    m = re.search(r"\((.*)\)", signature)
    if not m:
        return []
    inside = m.group(1).strip()
    if not inside:
        return []
    params = []
    for p in inside.split(","):
        p = p.strip()
        if "=" in p:
            p = p.split("=")[0].strip()
        if ":" in p:
            p = p.split(":")[0].strip()
        if p and p not in ("self", "cls"):
            params.append(p)
    return params


def extract_returns(signature: str) -> str:
    """Грубое извлечение возвращаемого типа из сигнатуры (для Python)."""
    if "->" in signature:
        return signature.split("->")[-1].replace(":", "").strip()
    return ""


def extract_verified_fields(index: dict, filepath: str, sym: dict) -> dict:
    """Собрать детерминированные (verified) метаданные символа из AST/индекса."""
    content = index["files"].get(filepath, "")
    lines = content.splitlines()
    
    # 1. Signature
    span_start = sym.get("span_start")
    span_end = sym.get("span_end")
    if span_start is not None and 1 <= span_start <= len(lines):
        signature = lines[span_start - 1].strip()
    else:
        signature = ""

    # 2. Params / Returns
    params = extract_params(signature)
    returns = extract_returns(signature)

    # 3. Location
    location = f"{filepath}:{sym.get('line', 1)}"

    # 4. Imports
    file_imports = []
    # Найдём импорты для этого файла через парсинг адаптером
    suffix = Path(filepath).suffix
    adapter = None
    for a in detect_adapters():
        if suffix in a.extensions:
            adapter = a
            break
            
    res_imports = []
    res_refs = []
    if adapter:
        try:
            res = adapter.parse(filepath, content)
            res_imports = res.imports
            res_refs = res.refs
        except Exception:
            pass

    for imp in res_imports:
        mod = imp.get("module")
        names = imp.get("names", [])
        if mod:
            file_imports.append(f"from {mod} import {', '.join(names)}")
        else:
            file_imports.append(f"import {', '.join(names)}")

    # 5. Callers
    callers = []
    sym_name = sym["name"]
    # Сканируем refs других файлов
    for other_path, other_content in index["files"].items():
        if sym_name in other_content:
            for idx, line in enumerate(other_content.splitlines(), start=1):
                if re.search(r"\b" + re.escape(sym_name) + r"\b", line):
                    # Проверяем, что это не объявление самого символа
                    if not re.search(r"\b(def|class|function)\s+" + re.escape(sym_name) + r"\b", line):
                        callers.append(f"{other_path}:{idx}")
    callers = sorted(list(set(callers)))[:10]

    # 6. Callees (только те, что лежат внутри спана и есть в символах индекса)
    callees = []
    if span_start is not None and span_end is not None:
        for ref in res_refs:
            if span_start <= ref["line"] <= span_end:
                ref_name = ref["name"]
                if ref_name in index.get("symbols", {}) and ref_name != sym_name:
                    callees.append(ref_name)
    callees = sorted(list(set(callees)))

    return {
        "signature": signature,
        "params": params,
        "returns": returns,
        "location": location,
        "imports": file_imports,
        "callers": callers,
        "callees": callees,
    }


SYSTEM_RLM_PROMPT = (
    "Ты — технический писатель и эксперт по коду. Опиши указанный символ (функцию/метод/класс) "
    "на основе предоставленного исходного кода.\n"
    "Ответь СТРОГО в формате JSON с полями:\n"
    "{\n"
    "  \"purpose\": \"Краткое текстовое описание назначения символа (на русском). Используй ссылки в формате файл:строка для подтверждения фактов.\",\n"
    "  \"recipe\": \"Краткий пример или рецепт использования символа в коде.\",\n"
    "  \"citations\": [\"список точных цитатных строк вида файл:строка, откуда взяты факты из purpose/recipe\"]\n"
    "}\n"
    "Никакого текста до и после JSON."
)


def build_rlm_card(args, index: dict, filepath: str, sym: dict) -> dict:
    """Запросить у большой модели advisory-поля карточки знаний."""
    content = index["files"].get(filepath, "")
    span_start = sym.get("span_start")
    span_end = sym.get("span_end")
    
    sym_content = ""
    if span_start is not None and span_end is not None:
        lines = content.splitlines()
        sym_content = "\n".join(lines[span_start - 1 : span_end])

    prompt = (
        f"Опиши класс/метод/функцию '{sym['name']}' в файле '{filepath}'.\n"
        f"Тип: {sym['kind']}\n"
        f"Строка объявления: {sym.get('line', 1)}\n"
        f"Тело кода символа:\n"
        f"```\n{sym_content}\n```\n\n"
        "Выдай строго JSON-объект по шаблону."
    )

    model = args.escalate if args.escalate else args.model
    try:
        response, usage = generate(args, model, SYSTEM_RLM_PROMPT, prompt)
    except Exception as e:
        logger.debug(f"Failed to generate RLM card for {sym['name']}: {e}")
        return {"purpose": "(ошибка генерации)", "recipe": "", "citations": []}

    # Парсинг JSON
    s = response.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    try:
        data = json.loads(s)
    except Exception:
        # Попытка найти блок {}
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = {}
        else:
            data = {}

    purpose = data.get("purpose", "")
    recipe = data.get("recipe", "")
    cites = data.get("citations", [])

    # Верификация цитат
    full_text = purpose + "\n" + recipe
    verified_cites = verify_citations(full_text, index)
    valid_cites = [c for c, ok in verified_cites if ok]

    return {
        "purpose": purpose,
        "recipe": recipe,
        "citations": valid_cites,
    }


def find_symbol_in_file(index: dict, filepath: str, sym_name: str) -> dict | None:
    """Парсит файл через адаптер и находит символ по имени."""
    content = index["files"].get(filepath)
    if not content:
        return None
    suffix = Path(filepath).suffix
    adapter = None
    for a in detect_adapters():
        if suffix in a.extensions:
            adapter = a
            break
    if not adapter:
        return None
    try:
        res = adapter.parse(filepath, content)
        for s in res.symbols:
            if s["name"] == sym_name:
                return s
    except Exception:
        pass
    return None


def get_or_build_card(args, index: dict, filepath: str, sym_name: str) -> dict | None:
    """Получить карточку из кэша (с валидацией по хэшу спана) или построить заново."""
    sym = find_symbol_in_file(index, filepath, sym_name)
    if not sym:
        return None

    content = index["files"].get(filepath, "")
    span_start = sym.get("span_start")
    span_end = sym.get("span_end")
    current_hash = get_symbol_span_hash(content, span_start, span_end)

    # 1. Загрузка кэша
    cache_file = rlm_cache_path(args.repo)
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    key = f"{filepath}::{sym_name}"
    card = cache.get(key)

    # 2. Проверка валидности кэша (Сверка хэшей)
    if card and card.get("span_hash") == current_hash:
        return card

    # 3. Карточка устарела или отсутствует — строим заново
    logger.info(f"RLM: Building card for {sym_name} in {filepath}...")
    verified = extract_verified_fields(index, filepath, sym)
    advisory = build_rlm_card(args, index, filepath, sym)

    new_card = {
        "id": f"rlm_{hashlib.md5(key.encode()).hexdigest()[:8]}",
        "file": filepath,
        "symbol": sym_name,
        "span_hash": current_hash,
        "verified": verified,
        "advisory": advisory,
        "model": args.escalate if args.escalate else args.model,
    }

    # Сохраняем в кэш
    cache[key] = new_card
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to write RLM cache: {e}")

    return new_card
