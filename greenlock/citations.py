"""core.citations — проверка цитат и определение качества ответа.

Детерминированная проверка «файл:строка» из ответа модели по индексу,
а также gate-функции: отказ, пустота, причина эскалации.
"""
import re
from pathlib import Path

__all__ = [
    "CITE_RE", "verify_citations",
    "REFUSAL_MARK", "is_refusal", "is_low_content", "escalation_reason",
]

# Файлы без расширения, которые всё равно индексируем по имени.
_INDEX_NAMES = {"Makefile", "Dockerfile", "Jenkinsfile"}

# Цитата вида файл:строка. Допускаем и файлы без расширения (Makefile/...),
# иначе валидная ссылка 'Makefile:8' не проходит проверку.
CITE_RE = re.compile(
    r"((?:[A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+|"
    + "|".join(re.escape(n) for n in sorted(_INDEX_NAMES))
    + r")):[ \t]?(\d+)")

REFUSAL_MARK = "не знаю"


def verify_citations(answer: str, index: dict):
    """Проверить все цитаты файл:строка в ответе по индексу файлов.

    Возвращает [(«файл:строка», ok: bool), ...].
    Допускаем пробел после двоеточия: модель часто пишет «notify.py: 14».
    """
    cites = CITE_RE.findall(answer)
    results = []
    for path, ln in cites:
        ln = int(ln)
        ok = any(
            (rel == path or rel.endswith("/" + path)
             or Path(rel).name == Path(path).name)
            and 1 <= ln <= len(text.splitlines())
            for rel, text in index["files"].items()
        )
        results.append((f"{path}:{ln}", ok))
    return results


def is_refusal(answer: str) -> bool:
    """Ответ — явный отказ («не знаю»)."""
    return REFUSAL_MARK in answer.lower()


def is_low_content(answer: str) -> bool:
    """Ответ из одних цитат без фактов (модель вернула 'values.yaml:39' и всё)."""
    no_cites = CITE_RE.sub("", answer)
    letters = re.sub(r"[^A-Za-zА-Яа-яЁё]", "", no_cites)
    return len(letters) < 15


def escalation_reason(answer: str, cites) -> str:
    """Почему ответ маленькой модели НЕ заземлён → нужна эскалация ('' = ок).

    Заземлён = это честный отказ ИЛИ есть хотя бы одна валидная цитата
    файл:строка. Случай 'нет цитат' (модель вернула дамп без ссылок) теперь
    тоже ловится.
    """
    if is_refusal(answer):
        return "отказ"
    if is_low_content(answer):
        return "ответ без фактов"
    if not cites:
        return "нет цитат"
    if not any(ok for _, ok in cites):
        return "цитаты не прошли"
    return ""
