"""core.structural — детерминированный структурный слой (без вызова модели).

structural_answer: диспетчер с plugin-хуками. Встроенные хендлеры (универсальные):
_enumerate_list, _scalar_answer, _symbol_answer, _md_heading_answer.
Плагины добавляют доменные хендлеры (based_on, database и т.п.).

plugins=None → авто-загрузка плагинов (кэш на уровне модуля).
plugins=[]  → плагины отключены.
"""
import re
from pathlib import Path

from greenlock.utils import _norm, _zero_usage, query_key_tokens
from greenlock.citations import verify_citations
from greenlock.index import CODE_EXT

__all__ = [
    "SYMBOL_TRIGGERS", "STRUCT_SCALAR_FLOOR", "STRUCT_TIE_DELTA",
    "structural_answer",
]

# «Где определён символ X» — точная локализация функции/класса по коду.
# Универсально для любого проекта.
SYMBOL_TRIGGERS = ("где определ", "в каком файле", "где находит", "где объявл",
                   "где живёт", "где живет", "defined", "where is", "located",
                   "определена функц", "определён", "определен")

STRUCT_SCALAR_FLOOR = 5.0   # минимальный скор для детерминированного скаляр-ответа
STRUCT_TIE_DELTA = 0.75     # почти-равные хиты: при разных значениях — неоднозначно
_DEFAULT_PLUGINS = None


def _component_names(keys: list[dict]) -> set[str]:
    """Имена-«области»: ключи-РОДИТЕЛИ (имеют детей) и имена зависимостей.

    Это SCOPE (mongodb, ditto, grafana, service...), а не запрашиваемое значение —
    слово 'mongodb' в вопросе задаёт компонент, а не просит значение ключа с
    именем 'mongodb' (иначе nodePorts.mongodb даёт уверенно неверный ответ).
    """
    names = set()
    for e in keys:
        segs = e["path"].split(".")
        for i in range(1, len(segs)):           # каждый предок пути — родитель
            names.add(_norm(segs[i - 1]))
        if e["path"].endswith("[].name") and e["value"]:
            names.add(_norm(e["value"]))         # имя зависимости
    names.discard("")
    return names


def _struct_score(e: dict, qtoks: set[str], components: set[str],
                  auth_files: frozenset[str] = frozenset()) -> tuple[float, bool]:
    """Скор записи-ключа против токенов запроса.

    auth_files: авторитетные корневые файлы (от плагина, напр. chart.yaml).
    По умолчанию — пустое множество (нет бустов).
    """
    leaf = _norm(e["leaf"])
    segs = [_norm(s) for s in e["segs"]]
    leaf_hit = leaf in qtoks and leaf not in components
    score = 3.0 if leaf_hit else 0.0
    score += 2.0 * sum(1 for s in segs[:-1] if s in qtoks)  # сегменты-компоненты
    if _norm(e["root"]) in qtoks:           # спрошенный компонент — корневой блок
        score += 1.5
        if e["depth"] == 2:                 # это прямой флаг компонента <comp>.<key>
            score += 1.5
    name = Path(e["file"]).name.lower()
    if e["file"].replace("\\", "/").count("/") == 0 and name in auth_files:
        score += 1.0
    score -= 0.3 * e["depth"]               # мельче путь — авторитетнее
    if e["value"] is not None:
        score += 0.5                        # у листа есть готовое значение
    return score, leaf_hit


def _enumerate_list(keys: list[dict], qtoks: set[str], index: dict,
                    auth_files: frozenset[str] = frozenset()):
    """Перечисление списка '<root>[].name', когда сам <root> назван в вопросе."""
    groups: dict[tuple, list] = {}
    for e in keys:
        if e["leaf"] == "name" and e["value"] and e["path"].endswith("[].name"):
            root = e["path"][:-len("[].name")]      # 'dependencies'
            if _norm(root) in qtoks:
                groups.setdefault((e["file"], root), []).append(e)
    if not groups:
        return None

    def rank(kv):
        (file, _root), kids = kv
        name = Path(file).name.lower()
        auth = file.replace("\\", "/").count("/") == 0 and name in auth_files
        return (auth, len(kids))

    (_file, root), kids = max(groups.items(), key=rank)
    if len(kids) < 2:
        return None
    # Вопрос называет конкретный элемент списка → это не запрос всей коллекции
    if any(_norm(k["value"]) in qtoks for k in kids):
        return None
    names = [k["value"] for k in kids]
    cites_txt = ", ".join(f"{k['file']}:{k['line']}" for k in kids[:8])
    ans = f"{root}: " + ", ".join(names) + f" ({cites_txt})."
    return ans, verify_citations(ans, index), _zero_usage()


def _scalar_answer(keys: list[dict], qtoks: set[str], index: dict,
                   components: set[str],
                   auth_files: frozenset[str] = frozenset()):
    """Скаляр «ключ = значение» (appVersion, hono.enabled, nodePort)."""
    scored = []
    for e in keys:
        s, leaf_hit = _struct_score(e, qtoks, components, auth_files=auth_files)
        if leaf_hit or s > 0:
            scored.append((s, leaf_hit, e))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored or not scored[0][1]:      # лучший хит должен называть КЛЮЧ
        return None
    best_s, _, best = scored[0]
    if best["value"] is None or best_s < STRUCT_SCALAR_FLOOR:
        return None
    # Уверены, только если близкие по скору хиты не дают другого значения.
    near_vals = {_norm(e["value"]) for s, _, e in scored
                 if best_s - s <= STRUCT_TIE_DELTA and e["value"] is not None}
    if len(near_vals) > 1:
        return None
    ans = f"{best['path']} = {best['value']} ({best['file']}:{best['line']})."
    return ans, verify_citations(ans, index), _zero_usage()


def _symbol_answer(index: dict, query: str):
    """«Где определена функция/класс X» — точная локализация по таблице символов
    кода. Требует и явный интент локализации, и точное имя-идентификатор."""
    syms = index.get("symbols") or {}
    if not syms or not any(t in query.lower() for t in SYMBOL_TRIGGERS):
        return None
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", query):
        if len(name) < 3:
            continue
        locs = [(f, ln) for f, ln in syms.get(name, [])
                if Path(f).suffix.lower() in CODE_EXT]
        if not locs:
            continue
        cites = ", ".join(f"{f}:{ln}" for f, ln in locs[:5])
        ans = f"Символ {name} определён в {cites}."
        return ans, verify_citations(ans, index), _zero_usage()
    return None


def _md_heading_answer(index: dict, qtoks: set[str]):
    """Секция Markdown, чей заголовок назван в вопросе.

    H1 (level=1) пропускается: это заголовок документа / имя проекта,
    а не секция-список. Без этого гарда имя проекта в H1 матчится
    на любой вопрос, содержащий это слово, и выдаёт буллеты из тела
    (Prerequisites и т.п.) — уверенно-неверный ответ.
    """
    for s in index.get("md_sections") or []:
        if s["level"] <= 1:             # H1 = заголовок документа, не секция
            continue
        if _norm(s["title"]) not in qtoks:
            continue
        items = []
        for off, ln in enumerate(s["body"]):
            mb = re.match(r"^\s*[-*]\s+(.*)", ln)
            if mb and mb.group(1).strip():
                items.append((s["body_start"] + off, mb.group(1).strip()))
        if len(items) >= 2:
            cites = [f"{s['file']}:{s['line']}"] + \
                    [f"{s['file']}:{ln}" for ln, _ in items[:8]]
            ans = (f"{s['title']}: " + ", ".join(t for _, t in items) +
                   f" ({', '.join(cites[:8])}).")
            return ans, verify_citations(ans, index), _zero_usage()
    return None


def structural_answer(index: dict, query: str, plugins=None):
    """Детерминированный ответ из структурных индексов, БЕЗ вызова модели.

    Возвращает (answer, cites, usage) или None, если уверенного хита нет.

    plugins=None → авто-загрузить и закэшировать (дефолт = ВКЛ).
    plugins=[]  → отключить доменные хендлеры.
    """
    global _DEFAULT_PLUGINS
    if plugins is None:
        if _DEFAULT_PLUGINS is None:
            from greenlock.domain_plugins import load_plugins
            _DEFAULT_PLUGINS = load_plugins()
        plugins = _DEFAULT_PLUGINS

    # Собрать key_hints и auth_files от плагинов
    extra_hints: dict[str, list[str]] = {}
    auth_files: set[str] = set()
    for p in plugins:
        extra_hints.update(p.key_hints())
        auth_files.update(p.auth_files())
    auth_frozen = frozenset(auth_files)

    qtoks = query_key_tokens(query, extra_hints=extra_hints or None)
    if not qtoks:
        return None
    keys = index.get("yaml_keys") or []
    components = _component_names(keys)

    # 1. Встроенные хендлеры ДО плагинов (по приоритету оригинала)
    builtins_pre = [
        _enumerate_list(keys, qtoks, index, auth_files=auth_frozen),
        _scalar_answer(keys, qtoks, index, components, auth_files=auth_frozen),
        _symbol_answer(index, query),
    ]

    # 2. Хендлеры от плагинов (based_on, database — между symbol и md_heading)
    plugin_hits = []
    for p in plugins:
        plugin_hits.extend(p.handlers(index, query, qtoks))

    # 3. Встроенные хендлеры ПОСЛЕ плагинов
    builtins_post = [
        _md_heading_answer(index, qtoks),
    ]

    # Первый непустой hit (порядок: builtins_pre → plugins → builtins_post)
    for hit in builtins_pre + plugin_hits + builtins_post:
        if hit:
            return hit
    return None
