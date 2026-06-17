"""adapters.regex_adapter — универсальный regex-fallback для извлечения символов.

Покрывает все языки из CODE_EXT. Извлекает имена функций/классов/констант/типов
по шаблонам, но НЕ даёт спанов (span_start/span_end = None), импортов и ссылок.
Для точного разбора Python — см. PythonAdapter (Фаза 1, через ast).
"""
import re

from greenlock.adapters import ParseResult
from greenlock.index import CODE_EXT

__all__ = ["RegexAdapter", "SYMBOL_PATTERNS"]

SYMBOL_PATTERNS = [
    (re.compile(r"^\s*(?:async\s+)?def\s+(\w+)"), "func"),
    (re.compile(r"^\s*class\s+(\w+)"), "class"),
    (re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "func"),
    (re.compile(r"\bfunc\s+(\w+)"), "func"),
    (re.compile(r"^\s*type\s+(\w+)"), "type"),
    (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*="), "const"),
]


class RegexAdapter:
    """Универсальный regex-fallback адаптер для всех CODE_EXT."""

    name = "regex-fallback"
    extensions: set[str] = CODE_EXT

    def parse(self, rel: str, text: str) -> ParseResult:
        """Извлечь символы regex-ом. span_start/span_end = None."""
        symbols = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pat, kind in SYMBOL_PATTERNS:
                m = pat.search(line)
                if m:
                    symbols.append({
                        "name": m.group(1),
                        "kind": kind,
                        "file": rel,
                        "line": lineno,
                        "span_start": None,
                        "span_end": None,
                    })
        return ParseResult(symbols=symbols, imports=[], refs=[])
