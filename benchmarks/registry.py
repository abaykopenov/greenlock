"""benchmarks — реестр конфигов бенчмарков письма кода.

Каждый конфиг: модуль задач (даёт REPO + TASKS) + команда родного тест-сета.
Добавить НОВЫЙ репо = (1) написать модуль задач (REPO + TASKS), (2) добавить
запись сюда. Раннер и оракул общие; ничего в ядре трогать не нужно.
"""
import sys

BENCHMARKS = {
    "pricing": {
        "tasks_module": "benchmarks.bench_tasks",
        "native_cmd": [sys.executable, "-m", "pytest", "bench_pricing/test_pricing.py", "-q"],
        "native_cwd": "repos",
        "title": "БЕНЧМАРК (Python) — repos/bench_pricing",
    },
    "node_pricing": {
        "tasks_module": "benchmarks.bench_node_tasks",
        "native_cmd": ["node", "--test", "bench_node_pricing/pricing.test.js"],
        "native_cwd": "repos",
        "title": "БЕНЧМАРК (JS) — repos/bench_node_pricing",
    },
}


def load(name: str):
    """Вернуть (repo, tasks, native_cmd, native_cwd, title) для бенчмарка по имени."""
    import importlib
    cfg = BENCHMARKS[name]
    mod = importlib.import_module(cfg["tasks_module"])
    return {
        "repo": mod.REPO,
        "tasks": mod.TASKS,
        "native_cmd": cfg["native_cmd"],
        "native_cwd": cfg["native_cwd"],
        "title": cfg["title"],
    }
