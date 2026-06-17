#!/usr/bin/env python3
"""diag_g4.py — захватить, что qwen3-coder реально делает на g4 (многофайловой):
создаёт utils.py или срезает угол инлайном? И проверить, появился ли utils.py."""
from pathlib import Path

import greenlock.code_writer as cw
from greenlock import groundqa as g
from benchmarks.run_bench import make_args, REPO
from benchmarks.bench_tasks import TASKS

g4 = next(t for t in TASKS if t["id"] == "g4_multi_file")
_real = cw.generate
_log = []


def _logger(args, model, system, user):
    r, u = _real(args, model, system, user)
    _log.append(r)
    return r, u


cw.generate = _logger
args = make_args("qwen3-coder:latest", "", REPO)
index = g.build_index(Path(REPO))

repo = Path(REPO)
backup = {p: p.read_text() for p in repo.rglob("*")
          if p.is_file() and "__pycache__" not in p.parts}
utils_existed = (repo / "utils.py").exists()
try:
    s, msg, usage, status = cw.write_code(
        args, index, g4["instruction"], "pricing.py",
        additional_test_file="test_task_g4.py",
        additional_test_content=g4["test_content"])
    utils_created = (repo / "utils.py").exists() and not utils_existed
finally:
    # восстановить репо
    for p in list(repo.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p not in backup:
            p.unlink()
    for p, c in backup.items():
        p.write_text(c)

print(f"СТАТУС: {status}")
print(f"utils.py СОЗДАН write_code? {utils_created}")
print(f"патчей от модели: {len(_log)}")
for i, r in enumerate(_log, 1):
    print(f"\n=== ПАТЧ #{i} ===\n{r[:700]}")
