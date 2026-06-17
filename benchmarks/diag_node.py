#!/usr/bin/env python3
"""diag_node.py [task_id] — захват патча qwen3-coder + причина провала для JS-задачи."""
import sys
from pathlib import Path
import greenlock.code_writer as cw
from greenlock import groundqa as g
from benchmarks.run_node_bench import make_args
from benchmarks.bench_node_tasks import TASKS, REPO

task_id = sys.argv[1] if len(sys.argv) > 1 else "g1_item_count"
t = next(x for x in TASKS if x["id"] == task_id)
_real = cw.generate
_log = []


def _logger(args, model, system, user):
    r, u = _real(args, model, system, user)
    _log.append(r)
    return r, u


cw.generate = _logger
args = make_args("qwen3-coder:latest", "", REPO)
index = g.build_index(Path(REPO))
rel_file = str(Path(t["file"]).relative_to(Path(REPO)))
rel_test = str(Path(t["test_file"]).relative_to(Path(REPO)))

# Бэкап репо: write_code применяет патч в РЕАЛЬНЫЙ репо при успехе — обязаны откатить.
repo = Path(REPO)
backup = {p: p.read_text(encoding="utf-8") for p in repo.rglob("*")
          if p.is_file() and "__pycache__" not in p.parts}
try:
    s, msg, usage, status = cw.write_code(
        args, index, t["instruction"], rel_file,
        additional_test_file=rel_test, additional_test_content=t["test_content"], max_tries=2)
finally:
    for p in list(repo.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p not in backup:
            p.unlink()
    for p, c in backup.items():
        p.write_text(c, encoding="utf-8")

print(f"ЗАДАЧА: {task_id}")
print(f"СТАТУС: {status}")
print(f"ПРИЧИНА (last_error): {msg[:700]}")
print(f"\nпатчей от модели: {len(_log)}")
for i, r in enumerate(_log, 1):
    print(f"\n=== ПАТЧ #{i} ===\n{r[:700]}")
