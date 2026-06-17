"""core.search — семантический поиск: эмбеддинги, кэш, ранжирование.

embed_chunks_cached: батчевое кэширование эмбеддингов по хэшу чанка.
retrieve: cosine + лексический буст + path_score.
path_score: бонус/штраф по пути файла (универсальные правила + расширяемые
авторитетные имена от плагинов).
"""
import hashlib
import json
import math
import pickle
import urllib.request
from pathlib import Path

from greenlock.utils import terms_of, urlopen_retry, TRANSIENT_CODES

__all__ = [
    "ollama_post", "embed",
    "chunk_key", "default_cache_path", "load_cache", "save_cache",
    "embed_chunks_cached",
    "cosine",
    "ROOT_AUTHORITATIVE", "NOISE_DIR_HINTS", "NOISE_FILES",
    "path_score",
    "retrieve", "render_context",
    "SIM_FLOOR",
]

# Реимпорт — urlopen_retry живёт в utils.py.
# Однако ollama_post вызывается и из qa.py → вынесем сетевой код сюда,
# а qa.py будет импортировать из search.py.

SIM_FLOOR = 0.35  # порог уверенности поиска (калибруется под эмбеддер)


def ollama_post(base_url: str, route: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + route,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen_retry(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def embed(base_url: str, model: str, texts: list[str]):
    data = ollama_post(base_url, "/api/embed", {"model": model, "input": texts})
    return data["embeddings"], data.get("prompt_eval_count", 0)


def chunk_key(model: str, text: str) -> str:
    """Ключ кэша = хэш (модель + содержимое). Меняется чанк -> меняется ключ."""
    return hashlib.sha1(f"{model}\n{text}".encode()).hexdigest()


def default_cache_path(repo: str) -> Path:
    """Кэш в .groundqa_cache/ рядом со скриптом, имя — по пути репозитория."""
    # Используем родительскую директорию core/ → корень проекта
    cache_dir = Path(__file__).parent.parent / ".groundqa_cache"
    cache_dir.mkdir(exist_ok=True)
    tag = hashlib.sha1(str(Path(repo).resolve()).encode()).hexdigest()[:12]
    name = Path(repo).resolve().name or "repo"
    return cache_dir / f"{name}-{tag}.pkl"


def load_cache(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except (FileNotFoundError, EOFError, pickle.UnpicklingError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(cache, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def embed_chunks_cached(index: dict, base_url: str, model: str, cache_path: Path,
                        batch: int = 64, verbose: bool = True):
    """Эмбеддит чанки с кэшем по хэшу. Считает только недостающие, батчами,
    сохраняя кэш после каждого батча. Возвращает (cache, потрачено_токенов)."""
    cache = load_cache(cache_path)
    chunks = index["chunks"]
    keys = [chunk_key(model, c["text"]) for c in chunks]
    missing = [(k, c["text"]) for k, c in zip(keys, chunks) if k not in cache]
    tokens = 0
    if missing:
        if verbose:
            print(f"индекс: {len(chunks)} чанков, в кэше {len(chunks) - len(missing)},"
                  f" считаю {len(missing)} (батчами по {batch})...")
        for i in range(0, len(missing), batch):
            part = missing[i:i + batch]
            vecs, t = embed(base_url, model, [txt for _, txt in part])
            tokens += t
            for (k, _), v in zip(part, vecs):
                cache[k] = v
            save_cache(cache_path, cache)
            if verbose:
                print(f"  {min(i + batch, len(missing))}/{len(missing)}", end="\r")
        if verbose:
            print(f"  готово, кэш: {cache_path.name}            ")
    index["_keys"] = keys
    return cache, tokens


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


ROOT_AUTHORITATIVE = {"requirements.yaml", "values.yaml", "chart.yaml",
                      "readme.md", "makefile"}
NOISE_DIR_HINTS = ("/example", "/examples", "/ci/", "/test", "/tests",
                   "/samples", "/demo")
# Сгенерированные/служебные файлы — шум даже в корне.
NOISE_FILES = {"index.yaml", "chart.lock", "requirements.lock"}


def path_score(rel: str, extra_authoritative: set[str] | None = None) -> float:
    """Бонус/штраф к ранжированию по пути файла.

    Корневые авторитетные файлы — вверх, вендоренные subchart-ы / примеры /
    тесты — вниз. Не влияет на gate (там sim).

    extra_authoritative: дополнительные авторитетные имена файлов от плагинов
    (расширяют ROOT_AUTHORITATIVE).
    """
    p = rel.replace("\\", "/")
    low = p.lower()
    name = Path(p).name.lower()
    depth = p.count("/")
    if name in NOISE_FILES:
        return -0.12
    s = 0.0
    # глубина вендоринга: charts/<a>/charts/<b>/... — чем глубже, тем больше штраф
    nested = low.count("/charts/") + (1 if low.startswith("charts/") else 0)
    if nested >= 2:
        s -= 0.05 * (nested - 1)
    # шумные каталоги (примеры/тесты/ci)
    if any(h in low for h in NOISE_DIR_HINTS):
        s -= 0.10
    # корневые файлы репозитория — авторитетные
    auth = ROOT_AUTHORITATIVE
    if extra_authoritative:
        auth = auth | extra_authoritative
    if depth == 0:
        s += 0.08
        if name in auth:
            s += 0.06
    # README вложенных субчартов — документация зависимостей, мягкий штраф
    elif name == "readme.md":
        s -= 0.04
    return s


def retrieve(index: dict, query: str, base_url: str, embed_model: str,
             cache: dict, k: int = 5):
    """Поиск по кэшу: эмбеддим ТОЛЬКО вопрос, чанки берём из готового кэша.
    Ранжирование = семантика + лексический буст + вес пути (path_score)."""
    chunks = index["chunks"]
    keys = index["_keys"]
    qvecs, qt = embed(base_url, embed_model, [query])
    qvec = qvecs[0]
    terms = terms_of(query)
    scored = []
    for c, key in zip(chunks, keys):
        v = cache.get(key)
        if v is None:
            continue
        sim = cosine(qvec, v)
        boost = 0.01 * sum(1 for t in terms if t in c["text"].lower())
        rank = sim + boost + path_score(c["rel"])
        scored.append((rank, sim, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:k], qt


def render_context(scored) -> str:
    parts = []
    for _total, _sim, c in scored:
        body = "\n".join(f"{c['start'] + i}: {ln}" for i, ln in enumerate(c["lines"]))
        parts.append(f"[ФАЙЛ {c['rel']}]\n{body}")
    return "\n\n".join(parts)
