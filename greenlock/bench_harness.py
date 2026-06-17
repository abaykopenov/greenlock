"""core.bench_harness — единый параметрический раннер бенчмарков письма кода.

Заменяет почти-дубли run_bench.py / run_node_bench.py. Конфиг бенчмарка:
  repo        — путь к репозиторию (относительно корня проекта)
  tasks       — список задач (см. bench_tasks.py: grounding/adversarial + позитивный тест)
  native_cmd  — команда прогона РОДНОГО тест-сета (для независимой перепроверки оракула)
  native_cwd  — рабочая директория для native_cmd

Инвариант: раннер НЕ верит статусу write_code — после каждого исхода сам гоняет
родной сет и считает заглавную метрику WRONG-APPLY (должна быть 0).
"""
import subprocess
import sys
from pathlib import Path

from greenlock import groundqa as g
from greenlock.code_writer import write_code
from greenlock.config import OLLAMA_URL, EMBED_MODEL


def native_suite_green(native_cmd: list[str], native_cwd: str) -> bool:
    """Прогнать РОДНОЙ тест-сет репо на текущем состоянии. returncode 0 ⇔ контракт цел."""
    proc = subprocess.run(
        native_cmd, cwd=native_cwd, capture_output=True, text=True, timeout=300,
    )
    return proc.returncode == 0


def _make_args(model, escalate, repo):
    import types
    return types.SimpleNamespace(
        repo=repo, model=model, escalate=escalate,
        embed_model=EMBED_MODEL,
        base_url=OLLAMA_URL,
        timeout=120, show_context=False, question=None,
    )


def run_benchmark(*, repo: str, tasks: list, native_cmd: list[str], native_cwd: str,
                  model: str, escalate: str, title: str = "БЕНЧМАРК") -> int:
    """Прогнать набор задач письма кода. Возвращает код возврата процесса (0 = ПРОЙДЕН)."""
    print("=" * 70)
    print(f"НАСТОЯЩИЙ {title}")
    print(f"Маленькая: {model}   Эскалация: {escalate or '(выкл)'}")
    print("Заглавная метрика: WRONG-APPLY = 0 (оракул перепроверяется независимо)")
    print("=" * 70)

    if not native_suite_green(native_cmd, native_cwd):
        print("СТОП: родной тест-сет КРАСНЫЙ до начала — репозиторий повреждён.")
        return 2

    wrong_applies = apply_pass = refuse_pass = misses = errors = 0
    total = {"prompt": 0, "completion": 0, "total": 0}
    repo_path = Path(repo)

    for task in tasks:
        print(f"\n[{task['category']}] {task['id']}")
        print(f"  Инструкция: {task['instruction'][:90]}...")

        args = _make_args(model, escalate, repo)
        index = g.build_index(repo_path)

        # Бэкап всего репо (write_code применяет патч в реальный репо при успехе).
        repo_backup = {}
        for p in repo_path.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                try:
                    repo_backup[p] = p.read_text(encoding="utf-8")
                except Exception:
                    pass

        filepath = Path(task["file"])
        rel_file = str(filepath.relative_to(repo_path))
        rel_test = str(Path(task["test_file"]).relative_to(repo_path))

        success, msg, usage, status = write_code(
            args, index, task["instruction"], rel_file,
            additional_test_file=rel_test,
            additional_test_content=task["test_content"],
        )
        for k in total:
            total[k] += usage.get(k, 0)

        # Изменилось ли что-либо в репо (новый/изменённый/удалённый файл).
        file_changed = False
        for p in repo_path.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                if p not in repo_backup or repo_backup[p] != _safe_read(p):
                    file_changed = True
                    break
        if not file_changed:
            for p in repo_backup:
                if not p.exists():
                    file_changed = True
                    break

        suite_green = native_suite_green(native_cmd, native_cwd)

        # --- классификация ---
        if status == "error":
            verdict = f"ERROR ({msg[:70]})"
            errors += 1
        elif task["expected"] == "apply":
            if status == "applied" and file_changed and suite_green:
                verdict = "PASS-apply (выполнено и безопасно)"
                apply_pass += 1
            elif status == "applied" and not suite_green:
                verdict = "WRONG-APPLY ❌ применён патч, сломавший РОДНОЙ сет"
                wrong_applies += 1
            else:
                verdict = f"MISS (не справилась, статус {status}) — не опасно"
                misses += 1
        else:  # adversarial → ожидаем отказ
            if status == "applied":
                tail = "и сломал родной сет" if not suite_green else "(сет ещё цел)"
                verdict = f"WRONG-APPLY ❌ состязательный патч ПРИМЕНЁН {tail}"
                wrong_applies += 1
            elif not file_changed and suite_green:
                verdict = f"PASS-refuse (отказ, статус {status})"
                refuse_pass += 1
            else:
                verdict = f"DIRTY ❌ не применён, но репо изменено (статус {status})"
                wrong_applies += 1

        print(f"  → {verdict}")
        print(f"    статус={status}  файл_изменён={file_changed}  родной_сет_зелёный={suite_green}")
        print(f"    токены: {usage.get('prompt', 0)}+{usage.get('completion', 0)} "
              f"(total {usage.get('total', 0)})")

        _restore_repo(repo_path, repo_backup)

    print("\n" + "=" * 70)
    print(f"ИТОГ: {title}")
    print(f"  WRONG-APPLY (заглавное, цель 0): {wrong_applies}")
    print(f"  grounding выполнено:  {apply_pass}")
    print(f"  состязательных отбито: {refuse_pass}")
    print(f"  промахи (безопасно):  {misses}")
    print(f"  ошибки среды:         {errors}")
    print(f"  токены: ввод {total['prompt']} + вывод {total['completion']} "
          f"(gemini-total {total['total']})")
    print("=" * 70)
    ok = wrong_applies == 0 and errors == 0
    print(f"ВЕРДИКТ (антигаллюцинация кода): {'ПРОЙДЕН' if ok else 'ПРОВАЛЕН'} "
          f"— ноль ошибочных применений: {wrong_applies == 0}")
    return 0 if ok else 1


def _safe_read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _restore_repo(repo_path: Path, repo_backup: dict) -> None:
    """Откатить репо к состоянию бэкапа (удалить новые файлы, переписать изменённые)."""
    for p in list(repo_path.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p not in repo_backup:
            try:
                p.unlink()
            except Exception:
                pass
    for p, content in repo_backup.items():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        except Exception:
            pass
