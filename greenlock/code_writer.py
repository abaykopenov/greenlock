"""core.code_writer — оркестратор цикла verify→repair.

Координирует работу генератора кода, префилтера closed-world, оракула верификации
и механизма ремонта/эскалации.
"""
import json
import re
import urllib.error
from pathlib import Path

from greenlock.adapters import detect_verifier, detect_adapters
from greenlock.closed_world import closed_world_check
from greenlock.patch_applier import create_sandbox_dir, apply_patch, clean_sandbox_dir
from greenlock.qa import generate
from greenlock.dep_closure import get_dependency_closure, get_dependency_signatures

__all__ = ["write_code", "parse_model_patch", "parse_model_patches", "truncate_error_output"]


def parse_model_patches(response: str) -> list[dict] | None:
    """Извлечь и распарсить JSON-патчи из ответа модели.

    Возвращает список словарей-патчей или None.
    """
    s = response.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    def _as_list(obj):
        if isinstance(obj, list):
            return [x for x in obj if isinstance(x, dict)]
        if isinstance(obj, dict):
            if "patches" in obj and isinstance(obj["patches"], list):
                return [x for x in obj["patches"] if isinstance(x, dict)]
            return [obj]
        return None

    try:
        return _as_list(json.loads(s))
    except Exception:
        # Попытка найти блок [] или {} регулярным выражением
        m_arr = re.search(r"\[\s*\{.*\}\s*\]", s, re.DOTALL)
        if m_arr:
            try:
                return _as_list(json.loads(m_arr.group(0)))
            except Exception:
                pass
        m_obj = re.search(r"\{.*\}", s, re.DOTALL)
        if m_obj:
            try:
                return _as_list(json.loads(m_obj.group(0)))
            except Exception:
                pass
        return None


def parse_model_patch(response: str) -> dict | None:
    """Извлечь и распарсить один JSON-патч (для совместимости)."""
    patches = parse_model_patches(response)
    if patches:
        return patches[0]
    return None


def truncate_error_output(output: str) -> str:
    """Усечь вывод тестов до хвоста ошибок (последние 30 строк)."""
    lines = output.splitlines()
    if len(lines) > 30:
        return "... [вывод усечён] ...\n" + "\n".join(lines[-30:])
    return output


def _symbol_menu(filepath: str, content: str) -> str:
    """Квалифицированные имена символов файла (Класс.метод) — чтобы модель
    выбирала реальные цели для replace_symbol, а не угадывала."""
    from greenlock.adapters import detect_adapters
    from pathlib import Path
    suffix = Path(filepath).suffix
    adapter = None
    for a in detect_adapters():
        if suffix in a.extensions:
            adapter = a
            break
    if not adapter or adapter.name == "regex-fallback":
        return ""
    try:
        res = adapter.parse(filepath, content)
    except Exception:
        return ""
    classes = [s for s in res.symbols if s["kind"] == "class"]
    items = []
    for s in res.symbols:
        if s["kind"] == "class":
            items.append(f"{s['name']} (класс)")
        elif s["kind"] == "method":
            parent = next((c for c in classes
                           if c["span_start"] is not None and s["span_start"] is not None
                           and c["span_start"] <= s["span_start"] <= c["span_end"]), None)
            q = f"{parent['name']}.{s['name']}" if parent else s["name"]
            items.append(f"{q} (метод)")
        elif s["kind"] == "func":
            items.append(f"{s['name']} (функция)")
    return ", ".join(items)


def normalize_patch_file(root_name: str, target_file: str, patch_file: str) -> str:
    """Нормализует путь файла в патче так, чтобы он начинался с root_name."""
    if not patch_file:
        return str(Path(root_name) / target_file)
    p = Path(patch_file)
    if p.parts and p.parts[0] == root_name:
        return str(p)
    if p.parts and len(p.parts) > 1 and p.parts[0] == "repos" and p.parts[1] == root_name:
        return str(Path(*p.parts[1:]))
    return str(Path(root_name) / patch_file)


def write_code(args, index: dict, task_desc: str, target_file: str,
               additional_test_file: str | None = None,
               additional_test_content: str | None = None,
               max_tries: int = 3, on_event=None) -> tuple[bool, str, dict, str]:
    """Главный цикл написания и проверки кода с восстановлением (repair).

    Возвращает (success, message, token_usage, status).
    status может быть: "applied", "refused", "failed", "error".

    on_event(dict) — необязательный колбэк прогресса (для веб-UI/SSE). Вызывается
    на каждой стадии цикла; исключения внутри него глушатся, чтобы не ломать оракул.
    """
    total_usage = {"prompt": 0, "completion": 0, "total": 0}
    sandbox = None

    def emit(etype, **kw):
        if on_event:
            try:
                on_event({"type": etype, **kw})
            except Exception:
                pass

    try:
        # 1. Создание песочницы
        root = Path(args.repo)
        try:
            sandbox = create_sandbox_dir(root)
        except Exception as e:
            return False, f"Failed to create sandbox: {e}", total_usage, "error"
        emit("sandbox", target=target_file,
             test=additional_test_file or None)

        # Подложим файл теста, если передан
        if additional_test_file and additional_test_content:
            test_path = sandbox / root.name / additional_test_file
            try:
                test_path.parent.mkdir(parents=True, exist_ok=True)
                test_path.write_text(additional_test_content, encoding="utf-8")
            except Exception as e:
                return False, f"Failed to write task test to sandbox: {e}", total_usage, "error"

        # 2. Определение верификатора и запуск baseline
        try:
            verifier = detect_verifier(sandbox)
            baseline = verifier.capture_baseline(sandbox)
        except Exception as e:
            return False, f"Baseline capture crashed: {e}", total_usage, "error"
        emit("baseline", verifier=type(verifier).__name__,
             passed=len(baseline.get("passed", []) or []),
             confidence=baseline.get("confidence"))

        # Проверка позитивного оракула (Must-Fix #1):
        # Тест к задаче должен существовать и падать (быть RED) на baseline.
        if additional_test_file:
            test_module_name = Path(additional_test_file).name
            # Для проверки имени JS-тестов и Python-тестов используем как stem, так и basename
            test_module_stem = Path(additional_test_file).name
            if test_module_stem.endswith(".test.js"):
                # Для JS тестов усекаем расширение
                test_module_stem = test_module_stem[:-8]
            elif test_module_stem.endswith(".js") or test_module_stem.endswith(".py"):
                test_module_stem = Path(additional_test_file).stem
            
            already_passed = []
            for t in baseline.get("passed", set()):
                parts = t.split("::")
                if parts:
                    class_parts = parts[0].split(".")
                    # Поддержка как Python-тестов, так и путей в JS-тестах
                    if test_module_stem in class_parts or test_module_name in t:
                        already_passed.append(t)
            if already_passed:
                return (
                    False,
                    f"Positive oracle error: task test '{test_module_name}' is already passing on baseline "
                    f"(tests: {', '.join(already_passed)}). It must fail first.",
                    total_usage,
                    "error"
                )
            emit("precondition", test=test_module_name, red=True)

        # 3. Подготовка контекста grounding (разрешённые символы)
        allowed_globals = sorted(list(index.get("symbols", {}).keys()))

        # Чтение текущего состояния файла
        rel_filepath = Path(target_file)
        abs_filepath_sandbox = sandbox / root.name / rel_filepath
        current_content = ""
        if abs_filepath_sandbox.exists():
            current_content = abs_filepath_sandbox.read_text(encoding="utf-8")

        symbol_menu = _symbol_menu(target_file, current_content)

        ext = Path(target_file).suffix
        if ext in (".js", ".ts"):
            method_example = '{"mode":"add_method","class":"ClassName","replacement":"  method_name(args) {\\n    body\\n  }"}'
            replace_example = '{"mode":"replace_symbol","symbol":"Class.method","replacement":"  method_name(args) {\\n    body\\n  }"}'
            example_file1 = f"repos/{root.name}/pricing.js"
            example_file2 = f"repos/{root.name}/utils.js"
            replacement_lang_desc = "JavaScript"
            syntax_lang = "javascript"
        else:
            method_example = '{"mode":"add_method","class":"ClassName","replacement":"    def method_name(self, args):\\n        body"}'
            replace_example = '{"mode":"replace_symbol","symbol":"Class.method","replacement":"    def method_name(self, args):\\n        body"}'
            example_file1 = f"repos/{root.name}/pricing.py"
            example_file2 = f"repos/{root.name}/utils.py"
            replacement_lang_desc = "Python"
            syntax_lang = "python"

        grounding_context = (
            "Ты работаешь в closed-world режиме: используй только существующие "
            "символы проекта, не выдумывай функции/классы/методы.\n"
            f"Доступные имена проекта: {', '.join(allowed_globals)}\n"
            f"Существующие символы файла (цели для replace_symbol): {symbol_menu or '—'}\n"
            "Выдай список патчей строго в JSON (массив объектов), обёрнутом в ```json ... ```.\n"
            "Каждый объект в массиве должен содержать:\n"
            "  • \"file\": относительный путь файла от корня репозитория.\n"
            "  • \"mode\": один из режимов:\n"
            "      - \"add_method\": добавить новый метод в класс (требует \"class\" и \"replacement\");\n"
            "      - \"replace_symbol\": заменить существующий символ целиком (требует \"symbol\" и \"replacement\");\n"
            "      - \"new_file\": создать новый файл (требует \"replacement\");\n"
            "      - \"rewrite_file\": переписать файл целиком (требует \"replacement\").\n"
            f"Пример патча:\n"
            f"[\n"
            f"  {method_example[:-1]}, \"file\": \"{example_file1}\"}},\n"
            f"  {{\"mode\": \"new_file\", \"file\": \"{example_file2}\", \"replacement\": \"<код>\"}}\n"
            f"]\n"
            f"replacement — код с правильными отступами {replacement_lang_desc}. Никакого текста вне JSON.\n"
        )

        system_prompt = (
            "Ты — AI-программист. Твоя задача — написать/исправить код по заданию.\n"
            "Отвечай строго JSON-патчем по шаблону."
        )

        # Сбор сигнатур зависимостей
        dep_closure = get_dependency_closure(index, target_file, max_depth=2)
        dep_signatures_list = []
        for dep_file in sorted(list(dep_closure)):
            sigs = get_dependency_signatures(index, dep_file)
            if sigs:
                dep_signatures_list.append(f"Файл {dep_file}:\n{sigs}")

        dep_context_str = ""
        if dep_signatures_list:
            dep_context_str = "\nКОНТЕКСТ ЗАВИСИМОСТЕЙ\n" + "\n".join(dep_signatures_list) + "\n"

        # Сбор RLM карточек знаний
        from greenlock.rlm import get_or_build_card
        rlm_cards = []
        target_symbols = []
        target_content = index["files"].get(target_file, "")
        if target_content:
            try:
                suffix = Path(target_file).suffix
                for a in detect_adapters():
                    if suffix in a.extensions:
                        res = a.parse(target_file, target_content)
                        for sym in res.symbols:
                            target_symbols.append(sym["name"])
                        for ref in res.refs:
                            ref_name = ref["name"]
                            defining_locs = index.get("symbols", {}).get(ref_name, [])
                            for defining_file, line in defining_locs:
                                if defining_file in dep_closure:
                                    target_symbols.append(ref_name)
                        break
            except Exception:
                pass

        target_symbols = sorted(list(set(target_symbols)))
        for sym_name in target_symbols:
            defining_locs = index.get("symbols", {}).get(sym_name, [])
            for defining_file, line in defining_locs:
                if defining_file == target_file or defining_file in dep_closure:
                    card = get_or_build_card(args, index, defining_file, sym_name)
                    if card:
                        rlm_cards.append(card)
                    break

        rlm_context_str = ""
        if rlm_cards:
            rlm_sections = []
            for card in rlm_cards:
                verified = card.get("verified", {})
                advisory = card.get("advisory", {})
                callers = ", ".join(verified.get("callers", []))
                callees = ", ".join(verified.get("callees", []))
                rlm_sections.append(
                    f"- Символ: {card['symbol']} ({verified.get('location', '')})\n"
                    f"  Сигнатура: {verified.get('signature', '')}\n"
                    f"  Параметры: {', '.join(verified.get('params', []))}\n"
                    f"  Импортирует: {', '.join(verified.get('imports', []))}\n"
                    f"  Вызывается в: {callers or '—'}\n"
                    f"  Вызывает: {callees or '—'}\n"
                    f"  Назначение (Advisory): {advisory.get('purpose', '(описание отсутствует)')}\n"
                    f"  Пример использования: {advisory.get('recipe', '—')}"
                )
            rlm_context_str = "\nКАРТОЧКИ ЗНАНИЙ (RLM)\n" + "\n\n".join(rlm_sections) + "\n"

        user_prompt = (
            f"{grounding_context}\n"
            f"{rlm_context_str}"
            f"{dep_context_str}"
            f"Задание: {task_desc}\n\n"
            f"Текущее содержимое файла {target_file}:\n"
            f"```{syntax_lang}\n{current_content}\n```\n"
        )

        # Циклы генерации
        model = args.model
        tries = 0
        last_error = ""

        # Сначала пытаемся маленькой моделью, потом (при неудаче) эскалируем на большую
        run_models = [(model, max_tries)]
        if args.escalate:
            run_models.append((args.escalate, 2))

        # Для разделения отказа по регрессии от обычной неудачи
        is_regression_reject = False

        # Бэкап измененных файлов: abs_path -> (original_content, existed)
        sandbox_backups = {}

        for active_model, runs in run_models:
            for attempt in range(runs):
                tries += 1
                emit("attempt", n=tries, model=active_model,
                     repair=bool(last_error))

                # Подготовка промпта с историей ошибок, если они были
                current_user_prompt = user_prompt
                if last_error:
                    current_user_prompt += (
                        f"\nПредыдущая попытка завершилась ошибкой:\n"
                        f"```\n{last_error}\n```\n"
                        f"Исправь код с учётом этой ошибки."
                    )

                emit("generate", model=active_model, stage="модель пишет патч")
                try:
                    response, usage = generate(args, active_model, system_prompt, current_user_prompt)
                    for k in total_usage:
                        total_usage[k] += usage.get(k, 0)
                    emit("generated", model=active_model, chars=len(response or ""))
                except urllib.error.HTTPError as e:
                    # Сетевая ошибка (например, 503) — прокидываем статус "error"
                    return (
                        False,
                        f"Model generation error: HTTP Error {e.code}: {e.reason}",
                        total_usage,
                        "error"
                    )
                except Exception as e:
                    last_error = f"Model generation error: {e}"
                    continue

                # 1. Откат изменений из предыдущей попытки
                for path_abs, (orig_content, existed) in sandbox_backups.items():
                    if existed:
                        path_abs.write_text(orig_content, encoding="utf-8")
                    else:
                        if path_abs.exists():
                            path_abs.unlink()
                sandbox_backups.clear()

                # Парсинг патчей
                patches = parse_model_patches(response)
                if not patches:
                    err = "Invalid patch format. Output must be a valid JSON matching the instructions."
                    emit("parse", ok=False, error=err)
                    if err == last_error:
                        break  # Ранний стоп на повторение ошибки
                    last_error = err
                    continue
                emit("parse", ok=True, count=len(patches),
                     modes=[p.get("mode") for p in patches])

                # Применение патчей с бэкапом
                apply_err = None
                for patch in patches:
                    rel_file = normalize_patch_file(root.name, target_file, patch.get("file", ""))
                    patch["file"] = rel_file
                    abs_file = sandbox / rel_file

                    # Защита от обхода пути: файл обязан лежать ВНУТРИ песочницы
                    # (модель — недоверенный источник; '..'/абсолютные пути режем).
                    try:
                        abs_file.resolve().relative_to(sandbox.resolve())
                    except ValueError:
                        apply_err = f"Patch path escapes sandbox: {patch.get('file')!r}"
                        break

                    if abs_file not in sandbox_backups:
                        existed = abs_file.exists()
                        orig_content = abs_file.read_text(encoding="utf-8") if existed else ""
                        sandbox_backups[abs_file] = (orig_content, existed)

                    err = apply_patch(sandbox, patch)
                    if err:
                        apply_err = err
                        break

                if apply_err:
                    emit("apply", ok=False, error=apply_err)
                    if apply_err == last_error:
                        break
                    last_error = apply_err
                    continue
                emit("apply", ok=True,
                     files=sorted({str(p.relative_to(sandbox)) for p in sandbox_backups}))

                # Closed-world префилтер для всех измененных файлов
                cw_errors = []
                for abs_file in sandbox_backups:
                    if abs_file.exists():
                        errors = closed_world_check(abs_file, index.get("symbols", {}))
                        cw_errors.extend(errors)

                if cw_errors:
                    err = "Closed-world validation failed:\n" + "\n".join(cw_errors)
                    emit("closed_world", ok=False, errors=cw_errors[:8])
                    if err == last_error:
                        break
                    last_error = err
                    continue
                emit("closed_world", ok=True)

                # Оракул (верификация)
                changed_files = []
                for abs_file in sandbox_backups:
                    rel_to_sandbox = abs_file.relative_to(sandbox)
                    changed_files.append(str(rel_to_sandbox))

                if additional_test_file:
                    changed_files.append(str(Path(root.name) / additional_test_file))

                emit("verify", stage="оракул гоняет тесты")
                res = verifier.verify(sandbox, changed_files, baseline=baseline)
                emit("verified", passed=bool(res.get("passed")),
                     confidence=res.get("confidence"),
                     regression=bool(res.get("regression", False)))

                # Дополнительная проверка на регрессию
                if res.get("regression", False):
                    is_regression_reject = True

                # Если все тесты зелёные и уверенность полная
                if res["passed"] and res["confidence"] == "full":
                    # Переносим результаты всех измененных файлов в основной репозиторий
                    for abs_file, (orig_content, existed) in sandbox_backups.items():
                        try:
                            rel_to_repo = abs_file.relative_to(sandbox / root.name)
                            dest_file = root / rel_to_repo
                            if abs_file.exists():
                                dest_file.parent.mkdir(parents=True, exist_ok=True)
                                dest_file.write_text(abs_file.read_text(encoding="utf-8"), encoding="utf-8")
                            else:
                                if dest_file.exists():
                                    dest_file.unlink()
                        except Exception as e:
                            return False, f"Failed to apply patch from sandbox to main repo: {e}", total_usage, "error"

                    emit("applied", tries=tries, files=changed_files)
                    return True, f"Code successfully applied after {tries} attempts.", total_usage, "applied"

                # Если тесты красные или degraded
                failed_stage = None
                for stage in res["stages"]:
                    if not stage["ok"]:
                        failed_stage = stage
                        break

                if failed_stage:
                    err = f"Verification failed at stage '{failed_stage['name']}':\n{truncate_error_output(failed_stage['output'])}"
                    emit("verify_fail", stage=failed_stage["name"],
                         output=truncate_error_output(failed_stage["output"]))
                else:
                    err = f"Verification confidence is '{res['confidence']}' (not full). Auto-apply rejected."
                    emit("verify_fail", stage="confidence",
                         output=f"confidence={res['confidence']} (не full) — авто-применение отклонено")

                if err == last_error:
                    break
                last_error = err

        # Если цикл завершился без успеха
        status = "refused" if is_regression_reject else "failed"
        return False, f"Failed to produce a verified patch. Last error: {last_error}", total_usage, status

    finally:
        if sandbox is not None:
            clean_sandbox_dir(sandbox)
