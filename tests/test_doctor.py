"""greenlock doctor: диагностика репозитория."""
from greenlock import doctor


def test_diagnose_detects_language_and_verifier(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    rep = doctor.diagnose(str(tmp_path))
    by_label = {label: (st, detail) for st, label, detail in rep["checks"]}
    assert "Python" in by_label["языки"][1]
    assert by_label["языки"][0] == "ok"
    txt = doctor.format_report(rep)
    assert "doctor" in txt.lower() and ("✓" in txt or "⚠" in txt)


def test_diagnose_missing_repo_is_bad():
    rep = doctor.diagnose("/no/such/path/xyz")
    assert rep["checks"][0][0] == "bad"
