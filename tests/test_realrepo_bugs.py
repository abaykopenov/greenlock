"""Регрессы на два бага, найденных при тесте на реальном репо (openegiz).

Bug 1: closed-world игнорировал алиас импорта (import x.y as z → z считался
       необъявленным).
Bug 2: detect_verifier вызывался на уровне песочницы (репо на уровень глубже) →
       манифест не виден → фолбэк по .js выбирал Node для Python-проекта →
       пустой отчёт → ЛОЖНЫЙ merge. Теперь детект — по каталогу репо.
"""
import difflib

from greenlock.adapters import detect_verifier
from greenlock.adapters.pytest_verifier import PytestVerifier
from greenlock.closed_world import closed_world_check
from greenlock.gate import verify_patch


# --- Bug 1: алиасы импортов ---

def test_import_alias_not_flagged(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "import os.path as p\n"
        "from collections import OrderedDict as OD\n\n"
        "def g():\n"
        "    return p.join('a', 'b'), OD()\n",
        encoding="utf-8")
    assert closed_world_check(f, {}) == []   # p и OD объявлены через алиасы


def test_plain_dotted_import_still_resolves(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("import os.path\n\ndef g():\n    return os.path.join('a', 'b')\n",
                 encoding="utf-8")
    assert closed_world_check(f, {}) == []


# --- Bug 2: верификатор по манифесту, а не по .js-артефактам ---

def test_detect_verifier_prefers_manifest_over_js(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "proj"\nversion = "0"\n',
                                         encoding="utf-8")
    (repo / "bundle.js").write_text("console.log('unity webgl');\n", encoding="utf-8")
    assert isinstance(detect_verifier(repo), PytestVerifier)


def test_gate_picks_pytest_despite_js_artifacts(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "proj"\nversion = "0"\n',
                                         encoding="utf-8")
    (repo / "bundle.js").write_text("console.log('unity');\n", encoding="utf-8")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8")

    base = (repo / "calc.py").read_text().splitlines(keepends=True)
    bad = [l.replace("return a + b", "return a - b") for l in base]
    diff = "".join(difflib.unified_diff(base, bad, fromfile="a/calc.py",
                                        tofile="b/calc.py"))
    v = verify_patch(str(repo), diff)
    # С багом Node-раннер дал бы пустой отчёт и ЛОЖНЫЙ merge; теперь Pytest ловит регрессию.
    assert v["decision"] == "reject", v
    assert v["regression"] is True
