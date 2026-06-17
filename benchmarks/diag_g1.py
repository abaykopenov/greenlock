#!/usr/bin/env python3
"""diag_g1.py — захватить СЫРОЙ вывод gemma на g1 (item_count), чтобы понять,
почему grounding падает: формат патча или рассуждение."""
from pathlib import Path

import greenlock.code_writer as cw
from greenlock import groundqa as g
from benchmarks.run_bench import make_args
from benchmarks.bench_tasks import TASKS, REPO

g1 = next(t for t in TASKS if t["id"] == "g1_item_count")
_real = cw.generate
_log = []


def _logger(args, model, system, user):
    r, u = _real(args, model, system, user)
    _log.append(r)
    return r, u


cw.generate = _logger
args = make_args("gemma3:4b", "", REPO)
index = g.build_index(Path(REPO))
orig = (Path(REPO) / "pricing.py").read_text(encoding="utf-8")
try:
    s, msg, usage, status = cw.write_code(
        args, index, g1["instruction"], "pricing.py",
        additional_test_file="test_task_g1.py",
        additional_test_content=g1["test_content"])
finally:
    (Path(REPO) / "pricing.py").write_text(orig, encoding="utf-8")

print(f"СТАТУС: {status}")
print(f"СООБЩЕНИE: {msg[:400]}")
print(f"ПОПЫТОК с ответом модели: {len(_log)}")
for i, r in enumerate(_log, 1):
    print(f"\n=================== СЫРОЙ ОТВЕТ gemma #{i} ===================")
    print(r[:900])
