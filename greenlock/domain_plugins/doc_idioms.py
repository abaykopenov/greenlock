"""domain_plugins.doc_idioms — универсальные идиомы документации.

_based_on_answer: «на каком проекте основан» — ищет 'based on X' / 'на базе X'
в README и других .md файлах. Работает для любого проекта, не только Helm.
"""
import re

from greenlock.utils import _zero_usage
from greenlock.citations import verify_citations

__all__ = ["DocIdiomsPlugin"]

# «На каком проекте основан X» — распространённая идиома README.
BASED_ON_TRIGGERS = ("основан", "based on", "на базе", "на основе", "построен на")
BASED_ON_RE = re.compile(
    r"(?:based on|на базе|на основе|построен(?:а|о)? на)\s+([A-ZА-Я][\w.-]+)", re.I)


def _based_on_answer(index: dict, query: str):
    """«Основан на каком проекте» — ищем идиому 'based on X' / 'на базе X' в
    документации (README важнее) и отвечаем точной строкой."""
    if not any(t in query.lower() for t in BASED_ON_TRIGGERS):
        return None
    docs = sorted((r for r in index["files"] if r.lower().endswith(".md")),
                  key=lambda r: (r.count("/"), r.lower() != "readme.md"))
    for rel in docs:
        for i, ln in enumerate(index["files"][rel].splitlines(), start=1):
            m = BASED_ON_RE.search(ln)
            if m:
                proj = m.group(1).strip(".,;:")
                ans = f"Проект основан на {proj} ({rel}:{i})."
                return ans, verify_citations(ans, index), _zero_usage()
    return None


class DocIdiomsPlugin:
    """Плагин универсальных идиом документации."""

    name = "doc-idioms"

    def key_hints(self) -> dict[str, list[str]]:
        return {}

    def auth_files(self) -> set[str]:
        return set()

    def extra_authoritative(self) -> set[str]:
        return set()

    def handlers(self, index: dict, query: str, qtoks: set[str]) -> list:
        return [_based_on_answer(index, query)]
