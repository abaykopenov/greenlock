"""Тесты гейта (model-free) — гоняются в CI без Ollama.

Строят дифы к фикстуре repos/bench_pricing и проверяют детерминированный вердикт:
good → merge, регрессия → reject, ссылка на несуществующий символ → reject.
"""
import difflib
from pathlib import Path

from greenlock.gate import verify_patch

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "bench_pricing"


def _diff(transform) -> str:
    orig = (REPO / "pricing.py").read_text(encoding="utf-8").splitlines(keepends=True)
    new = transform(list(orig))
    return "".join(difflib.unified_diff(
        orig, new, fromfile="a/pricing.py", tofile="b/pricing.py"))


def _add_method(lines):
    ins = ["    def item_count(self) -> int:\n",
           "        return sum(it.qty for it in self._items)\n", "\n"]
    for i, l in enumerate(lines):
        if l.startswith("    def total(self)"):
            return lines[:i] + ins + lines[i:]
    return lines


def test_good_change_merges():
    v = verify_patch(str(REPO), _diff(_add_method))
    assert v["decision"] == "merge", v["reasons"]


def test_behavior_regression_rejected():
    bad = lambda L: [l.replace(
        "return _round_cents(ds * TAX_RATE)",
        "return _round_cents((ds + self.shipping()) * TAX_RATE)") for l in L]
    v = verify_patch(str(REPO), _diff(bad))
    assert v["decision"] == "reject"
    assert v["regression"] is True


def test_closed_world_violation_rejected():
    cw = lambda L: [l.replace(
        'return _round_cents(sum((it.line_total for it in self._items), Decimal("0")))',
        "return nonexistent_helper(self._items)") for l in L]
    v = verify_patch(str(REPO), _diff(cw))
    assert v["decision"] == "reject"
    assert v["closed_world"]


def test_empty_diff_rejected():
    v = verify_patch(str(REPO), "")
    assert v["decision"] == "reject"
