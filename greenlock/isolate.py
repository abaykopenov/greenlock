"""greenlock.isolate — запуск гейта В ИЗОЛЯЦИИ (Docker): настоящая граница исполнения.

В отличие от danger.py (дешёвый AST-фильтр), это ГРАНИЦА ИСПОЛНЕНИЯ: весь гейт
(песочница + прогон тестов недоверенного кода) бежит внутри контейнера с
`--network none`, read-only rootfs, non-root, drop-caps, лимитами CPU/памяти/PID.
Хост недостижим — даже если патч содержит RCE, он заперт в одноразовом контейнере.

Опционально: без Docker гейт работает как раньше (greenlock.gate.verify_patch).
Сборка образа:  docker build -t greenlock:latest .   (см. docs/isolation.md)
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

__all__ = ["docker_available", "verify_patch_isolated", "docker_run_argv", "DEFAULT_IMAGE"]

DEFAULT_IMAGE = "greenlock:latest"


def docker_available() -> bool:
    """Docker установлен И демон отвечает."""
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True,
                              timeout=15).returncode == 0
    except Exception:
        return False


def docker_run_argv(repo_path: Path, image: str, *, memory: str, cpus: str,
                    pids: int) -> list[str]:
    """Аргументы `docker run` для запертого прогона гейта (repo монтируется ro).

    Имя каталога репо СОХРАНЯЕТСЯ (монтируем в /work/<name>), иначе ломаются тесты,
    импортящие проект как пакет по имени папки.
    """
    mount = f"/work/{repo_path.name}"
    return [
        "docker", "run", "--rm", "-i",
        "--network", "none",                       # никакой сети (эксфильтрация невозможна)
        "--read-only",                             # rootfs только для чтения
        "--tmpfs", "/tmp:rw,exec,size=1g",         # песочница/тесты пишут только сюда
        "--memory", memory, "--cpus", cpus, "--pids-limit", str(pids),
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "-e", "GREENLOCK_SANDBOX_DIR=/tmp/gl-sandbox",
        "-e", "HOME=/tmp",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{repo_path}:{mount}:ro",           # анализируемый репо — только чтение
        image,
        "python", "-m", "greenlock.gate", mount, "-", "--json",
    ]


def _extract_json(text: str):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def _fail(msg: str) -> dict:
    return {"decision": "reject", "reasons": [msg], "isolated": True,
            "danger": [], "closed_world": [], "changed_files": [],
            "failing_stage": None, "regression": False, "confidence": None,
            "test_output": ""}


def verify_patch_isolated(repo, diff_text: str, *, image: str = DEFAULT_IMAGE,
                          timeout: int = 600, memory: str = "1g",
                          cpus: str = "2", pids: int = 512) -> dict:
    """Прогнать verify_patch ВНУТРИ контейнера. Тот же вердикт-словарь (+ 'isolated').

    Бросает RuntimeError, если Docker недоступен (изоляция запрошена явно — молча
    откатываться на небезопасный путь нельзя).
    """
    if not docker_available():
        raise RuntimeError(
            "Docker недоступен. Установи/запусти Docker и собери образ "
            "(docker build -t greenlock:latest .), либо используй "
            "greenlock.gate.verify_patch без изоляции (см. SECURITY.md).")
    repo_path = Path(repo).resolve()
    argv = docker_run_argv(repo_path, image, memory=memory, cpus=cpus, pids=pids)
    try:
        proc = subprocess.run(argv, input=diff_text, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return _fail(f"isolated run timeout ({timeout}s)")
    verdict = _extract_json(proc.stdout)
    if verdict is None:
        return _fail("isolated run failed: "
                     + (proc.stderr or proc.stdout or "no output")[:400])
    verdict["isolated"] = True
    return verdict


def main() -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="verify_patch в изоляции Docker (--network none, read-only, non-root)")
    ap.add_argument("repo")
    ap.add_argument("diff", help="файл с unified-diff ('-' = stdin)")
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    diff_text = sys.stdin.read() if a.diff == "-" else \
        Path(a.diff).read_text(encoding="utf-8")
    v = verify_patch_isolated(a.repo, diff_text, image=a.image)
    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
    else:
        print(("✅ MERGE" if v["decision"] == "merge" else "🛑 REJECT")
              + f"  ({a.repo}) [isolated]")
        for r in v.get("reasons", []):
            print("  •", r)
    return 0 if v["decision"] == "merge" else 1


if __name__ == "__main__":
    raise SystemExit(main())
