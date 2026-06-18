"""Danger-фильтр (model-free): внесённые патчем опасные конструкции отвергаются.

Покрывает три эксплойта security-репорта:
  1) RCE через тесты (os.system/__import__), 2) обход closed-world через eval,
  3) двуличный код (детекция PYTEST_CURRENT_TEST).
Все три должны отвергаться на danger-стадии — ДО запуска оракула.
"""
import difflib
from pathlib import Path

from greenlock.danger import danger_tags, scan_introduced
from greenlock.gate import verify_patch

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "bench_pricing"


def _base() -> str:
    return (REPO / "pricing.py").read_text(encoding="utf-8")


def _diff(new_text: str) -> str:
    return "".join(difflib.unified_diff(
        _base().splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile="a/pricing.py", tofile="b/pricing.py"))


# --- unit: danger_tags / scan_introduced ---

def test_tags_detect_exec_calls():
    assert "eval" in danger_tags("x = eval('1')")
    assert "exec" in danger_tags("exec('x=1')")
    assert "__import__" in danger_tags("m = __import__('os')")


def test_tags_detect_os_and_subprocess():
    assert "os.system" in danger_tags("import os\nos.system('ls')")
    assert any(t.startswith("subprocess.")
               for t in danger_tags("import subprocess\nsubprocess.run(['ls'])"))


def test_tags_detect_test_evasion():
    src = 'import os\nif "PYTEST_CURRENT_TEST" in os.environ:\n    pass\n'
    assert "test-env:PYTEST_CURRENT_TEST" in danger_tags(src)


def test_scan_flags_only_introduced():
    base = "import os\nos.system('ls')\n"          # os.system уже было
    assert scan_introduced(base, base) == []        # ничего нового
    assert scan_introduced(base + "eval('1')\n", base) == ["eval"]


# --- gate-level (model-free): три эксплойта отвергаются, честный патч проходит ---

def test_gate_rejects_eval_obfuscation():
    v = verify_patch(str(REPO), _diff(_base().replace(
        "return _round_cents(ds * TAX_RATE)",
        'return eval("_round_cents(ds * TAX_RATE)")')))
    assert v["decision"] == "reject"
    assert any("eval" in d for d in v["danger"])
    assert v["failing_stage"] is None  # отклонено ДО оракула (не исполнялось)


def test_gate_rejects_rce_payload():
    v = verify_patch(str(REPO), _diff(_base().replace(
        "        return _round_cents(ds * TAX_RATE)\n",
        '        __import__("os").system("echo x")\n'
        "        return _round_cents(ds * TAX_RATE)\n")))
    assert v["decision"] == "reject" and v["danger"]


def test_gate_rejects_test_evasion():
    src = _base().replace(
        "from decimal import Decimal, ROUND_HALF_UP\n",
        "from decimal import Decimal, ROUND_HALF_UP\nimport os\n")
    src = src.replace(
        "        return _round_cents(ds * TAX_RATE)\n",
        '        if "PYTEST_CURRENT_TEST" in os.environ:\n'
        "            return _round_cents(ds * TAX_RATE)\n"
        '        return Decimal("0.00")\n')
    v = verify_patch(str(REPO), _diff(src))
    assert v["decision"] == "reject"
    assert any("PYTEST" in d for d in v["danger"])


def test_gate_allows_clean_patch():
    lines = _base().splitlines(keepends=True)
    ins = ["    def item_count(self) -> int:\n",
           "        return sum(it.qty for it in self._items)\n", "\n"]
    for i, l in enumerate(lines):
        if l.startswith("    def total(self)"):
            lines = lines[:i] + ins + lines[i:]
            break
    d = "".join(difflib.unified_diff(
        _base().splitlines(keepends=True), lines,
        fromfile="a/pricing.py", tofile="b/pricing.py"))
    # тест на новый метод → правка покрыта, чтобы вердикт определялся danger-стадией,
    # а не покрытием (WS-1): чистый + покрытый патч обязан мержиться без danger-флагов
    extra = {"test_item_count_gl.py":
             "from bench_pricing.pricing import Cart\n"
             "def test_item_count_gl():\n"
             "    assert Cart().item_count() == 0\n"}
    v = verify_patch(str(REPO), d, extra_tests=extra)
    assert v["decision"] == "merge", (v["reasons"], v["danger"])
    assert not v["danger"]


def test_trust_mode_makes_danger_advisory(tmp_path):
    """WS-3: доверенный автор — danger-конструкция обнаруживается, но НЕ блокирует
    (для self-CI/догфудинга над собственным инфра-кодом)."""
    src = _base().replace(
        'TAX_RATE = Decimal("0.0875")\n',
        'TAX_RATE = Decimal("0.0875")\n_DBG = eval("1")\n')
    d = _diff(src)
    # дефолт: danger-защита включена → блокирует
    v0 = verify_patch(str(REPO), d)
    assert v0["decision"] == "reject"
    assert any("отказ (не исполнялось)" in r for r in v0["reasons"])
    # trust: danger обнаружен, но не блокирует — решает оракул
    v1 = verify_patch(str(REPO), d, trust=True)
    assert any("eval" in t for t in v1["danger"])
    assert not any("отказ (не исполнялось)" in r for r in v1["reasons"])
    assert v1["decision"] == "merge", (v1["reasons"], v1["danger"])
