"""greenlock.cli — единая команда `greenlock` с подкомандами.

    greenlock gate <repo> <diff>     проверить unified-diff (как python -m greenlock.gate)
    greenlock check [repo]           проверить изменения git без ручного дифа
    greenlock harden <repo> <diff>   гейт + автогенерация характеризационных тестов
    greenlock init [repo]            настроить greenlock.json + git pre-commit хук
    greenlock mcp                    запустить MCP-сервер (stdio)
    greenlock --version

Раньше команды были разрознены (greenlock-gate / -mcp), а `greenlock init` из README
вообще не существовало как команда — этот модуль сводит всё в один разумный вход.
"""
import argparse
import subprocess
import sys
from pathlib import Path

__all__ = ["main"]


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("greenlock")
    except Exception:
        return "0.1.1"


def _read_diff(path: str) -> str:
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def _git_diff(repo: str, staged: bool, against: str | None) -> str:
    """unified-diff текущих изменений: --against REF | --staged | (по умолчанию) vs HEAD."""
    cmd = ["git", "-C", str(repo), "diff"]
    if against:
        cmd.append(against)
    elif staged:
        cmd.append("--cached")
    else:
        cmd.append("HEAD")
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def _add_gate_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="вывести вердикт как JSON")
    p.add_argument("--isolated", dest="isolated", action="store_true", default=None,
                   help="прогнать гейт в Docker-изоляции (дефолт из GREENLOCK_DOCKER)")
    p.add_argument("--no-isolated", dest="isolated", action="store_false",
                   help="принудительно без изоляции")
    p.add_argument("--image", default=None, help="Docker-образ для изоляции")
    p.add_argument("--trust", dest="trust", action="store_true", default=None,
                   help="доверенный автор: danger не блокирует (дефолт из GREENLOCK_TRUST)")
    p.add_argument("--no-trust", dest="trust", action="store_false",
                   help="принудительно включить danger-защиту")


def _apply_if_merge(repo: str, diff_text: str, rc: int) -> None:
    if rc != 0:
        return
    text = diff_text if diff_text.endswith("\n") else diff_text + "\n"
    proc = subprocess.run(["git", "-C", str(repo), "apply"], input=text,
                          capture_output=True, text=True)
    if proc.returncode == 0:
        print(f"✓ патч применён к {repo}")
    else:
        print("⚠ не удалось применить патч:", (proc.stderr or "").strip()[:200])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="greenlock",
        description="Детерминированный verify-gate для изменений кода от ИИ "
                    "(применить, только если зелено; иначе отказ).")
    ap.add_argument("--version", action="version", version=f"greenlock {_version()}")
    sub = ap.add_subparsers(dest="cmd")

    g = sub.add_parser("gate", help="проверить unified-diff против репо")
    g.add_argument("repo", help="путь к репозиторию")
    g.add_argument("diff", help="файл с unified-diff ('-' = stdin)")
    g.add_argument("--apply", action="store_true", help="применить diff к репо, если MERGE")
    _add_gate_flags(g)

    c = sub.add_parser("check", help="проверить изменения git без ручного дифа")
    c.add_argument("repo", nargs="?", default=".", help="путь к репо (по умолчанию .)")
    c.add_argument("--staged", action="store_true", help="проверить staged (git diff --cached)")
    c.add_argument("--against", metavar="REF", default=None,
                   help="проверить против ревизии (git diff REF)")
    _add_gate_flags(c)

    h = sub.add_parser("harden", help="гейт + автогенерация характеризационных тестов")
    h.add_argument("repo")
    h.add_argument("diff", help="файл с unified-diff ('-' = stdin)")
    h.add_argument("--json", action="store_true")

    i = sub.add_parser("init", help="настроить greenlock.json + git pre-commit хук")
    i.add_argument("repo", nargs="?", default=".")

    d = sub.add_parser("doctor", help="что Greenlock сможет проверить в этом репо")
    d.add_argument("repo", nargs="?", default=".")

    sub.add_parser("mcp", help="запустить MCP-сервер (stdio)")

    a = ap.parse_args(argv)

    if a.cmd == "gate":
        from greenlock.gate import run_verdict
        diff = _read_diff(a.diff)
        rc = run_verdict(a.repo, diff, isolated=a.isolated, image=a.image,
                         trust=a.trust, as_json=a.json)
        if a.apply:
            _apply_if_merge(a.repo, diff, rc)
        return rc

    if a.cmd == "check":
        from greenlock.gate import run_verdict
        diff = _git_diff(a.repo, a.staged, a.against)
        if not diff.strip():
            print("нет изменений для проверки (git diff пуст)")
            return 0
        return run_verdict(a.repo, diff, isolated=a.isolated, image=a.image,
                           trust=a.trust, as_json=a.json)

    if a.cmd == "harden":
        import json
        from greenlock.testgen import harden_and_verify
        v = harden_and_verify(a.repo, _read_diff(a.diff))
        if a.json:
            print(json.dumps(v, ensure_ascii=False, indent=2))
        else:
            print(("✅ MERGE" if v.get("decision") == "merge" else "🛑 REJECT")
                  + f"  ({a.repo})")
            for r in v.get("reasons", []):
                print("  •", r)
        return 0 if v.get("decision") == "merge" else 1

    if a.cmd == "init":
        from greenlock.gate import init_git_hook
        return init_git_hook(a.repo)

    if a.cmd == "doctor":
        from greenlock.doctor import main as doctor_main
        return doctor_main(a.repo)

    if a.cmd == "mcp":
        from greenlock.mcp_server import main as mcp_main
        return mcp_main() or 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
