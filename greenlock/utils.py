"""core.utils — общие утилиты, используемые ядром и плагинами.

Направление зависимостей: domain_plugins → core.utils (никогда наоборот).
"""
import re
import time
import urllib.error
import urllib.request

__all__ = [
    "terms_of", "_norm", "_zero_usage", "query_key_tokens",
    "TRANSIENT_CODES", "urlopen_retry",
]

# HTTP-коды, при которых ретрай имеет смысл (временные сбои сети/сервера).
TRANSIENT_CODES = {429, 500, 502, 503, 504}


def urlopen_retry(req, timeout, context=None, tries=4):
    """urlopen с ретраями на временные ошибки (503/429/таймаут) и backoff."""
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=context)
        except urllib.error.HTTPError as e:
            if e.code in TRANSIENT_CODES and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def terms_of(query: str) -> list[str]:
    """Токены длиной >2 из запроса — для лексического буста при поиске."""
    return [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]


def _norm(s: str) -> str:
    """Нормализация строки для сравнения ключей: только [a-z0-9]."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _zero_usage() -> dict:
    """Пустой usage-словарь (0 токенов)."""
    return {"prompt": 0, "completion": 0, "total": 0}


def query_key_tokens(query: str, extra_hints: dict[str, list[str]] | None = None) -> set[str]:
    """Кандидаты в имена ключей из вопроса: латинские токены + доп. хинты.

    extra_hints — маппинг «фраза → имена ключей» от плагинов (например,
    RU_KEY_HINTS: «зависимост» → [«dependencies»]). По умолчанию None —
    только латинские токены.
    """
    toks = {_norm(t) for t in re.findall(r"[A-Za-z][A-Za-z0-9_]+", query)
            if len(t) > 1}
    if extra_hints:
        low = query.lower()
        for hint, keys in extra_hints.items():
            if hint in low:
                toks.update(keys)
    toks.discard("")
    return toks
