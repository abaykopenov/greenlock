"""greenlock.coverage — честный сигнал покрытия ИЗМЕНЁННЫХ строк (только stdlib).

Гейт обязан ставить confidence="full" лишь если изменение реально исполняется
тестами. Иначе «зелёный сет» ничего не говорит про сам патч (DESIGN §6: честная
деградация, а не ложный MERGE). Меряем исполнение через `sys.settrace` —
без внешних зависимостей (принцип stdlib-first).

Сопоставление идёт на уровне ОПЕРАТОРОВ (ast.stmt), а не сырых строк: трассировщик
срабатывает на «головной» строке оператора, а патч может менять его продолжение —
поэтому и изменённые, и исполненные строки сворачиваем к идентификатору оператора.
"""
import ast
import os

__all__ = ["stmt_line_map", "coverage_verdict", "run_pytest_traced",
           "v8_executed_changed_lines", "go_cover_executed_lines",
           "lcov_executed_lines"]


def go_cover_executed_lines(profile_text: str) -> dict[str, set[int]]:
    """Go coverprofile (`go test -coverprofile`) → {имя_в_профиле: set(исполненных строк)}.

    Строка профиля: `import/path/file.go:sl.sc,el.ec numStmts count`. Блок с count>0
    помечает строки sl..el исполненными. Имя — import-path + файл (НЕ fs-rel),
    сопоставление с изменённым файлом — по суффиксу на стороне вызывающего.
    """
    result: dict[str, set[int]] = {}
    for raw in profile_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("mode:"):
            continue
        try:
            name, rest = line.split(":", 1)
            block, _nstmt, count = rest.rsplit(" ", 2)
            count = int(count)
            start, end = block.split(",")
            sl = int(start.split(".")[0])
            el = int(end.split(".")[0])
        except (ValueError, IndexError):
            continue
        if count > 0 and 1 <= sl <= el:
            result.setdefault(name, set()).update(range(sl, el + 1))
    return result


def lcov_executed_lines(lcov_text: str) -> dict[str, set[int]]:
    """LCOV (напр. `cargo-llvm-cov --lcov`) → {путь_файла: set(исполненных строк)}.

    `SF:<file>` задаёт текущий файл; `DA:<line>,<count>` с count>0 → строка исполнена.
    """
    result: dict[str, set[int]] = {}
    cur: str | None = None
    for raw in lcov_text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            cur = line[3:]
            result.setdefault(cur, set())
        elif line.startswith("DA:") and cur is not None:
            try:
                ln, cnt = line[3:].split(",")[:2]
                if int(cnt) > 0:
                    result[cur].add(int(ln))
            except ValueError:
                continue
        elif line.startswith("end_of_record"):
            cur = None
    return result


def _line_offsets(src: str) -> list[int]:
    """offs[k] = смещение (в символах) начала строки k+1 (1-based)."""
    offs = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            offs.append(i + 1)
    return offs


def v8_executed_changed_lines(cov_dir: str, target_path: str,
                              changed_lines: set[int]) -> tuple[bool, set[int]]:
    """Какие из changed_lines файла реально исполнились — по покрытию V8 (NODE_V8_COVERAGE).

    Возвращает (measured, executed): measured=False, если для файла нет данных
    покрытия (тогда вызывающий применяет fail-open). Строка считается исполненной,
    если САМЫЙ УЗКИЙ V8-range, её покрывающий, имеет count>0 — так невыполненная
    ветка внутри вызванной функции НЕ засчитывается (без over-report → без ложного MERGE).
    """
    import glob
    import json
    import os

    real = os.path.realpath(target_path)
    try:
        src = open(target_path, encoding="utf-8").read()
    except OSError:
        return (False, set())
    offs = _line_offsets(src)
    src_lines = src.split("\n")

    ranges: list[tuple[int, int, int]] = []
    found = False
    for jf in glob.glob(os.path.join(cov_dir, "*.json")):
        try:
            data = json.loads(open(jf, encoding="utf-8").read())
        except Exception:
            continue
        for entry in data.get("result", []):
            url = entry.get("url", "")
            if url.startswith("file://"):
                from urllib.parse import unquote, urlparse
                p = unquote(urlparse(url).path)   # декодируем %20 и т.п. (пробелы в пути)
            else:
                p = url
            if not p or os.path.realpath(p) != real:
                continue
            found = True
            for fn in entry.get("functions", []):
                for r in fn.get("ranges", []):
                    ranges.append((r["startOffset"], r["endOffset"], r.get("count", 0)))
    if not found:
        return (False, set())

    executed: set[int] = set()
    for ln in changed_lines:
        if ln < 1 or ln > len(src_lines):
            continue
        line = src_lines[ln - 1]
        if not line.strip():
            continue
        off = offs[ln - 1] + (len(line) - len(line.lstrip()))  # первый значимый символ
        best = None  # (size, count) самого узкого охватывающего range
        for s, e, c in ranges:
            if s <= off < e:
                size = e - s
                if best is None or size < best[0]:
                    best = (size, c)
        if best is not None and best[1] > 0:
            executed.add(ln)
    return (True, executed)


def stmt_line_map(source: str) -> dict[int, int]:
    """line → id оператора (начальная строка наименьшего охватывающего ast.stmt).

    Чистые докстринги/строки-выражения пропускаются (поведения не несут), поэтому
    изменение только комментария/докстринга не считается изменением кода.
    """
    by_line: dict[int, tuple[int, int]] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # заголовок def/class исполняется при импорте — это НЕ покрытие тела
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant) \
                and isinstance(node.value.value, str):
            continue  # докстринг / строка-выражение
        start = node.lineno
        end = getattr(node, "end_lineno", start) or start
        span = end - start
        for ln in range(start, end + 1):
            prev = by_line.get(ln)
            if prev is None or span < prev[1]:   # наименьший охватывающий оператор
                by_line[ln] = (start, span)
    return {ln: v[0] for ln, v in by_line.items()}


def coverage_verdict(file_source: str, changed_added: set[int],
                     executed: set[int]) -> tuple[bool, bool]:
    """(has_code_change, covered) для одного файла.

    has_code_change=False → изменены только комментарии/доки/пустые строки: покрытие
    не требуется. Иначе covered=True ⇔ хотя бы один изменённый оператор исполнился.
    """
    line2stmt = stmt_line_map(file_source)
    changed_stmts = {line2stmt[ln] for ln in changed_added if ln in line2stmt}
    if not changed_stmts:
        return (False, True)
    executed_stmts = {line2stmt[ln] for ln in executed if ln in line2stmt}
    return (True, bool(changed_stmts & executed_stmts))


def run_pytest_traced(pytest_args: list[str], targets: list[str]) -> tuple[int, dict[str, list[int]]]:
    """Прогнать pytest IN-PROCESS под трассировкой, вернуть (exit_code, {файл: [строки]}).

    Трассируем только кадры целевых файлов (changed), поэтому накладные расходы
    пропорциональны исполнению патча, а не всего сета. Вызывать из отдельного
    процесса (см. greenlock._covrun) — pytest мутирует глобальное состояние.
    """
    import sys
    import threading

    targets_raw = set(targets)
    targets_real = {os.path.realpath(t) for t in targets}
    hits: dict[str, set[int]] = {}
    cache: dict[str, bool] = {}

    def _loc(frame, event, arg):
        if event == "line":
            hits.setdefault(frame.f_code.co_filename, set()).add(frame.f_lineno)
        return _loc

    def _glob(frame, event, arg):
        if event != "call":
            return None
        fn = frame.f_code.co_filename
        hit = cache.get(fn)
        if hit is None:
            hit = fn in targets_raw or os.path.realpath(fn) in targets_real
            cache[fn] = hit
        return _loc if hit else None

    import pytest
    threading.settrace(_glob)
    sys.settrace(_glob)
    try:
        code = pytest.main(list(pytest_args))
    finally:
        sys.settrace(None)
        threading.settrace(None)

    executed: dict[str, list[int]] = {}
    for fn, lines in hits.items():
        executed[os.path.realpath(fn)] = sorted(lines)
    return int(code), executed
