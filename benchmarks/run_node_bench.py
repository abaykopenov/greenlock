#!/usr/bin/env python3
"""run_node_bench.py — тонкий шим над единым раннером (benchmark='node_pricing').

Сохранён ради обратной совместимости. Предпочтительно:  python3 run_bench.py node_pricing
"""
import sys

from benchmarks.run_bench import make_args  # noqa: F401  (back-compat для diag_node)
from greenlock.bench_harness import run_benchmark
from benchmarks.registry import load


def main() -> None:
    # Пробрасываем флаги как есть в общий раннер с фиксированным бенчмарком.
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--escalate", default="gemini-2.5-flash")
    ap.add_argument("--no-escalate", action="store_true")
    a = ap.parse_args()
    cfg = load("node_pricing")
    escalate = "" if a.no_escalate else a.escalate
    sys.exit(run_benchmark(
        repo=cfg["repo"], tasks=cfg["tasks"],
        native_cmd=cfg["native_cmd"], native_cwd=cfg["native_cwd"],
        model=a.model, escalate=escalate, title=cfg["title"],
    ))


if __name__ == "__main__":
    main()
