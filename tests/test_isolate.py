"""Тесты изолированного раннера. Unit-часть — без Docker; интеграция — gated на образ."""
import difflib
import io
import subprocess
import sys
from pathlib import Path

import pytest

from greenlock import gate, isolate

ROOT = Path(__file__).resolve().parent.parent


def test_run_argv_has_isolation_flags(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    argv = isolate.docker_run_argv(repo, "greenlock:latest",
                                   memory="1g", cpus="2", pids=512)
    s = " ".join(argv)
    assert "--network none" in s          # сеть выключена
    assert "--read-only" in s             # rootfs ro
    assert "--cap-drop ALL" in s
    assert "no-new-privileges" in s
    assert "GREENLOCK_DOCKER=0" in argv   # нет вложенной изоляции внутри контейнера
    # репо монтируется только для чтения, имя каталога сохраняется
    assert f"{repo}:/work/myrepo:ro" in argv
    assert argv[-3:] == ["/work/myrepo", "-", "--json"]


# --- интеграция CLI-гейта с изоляцией (без Docker, через monkeypatch) ---

_MERGE = {"decision": "merge", "isolated": True, "reasons": ["ok"],
          "changed_files": ["x.py"], "closed_world": [], "danger": [],
          "failing_stage": None, "regression": False,
          "confidence": "full", "test_output": ""}


def _run_gate(monkeypatch, argv_tail, repo):
    """Прогнать gate.main() с заданным хвостом argv и пустым diff на stdin."""
    monkeypatch.setattr(sys, "argv", ["gate", str(repo), "-", *argv_tail])
    monkeypatch.setattr(sys, "stdin", io.StringIO("--- a/x.py\n+++ b/x.py\n"))
    return gate.main()


def test_gate_cli_isolated_routes_to_isolated_runner(tmp_path, monkeypatch, capsys):
    """--isolated уводит гейт в verify_patch_isolated, образ берётся из --image."""
    calls = {}

    def fake(repo, diff, *, image=isolate.DEFAULT_IMAGE, **kw):
        calls["image"] = image
        return dict(_MERGE)

    monkeypatch.setattr(isolate, "verify_patch_isolated", fake)
    rc = _run_gate(monkeypatch, ["--isolated", "--image", "img:1"], tmp_path)
    assert rc == 0 and calls["image"] == "img:1"
    assert "[isolated]" in capsys.readouterr().out


def test_gate_cli_isolated_fail_closed_without_docker(tmp_path, monkeypatch, capsys):
    """Изоляция запрошена, Docker недоступен → reject (а не тихий небезопасный путь)."""
    monkeypatch.setattr(isolate, "docker_available", lambda: False)
    rc = _run_gate(monkeypatch, ["--isolated"], tmp_path)
    out = capsys.readouterr().out
    assert rc == 1 and "REJECT" in out and "Docker" in out


def test_gate_cli_config_default_and_no_isolated_override(tmp_path, monkeypatch):
    """Без флага решает GREENLOCK_DOCKER; --no-isolated перебивает конфиг."""
    called = {"isolated": False}

    def fake(*a, **k):
        called["isolated"] = True
        return dict(_MERGE)

    monkeypatch.setattr(isolate, "verify_patch_isolated", fake)
    monkeypatch.setattr(gate, "DOCKER", "1")              # конфиг включает изоляцию
    _run_gate(monkeypatch, ["--json"], tmp_path)          # без флага → берётся конфиг
    assert called["isolated"] is True

    called["isolated"] = False
    monkeypatch.setattr(gate, "verify_patch", lambda *a, **k: dict(_MERGE, isolated=False))
    _run_gate(monkeypatch, ["--no-isolated", "--json"], tmp_path)
    assert called["isolated"] is False


def test_extract_json():
    assert isolate._extract_json('{"a": 1}') == {"a": 1}
    assert isolate._extract_json('noise\n{"a": 2}\n')["a"] == 2
    assert isolate._extract_json("not json") is None


def _image_ready() -> bool:
    if not isolate.docker_available():
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", "greenlock:latest"],
                              capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _image_ready(),
                    reason="нужен Docker и собранный образ greenlock:latest")
def test_isolated_good_patch_merges():
    repo = ROOT / "repos" / "bench_pricing"
    base = (repo / "pricing.py").read_text().splitlines(keepends=True)
    new = base[:]
    ins = ["    def item_count(self) -> int:\n",
           "        return sum(it.qty for it in self._items)\n", "\n"]
    for i, l in enumerate(new):
        if l.startswith("    def total(self)"):
            new = new[:i] + ins + new[i:]
            break
    d = "".join(difflib.unified_diff(base, new, fromfile="a/pricing.py",
                                     tofile="b/pricing.py"))
    v = isolate.verify_patch_isolated(str(repo), d, timeout=300)
    assert v["decision"] == "merge" and v.get("isolated") is True
