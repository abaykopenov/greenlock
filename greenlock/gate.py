"""core.gate — verify-only: внешний unified-diff → детерминированный вердикт.

Сердце продукта №1 (слой верификации НАД агентами). Патч пришёл от ЛЮБОГО
источника — Devin/Copilot/Cursor/человек — мы только проверяем и решаем:
  merge   — closed-world ✔, оракул зелёный, регрессий нет;
  reject  — иначе (с конкретной причиной).

Модель здесь НЕ вызывается: это чистый детерминированный гейт. Тем и отличаемся
от review-ботов (они советуют, вероятностно) и от обычного CI (он тупой и не
делает closed-world): мы блокируем доказательно.

Если в репо нет тестов — оракул degraded → reject с честной причиной «гарантию
дать нельзя, нужен слой генерации тестов (testgen)». Это и есть стык с №4.
"""
import os
import subprocess
import tempfile
from pathlib import Path

from greenlock import groundqa as g
from greenlock import isolate
from greenlock.config import OLLAMA_URL, DOCKER, DOCKER_IMAGE, TRUST
from greenlock.adapters import detect_verifier, detect_adapters
from greenlock.closed_world import closed_world_check
from greenlock.danger import scan_file
from greenlock.patch_applier import create_sandbox_dir, clean_sandbox_dir
from greenlock.code_writer import truncate_error_output

__all__ = ["verify_patch"]


def _truthy(v: str) -> bool:
    """Истинность переключателя из env/конфига ('1'/'true'/'yes'/'on')."""
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _changed_files(diff_text: str) -> list[str]:
    """Имена изменённых файлов из заголовков unified-diff (без префиксов a/ b/)."""
    seen: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            p = line[4:].strip().split("\t")[0]
            if p == "/dev/null":
                continue
            if p.startswith(("a/", "b/")):
                p = p[2:]
            if p and p not in seen:
                seen.append(p)
    return seen


def _changed_lines(diff_text: str) -> dict[str, set[int]]:
    """Новые (added) номера строк по файлам из unified-diff (сторона '+').

    Нужно для проверки покрытия: исполняются ли тестами именно изменённые строки.
    """
    result: dict[str, set[int]] = {}
    cur: str | None = None
    new_ln = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            p = line[4:].strip().split("\t")[0]
            if p == "/dev/null":
                cur = None
            else:
                if p.startswith(("a/", "b/")):
                    p = p[2:]
                cur = p
                result.setdefault(cur, set())
            continue
        if line.startswith("@@"):
            try:
                plus = line.split("+", 1)[1]
                new_ln = int(plus.split(",")[0].split(" ")[0])
            except (IndexError, ValueError):
                new_ln = 0
            continue
        if cur is None or new_ln == 0:
            continue
        if line.startswith("+"):
            result[cur].add(new_ln)
            new_ln += 1
        elif line.startswith(("-", "\\")):
            pass  # удалённая строка / "No newline" — новая сторона не двигается
        else:
            new_ln += 1  # контекст
    return result


def _apply_diff(repo_dir: Path, diff_text: str) -> str | None:
    """Применить unified-diff в repo_dir. None = успех, иначе текст ошибки.

    Песочница — не git-репо (.git не копируется), но `git apply` это умеет;
    запасной путь — системный `patch`.
    """
    text = diff_text if diff_text.endswith("\n") else diff_text + "\n"
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False,
                                     encoding="utf-8") as f:
        f.write(text)
        diff_path = f.name
    last = ""
    try:
        for cmd in (
            ["git", "apply", "-p1", "--whitespace=nowarn", diff_path],
            ["git", "apply", "-p0", "--whitespace=nowarn", diff_path],
            ["patch", "-p1", "--no-backup-if-mismatch", "-s", "-i", diff_path],
        ):
            try:
                proc = subprocess.run(cmd, cwd=str(repo_dir),
                                      capture_output=True, text=True, timeout=30)
            except FileNotFoundError:
                continue  # нет git или patch — пробуем следующий
            if proc.returncode == 0:
                return None
            last = (proc.stderr or proc.stdout or "").strip()
        return last or "patch did not apply"
    finally:
        try:
            os.unlink(diff_path)
        except OSError:
            pass


def verify_patch(repo, diff_text: str, *, base_url: str | None = None,
                 extra_tests: dict | None = None, trust: bool = False) -> dict:
    """Проверить внешний unified-diff против репо. Вернуть вердикт-словарь.

    Ключи: decision (merge|reject), reasons[], changed_files[], closed_world[],
    failing_stage, regression, confidence, test_output.

    extra_tests — {rel-путь: содержимое} дополнительных тест-файлов (например
    характеризационных от greenlock.testgen). Кладутся в песочницу ДО снятия
    baseline, поэтому участвуют в оракуле наравне с родным сетом.

    trust — доверенный автор (WS-3): danger-конструкции (eval/exec/subprocess/…) НЕ
    блокируют, а лишь сообщаются. Для self-CI/догфудинга над собственным кодом, где
    такие конструкции легитимны. По умолчанию False — danger-защита включена.
    """
    base_url = base_url or OLLAMA_URL
    repo_path = Path(repo).resolve()
    verdict = {
        "decision": "reject", "reasons": [], "changed_files": [],
        "closed_world": [], "danger": [], "failing_stage": None,
        "regression": False, "confidence": None, "test_output": "",
    }

    if not repo_path.is_dir():
        verdict["reasons"].append(f"нет репо: {repo}")
        return verdict
    changed = _changed_files(diff_text)
    if not changed:
        verdict["reasons"].append("в дифе не распознаны изменённые файлы")
        return verdict
    verdict["changed_files"] = changed

    sandbox = create_sandbox_dir(repo_path)
    try:
        repo_copy = sandbox / repo_path.name
        # дополнительные тесты (характеризация) — ДО baseline, чтобы войти в оракул
        for rel, content in (extra_tests or {}).items():
            p = repo_copy / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        # Верификатор определяем по КАТАЛОГУ РЕПО (там манифест), а не по родителю
        # песочницы — иначе манифест не виден и фолбэк может выбрать чужой раннер
        # (напр. Node для Python-проекта с .js-артефактами). Прогон тестов —
        # по-прежнему из sandbox (рекурсивное обнаружение).
        verifier = detect_verifier(repo_copy)
        # изменённые строки (сторона +) → verifier для проверки покрытия патча
        cl = _changed_lines(diff_text)
        verifier.changed_lines = {
            str(Path(repo_path.name) / rel): lns for rel, lns in cl.items()
        }
        try:
            baseline = verifier.capture_baseline(sandbox)
        except Exception as e:
            # провал = отказ, а не краш (DESIGN §6)
            verdict["reasons"].append(
                f"не удалось снять baseline оракула — отказ: {str(e)[:300]}")
            return verdict

        # baseline-содержимое изменённых файлов (до диффа) — для danger-диффа
        baseline_src = {}
        for rel in changed:
            ab = repo_copy / rel
            baseline_src[rel] = ab.read_text(encoding="utf-8") if ab.exists() else None

        err = _apply_diff(repo_copy, diff_text)
        if err:
            verdict["reasons"].append(f"диф не применился к репо: {err[:300]}")
            return verdict

        # Индекс символов из РЕАЛЬНОГО репо (песочница отбрасывается SKIP_DIRS,
        # т.к. лежит под .groundqa_sandbox). WS-4: дополняем его символами из
        # ПРОПАТЧЕННЫХ изменённых файлов — иначе символы, введённые патчем, считались
        # бы «несуществующими» (ложный closed-world reject на многофайловых правках).
        index = g.build_index(repo_path)
        syms = index.setdefault("symbols", {})
        _adapters = detect_adapters()
        for rel in changed:
            ab = repo_copy / rel
            if not ab.exists():
                continue
            ad = next((a for a in _adapters if ab.suffix in a.extensions), None)
            if not ad:
                continue
            try:
                pres = ad.parse(rel, ab.read_text(encoding="utf-8"))
            except Exception:
                continue
            for sym in pres.symbols:
                syms.setdefault(sym["name"], []).append((sym["file"], sym["line"]))

        # closed-world: запрет ссылок на несуществующие символы
        cw: list[str] = []
        for rel in changed:
            ab = repo_copy / rel
            if ab.exists():
                cw += closed_world_check(ab, index.get("symbols", {}))
        if cw:
            verdict["closed_world"] = cw[:20]
            verdict["reasons"].append(
                f"closed-world: {len(cw)} ссыл(ок) на несуществующие символы — отказ")
            return verdict

        # danger-фильтр: опасные/обфусцирующие конструкции, ВНЕСЁННЫЕ патчем
        # (eval/exec, os.system/subprocess, детекция тест-окружения). ДО оракула —
        # чтобы вредоносный патч был отклонён, не успев исполниться.
        danger = []
        for rel in changed:
            for tag in scan_file(repo_copy / rel, baseline_src.get(rel)):
                danger.append(f"{rel}: {tag}")
        if danger:
            verdict["danger"] = danger[:20]
            if not trust:
                verdict["reasons"].append(
                    "danger: патч вносит опасные/обфусцирующие конструкции "
                    f"({', '.join(danger[:6])}) — отказ (не исполнялось)")
                return verdict
            # trusted-режим (WS-3): danger остаётся как ИНФОРМАЦИЯ, но не блокирует —
            # решает оракул. Защита от злонамеренного автора в этом режиме отключена.
            verdict["reasons"].append(
                "danger (trusted, НЕ блокирует): " + ", ".join(danger[:6]))

        # оракул: синтаксис → тесты → регрессия vs baseline
        changed_for_verify = [str(Path(repo_path.name) / rel) for rel in changed]
        try:
            res = verifier.verify(sandbox, changed_for_verify, baseline=baseline)
        except Exception as e:
            verdict["reasons"].append(
                f"оракул упал при проверке — отказ: {str(e)[:300]}")
            return verdict
        verdict["confidence"] = res.get("confidence")
        verdict["regression"] = bool(res.get("regression"))

        if res.get("passed") and res.get("confidence") == "full":
            verdict["decision"] = "merge"
            verdict["reasons"].append("оракул зелёный, регрессий нет — можно мержить")
            return verdict

        stage = next((s for s in res.get("stages", []) if not s.get("ok")), None)
        if stage:
            verdict["failing_stage"] = stage["name"]
            verdict["test_output"] = truncate_error_output(stage.get("output", ""))
            verdict["reasons"].append(
                f"оракул НЕ зелёный: стадия '{stage['name']}'"
                + (", РЕГРЕССИЯ родного сета" if verdict["regression"] else "")
                + " — отказ")
        elif res.get("confidence") != "full":
            verdict["failing_stage"] = "coverage"
            # пробрасываем детали (какие файлы не покрыты) — иначе вердикт «немой»
            tstage = next((s for s in res.get("stages", [])
                           if s.get("name") == "tests"), None)
            if tstage:
                verdict["test_output"] = truncate_error_output(tstage.get("output", ""))
            verdict["reasons"].append(
                "изменённые строки не исполняются тестами (confidence=degraded) — "
                "гарантию дать нельзя; нужен слой генерации тестов (testgen) — отказ")
        else:
            verdict["reasons"].append("оракул не дал полной уверенности — отказ")
        return verdict
    finally:
        clean_sandbox_dir(sandbox)


def init_git_hook(repo_dir: str) -> int:
    """Установить pre-commit git хук и интерактивно настроить greenlock.json в репозитории."""
    import stat
    import json
    import sys
    path = Path(repo_dir).resolve()
    git_dir = path / ".git"
    if not git_dir.is_dir():
        print(f"❌ Ошибка: {repo_dir} не является корнем Git-репозитория (папка .git не найдена)")
        return 1

    if sys.stdin.isatty():
        print("=== Инициализация Greenlock ===")
        cfg_path = path / "greenlock.json"
        write_cfg = True
        if cfg_path.exists():
            ans = input("Файл greenlock.json уже существует. Перезаписать его? (y/N): ").strip().lower()
            if ans not in ("y", "yes"):
                write_cfg = False
                print("Пропускаем создание greenlock.json.")

        if write_cfg:
            config_data = {}
            print("\nВыберите тип верификатора:")
            print("  1) pytest (Python)")
            print("  2) node (Node.js)")
            print("  3) go (Go)")
            print("  4) rust (Rust)")
            print("  5) custom (Пользовательские команды)")
            verifier_opt = input("Введите номер [1-5] или Enter для автоопределения: ").strip()

            verifier_map = {
                "1": "pytest",
                "2": "node",
                "3": "go",
                "4": "rust",
                "5": "custom"
            }
            v_type = verifier_map.get(verifier_opt)
            if v_type:
                config_data["verifier"] = v_type
                if v_type == "custom":
                    cmd_syntax = input("Введите команду проверки синтаксиса (например, 'node --check main.js'): ").strip()
                    if cmd_syntax:
                        config_data["syntax_command"] = cmd_syntax
                    cmd_test = input("Введите команду тестирования (например, 'npm test'): ").strip()
                    if cmd_test:
                        config_data["test_command"] = cmd_test
                else:
                    cmd_test = input(f"Введите команду тестирования для {v_type} (или Enter для дефолтной): ").strip()
                    if cmd_test:
                        config_data["test_command"] = cmd_test

            if config_data:
                try:
                    cfg_path.write_text(json.dumps(config_data, indent=2, ensure_ascii=False), encoding="utf-8")
                    print(f"✅ Файл конфигурации успешно создан: {cfg_path}")
                except Exception as e:
                    print(f"❌ Не удалось записать greenlock.json: {e}")
            else:
                print("Конфигурация не выбрана (оставляем автоматическое определение).")

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    hook_file = hooks_dir / "pre-commit"

    hook_content = """#!/bin/sh
# Greenlock pre-commit gate hook
# Сгенерировано автоматически через: greenlock init

# Запуск проверки перед коммитом. Передаем индексированный дифф на stdin.
git diff --cached --binary | python3 -m greenlock.gate . -

if [ $? -ne 0 ]; then
    echo "🛑 Greenlock: коммит отклонён из-за ошибок верификации."
    exit 1
fi
"""

    try:
        hook_file.write_text(hook_content, encoding="utf-8")
        st = hook_file.stat()
        hook_file.chmod(st.st_mode | stat.S_IEXEC)
        print(f"✅ Git pre-commit хук успешно установлен: {hook_file}")
        return 0
    except Exception as e:
        print(f"❌ Не удалось записать файл хука: {e}")
        return 1


def _hints(v: dict) -> list[str]:
    """Подсказки «как починить» по полям вердикта (показываются при reject)."""
    hints: list[str] = []
    stage = v.get("failing_stage")
    reasons = " ".join(v.get("reasons", []))
    if stage == "coverage":
        hints.append("изменённый код не исполняется тестами — сгенерируй сетку: "
                     "`greenlock harden <repo> <diff>` (или добавь тест на изменение).")
    if v.get("closed_world"):
        hints.append("ссылки на неизвестные имена (опечатка / нет импорта) — см. 'cw:' выше.")
    if v.get("danger") and stage is None and "не исполнялось" in reasons:
        hints.append("danger-конструкции заблокированы; если ты доверенный автор и это "
                     "намеренно — повтори с `--trust` (или GREENLOCK_TRUST=1).")
    if v.get("regression"):
        hints.append("патч ломает существующие тесты (регрессия) — см. вывод оракула выше.")
    if "не применился" in reasons:
        hints.append("diff не применился — он должен быть против ТЕКУЩЕГО состояния репо "
                     "(проверь через `greenlock check`, который сам берёт `git diff`).")
    if "Docker" in reasons:
        hints.append("изоляция запрошена, но Docker недоступен — собери образ "
                     "(`docker build -t greenlock:latest .`) или сними `--isolated`.")
    return hints


def run_verdict(repo, diff_text: str, *, isolated=None, image=None, trust=None,
                as_json: bool = False) -> int:
    """Прогнать гейт и напечатать вердикт; вернуть exit-код (0=merge, 1=reject).

    Общая точка для CLI `greenlock gate`/`check` и `python -m greenlock.gate`.
    isolated/trust: None → берётся из env/greenlock.json; image → --image/DOCKER_IMAGE.
    """
    import json
    isolated_eff = isolated if isolated is not None else _truthy(DOCKER)
    trust_eff = trust if trust is not None else _truthy(TRUST)
    if isolated_eff:
        img = image or DOCKER_IMAGE or isolate.DEFAULT_IMAGE
        try:
            v = isolate.verify_patch_isolated(repo, diff_text, image=img)
        except RuntimeError as e:
            # изоляция запрошена, но Docker недоступен — fail-closed, не откатываемся
            v = {"decision": "reject", "isolated": True, "reasons": [str(e)],
                 "changed_files": [], "closed_world": [], "danger": [],
                 "failing_stage": None, "regression": False,
                 "confidence": None, "test_output": ""}
    else:
        v = verify_patch(repo, diff_text, trust=trust_eff)

    if as_json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
        return 0 if v["decision"] == "merge" else 1

    suffix = " [isolated]" if v.get("isolated") else ""
    print(("✅ MERGE" if v["decision"] == "merge" else "🛑 REJECT")
          + f"  ({repo}){suffix}")
    print("изменённые файлы:", ", ".join(v.get("changed_files", [])) or "—")
    for r in v.get("reasons", []):
        print("  •", r)
    for e in v.get("closed_world", []):
        print("    cw:", e)
    for e in v.get("danger", []):
        print("    danger:", e)
    if v.get("test_output"):
        print("  вывод оракула (хвост):")
        print("    " + v["test_output"].replace("\n", "\n    "))
    if v["decision"] != "merge":
        for h in _hints(v):
            print("  → как починить:", h)
    return 0 if v["decision"] == "merge" else 1


def main() -> int:
    import argparse
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        repo_dir = sys.argv[2] if len(sys.argv) > 2 else "."
        return init_git_hook(repo_dir)

    ap = argparse.ArgumentParser(
        description="verify-only gate: внешний unified-diff → вердикт merge|reject")
    ap.add_argument("repo", help="путь к репозиторию (любому)")
    ap.add_argument("diff", help="файл с unified-diff ('-' = stdin)")
    ap.add_argument("--json", action="store_true", help="вывести вердикт как JSON")
    ap.add_argument(
        "--isolated", dest="isolated", action="store_true", default=None,
        help="прогнать весь гейт в Docker-изоляции (--network none, read-only, "
             "non-root, лимиты); дефолт берётся из GREENLOCK_DOCKER/greenlock.json")
    ap.add_argument(
        "--no-isolated", dest="isolated", action="store_false",
        help="принудительно без изоляции (даже если включена в конфиге)")
    ap.add_argument(
        "--image", default=None,
        help=f"Docker-образ для изоляции (дефолт {isolate.DEFAULT_IMAGE} или "
             "GREENLOCK_DOCKER_IMAGE)")
    ap.add_argument(
        "--trust", dest="trust", action="store_true", default=None,
        help="доверенный автор: danger-конструкции (eval/subprocess/…) НЕ блокируют, "
             "лишь сообщаются. Дефолт берётся из GREENLOCK_TRUST/greenlock.json")
    ap.add_argument(
        "--no-trust", dest="trust", action="store_false",
        help="принудительно включить danger-защиту (даже если trust в конфиге)")
    a = ap.parse_args()

    diff_text = sys.stdin.read() if a.diff == "-" else \
        Path(a.diff).read_text(encoding="utf-8")
    return run_verdict(a.repo, diff_text, isolated=a.isolated, image=a.image,
                       trust=a.trust, as_json=a.json)


if __name__ == "__main__":
    raise SystemExit(main())
