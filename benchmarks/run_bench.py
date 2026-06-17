#!/usr/bin/env python3
"""run_bench.py — ЕДИНЫЙ параметрический раннер бенчмарков письма кода.

    python3 run_bench.py <имя_бенчмарка> [--model M] [--no-escalate]
    python3 run_bench.py --list

Заменяет прежние почти-дубли run_bench/run_node_bench: язык/репо/команда родного
теста задаются конфигом в benchmarks.py. Без аргумента — бенчмарк 'pricing'
(обратная совместимость).
"""
import argparse
import sys

from greenlock.bench_harness import run_benchmark, native_suite_green as _native_green
from greenlock.config import OLLAMA_URL, EMBED_MODEL
from benchmarks.registry import BENCHMARKS, load

# --- back-compat для diag/smoke-скриптов ---
REPO = "repos/bench_pricing"


def make_args(model: str, escalate: str, repo: str):
    import types
    return types.SimpleNamespace(
        repo=repo, model=model, escalate=escalate,
        embed_model=EMBED_MODEL,
        base_url=OLLAMA_URL,
        timeout=120, show_context=False, question=None,
    )


def native_suite_green() -> bool:
    """Back-compat: родной сет бенчмарка 'pricing'."""
    cfg = BENCHMARKS["pricing"]
    return _native_green(cfg["native_cmd"], cfg["native_cwd"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("benchmark", nargs="?", default="pricing",
                    help="имя бенчмарка (см. --list)")
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--escalate", default="gemini-2.5-flash")
    ap.add_argument("--no-escalate", action="store_true")
    ap.add_argument("--list", action="store_true", help="показать доступные бенчмарки")
    a = ap.parse_args()

    if a.list:
        print("Доступные бенчмарки:")
        for name, cfg in BENCHMARKS.items():
            print(f"  {name:14} → {cfg['title']}")
        sys.exit(0)

    if a.benchmark not in BENCHMARKS:
        print(f"Неизвестный бенчмарк '{a.benchmark}'. Доступные: {', '.join(BENCHMARKS)}")
        sys.exit(2)

    cfg = load(a.benchmark)
    escalate = "" if a.no_escalate else a.escalate
    code = run_benchmark(
        repo=cfg["repo"], tasks=cfg["tasks"],
        native_cmd=cfg["native_cmd"], native_cwd=cfg["native_cwd"],
        model=a.model, escalate=escalate, title=cfg["title"],
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
