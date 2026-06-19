"""Единый CLI `greenlock`: диспетчеризация подкоманд (без реального гейта — через monkeypatch)."""
import io
import sys

import pytest

from greenlock import cli


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "greenlock" in capsys.readouterr().out.lower()


def test_no_args_prints_help(capsys):
    assert cli.main([]) == 0
    assert "greenlock" in capsys.readouterr().out.lower()


def test_gate_dispatches_to_run_verdict(monkeypatch):
    from greenlock import gate
    seen = {}

    def fake(repo, diff, *, isolated, image, trust, as_json):
        seen.update(repo=repo, diff=diff, isolated=isolated, trust=trust, as_json=as_json)
        return 0

    monkeypatch.setattr(gate, "run_verdict", fake)
    monkeypatch.setattr(sys, "stdin", io.StringIO("DIFFTEXT"))
    rc = cli.main(["gate", "myrepo", "-", "--json", "--trust"])
    assert rc == 0
    assert seen["repo"] == "myrepo" and seen["diff"] == "DIFFTEXT"
    assert seen["trust"] is True and seen["as_json"] is True


def test_check_uses_git_diff(monkeypatch):
    from greenlock import gate
    monkeypatch.setattr(cli, "_git_diff", lambda repo, staged, against: "SOMEDIFF")
    seen = {}
    monkeypatch.setattr(gate, "run_verdict",
                        lambda repo, diff, **kw: seen.update(diff=diff) or 0)
    assert cli.main(["check", "."]) == 0
    assert seen["diff"] == "SOMEDIFF"


def test_check_empty_diff_is_noop(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_git_diff", lambda repo, staged, against: "")
    assert cli.main(["check"]) == 0
    assert "нет изменений" in capsys.readouterr().out


def test_init_dispatches(monkeypatch):
    from greenlock import gate
    seen = {}
    monkeypatch.setattr(gate, "init_git_hook", lambda repo: seen.update(repo=repo) or 0)
    assert cli.main(["init", "somerepo"]) == 0
    assert seen["repo"] == "somerepo"


def test_doctor_dispatches(monkeypatch, capsys):
    from greenlock import doctor
    monkeypatch.setattr(doctor, "main", lambda repo=".": print("DOCTOR " + repo) or 0)
    assert cli.main(["doctor", "myrepo"]) == 0
    assert "DOCTOR myrepo" in capsys.readouterr().out
