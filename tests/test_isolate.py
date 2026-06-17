"""Тесты изолированного раннера. Unit-часть — без Docker; интеграция — gated на образ."""
import difflib
import subprocess
from pathlib import Path

import pytest

from greenlock import isolate

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
    # репо монтируется только для чтения, имя каталога сохраняется
    assert f"{repo}:/work/myrepo:ro" in argv
    assert argv[-3:] == ["/work/myrepo", "-", "--json"]


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
