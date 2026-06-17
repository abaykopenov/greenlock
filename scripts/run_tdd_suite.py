#!/usr/bin/env python3
"""run_tdd_suite.py — прогон TDD-набора задач для приёмки Фазы 1.

Запуск: python3 run_tdd_suite.py
"""
import argparse
import sys
import types
from pathlib import Path

from greenlock import groundqa as g
from greenlock.code_writer import write_code
from greenlock.config import OLLAMA_URL, EMBED_MODEL

TDD_TASKS = [
    {
        "id": "task_1_new_file",
        "repo": "sample_project",
        "file": "sample_project/math_utils.py",
        "instruction": "Создай новый файл sample_project/math_utils.py и реализуй в нём функцию fibonacci(n: int) -> int. Для n <= 0 возвращай 0, для n=1 возвращай 1, для n=2 возвращай 1.",
        "test_file": "sample_project/test_math.py",
        "test_content": """
def test_fibonacci():
    from sample_project.math_utils import fibonacci
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    assert fibonacci(5) == 5
""",
        "expected": "apply",
    },
    {
        "id": "task_2_repair",
        "repo": "sample_project",
        "file": "sample_project/storage.py",
        "instruction": "Добавь метод clear_tasks() в класс TaskStore. Метод должен очищать все задачи из хранилища (self._tasks) и сохранять изменения.",
        "test_file": "sample_project/test_clear.py",
        "test_content": """
def test_clear():
    from sample_project.storage import TaskStore
    from sample_project.models import Task
    from pathlib import Path
    import tempfile, json
    with tempfile.NamedTemporaryFile(suffix='.json', mode='w+') as f:
        json.dump({}, f)
        f.flush()
        store = TaskStore(Path(f.name))
        store.add_task("Test task")
        assert len(store.all_tasks()) == 1
        store.clear_tasks()
        assert len(store.all_tasks()) == 0
""",
        "expected": "apply",
    },
    {
        "id": "task_3_refusal_regression",
        "repo": "sample_project",
        "file": "sample_project/models.py",
        "instruction": "Измени приоритет по умолчанию в Task на 'high' (вместо 'normal').",
        "test_file": "sample_project/test_regression.py",
        "test_content": """
def test_default_priority_high():
    from sample_project.models import Task
    assert Task(id=1, title="x").priority == "high"
""",
        "expected": "refuse",
    }
]


def make_args(model: str, escalate: str, repo: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        repo=repo, model=model, escalate=escalate,
        embed_model=EMBED_MODEL,
        base_url=OLLAMA_URL,
        timeout=120, show_context=False, question=None,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--escalate", default="gemini-2.5-flash")
    ap.add_argument("--no-escalate", action="store_true")
    a = ap.parse_args()
    escalate = "" if a.no_escalate else a.escalate

    print("======================================================================")
    print("ЗАПУСК TDD-НАБОРА ЗАДАЧ (ФАЗА 1)")
    print(f"Маленькая модель: {a.model}   Эскалация: {escalate or '(выкл)'}")
    print("======================================================================")

    tot_passed = 0
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}

    for task in TDD_TASKS:
        print(f"\n[Задача: {task['id']}]")
        print(f"Инструкция: {task['instruction']}")
        
        args = make_args(a.model, escalate, task["repo"])
        index = g.build_index(Path(task["repo"]))
        
        # Сохранение исходного состояния файла для отката
        filepath = Path(task["file"])
        orig_exists = filepath.exists()
        orig_content = filepath.read_text(encoding="utf-8") if orig_exists else None

        # Относительный путь для оркестратора (должен быть относительно task["repo"])
        rel_file_for_writer = str(filepath.relative_to(Path(task["repo"])))
        rel_test_file = str(Path(task["test_file"]).relative_to(Path(task["repo"])))

        print("Запуск кодогенератора...")
        success, msg, usage, status = write_code(
            args, index, task["instruction"], rel_file_for_writer,
            additional_test_file=rel_test_file,
            additional_test_content=task["test_content"]
        )

        for k in total_tokens:
            total_tokens[k] += usage.get(k, 0)

        # Проверка итогового состояния файла
        file_changed = False
        if orig_exists:
            current_content = filepath.read_text(encoding="utf-8") if filepath.exists() else None
            file_changed = (current_content != orig_content)
        else:
            file_changed = filepath.exists()

        # Судейство вердикта
        verdict = "FAIL"
        if task["expected"] == "apply":
            if status == "applied" and file_changed:
                verdict = "PASS"
                tot_passed += 1
            else:
                verdict = f"FAIL (ожидалось применение 'applied', статус: {status}, файл_изменен: {file_changed})"
        elif task["expected"] == "refuse":
            if status == "refused" and not file_changed:
                verdict = "PASS"
                tot_passed += 1
            else:
                verdict = f"FAIL (ожидался отказ 'refused', статус: {status}, файл_изменен: {file_changed})"

        print(f"Результат: {verdict}")
        print(f"Сообщение: {msg}")
        print(f"Использовано токенов в задаче: ввод {usage.get('prompt', 0)} + "
              f"вывод {usage.get('completion', 0)} = {usage.get('prompt', 0) + usage.get('completion', 0)} "
              f"(gemini-total: {usage.get('total', 0)})")

        # Восстановление исходного состояния
        if orig_exists and orig_content is not None:
            filepath.write_text(orig_content, encoding="utf-8")
        elif not orig_exists and filepath.exists():
            filepath.unlink()

    print("\n======================================================================")
    print("СВОДКА TDD-НАБОРА:")
    print(f"  Успешно пройдено: {tot_passed} из {len(TDD_TASKS)}")
    print(f"  Токены: ввод {total_tokens['prompt']} + вывод {total_tokens['completion']} "
          f"= {total_tokens['prompt'] + total_tokens['completion']} (gemini-total: {total_tokens['total']})")
    print("======================================================================")

    if tot_passed == len(TDD_TASKS):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
