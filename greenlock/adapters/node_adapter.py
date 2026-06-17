"""adapters.node_adapter — точный лексический анализатор для JavaScript.

Извлекает символы с точными спанами (балансировка фигурных скобок), импорты,
Name-ссылки и локально определенные имена.
"""
from pathlib import Path
from greenlock.adapters import ParseResult

__all__ = ["NodeAdapter"]

JS_KEYWORDS = {
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "export", "extends", "finally",
    "for", "function", "if", "import", "in", "instanceof", "new", "return",
    "super", "switch", "this", "throw", "try", "typeof", "var", "void",
    "while", "with", "yield", "let", "package", "private", "protected",
    "public", "static", "yield", "async", "await", "null", "true", "false",
    "undefined", "from", "require", "as", "of", "get", "set"
}


def tokenize_js(text: str) -> list[dict]:
    tokens = []
    i = 0
    n = len(text)
    line = 1

    while i < n:
        c = text[i]

        if c == '\n':
            line += 1
            i += 1
            continue

        if c.isspace():
            i += 1
            continue

        # Line comment
        if c == '/' and i + 1 < n and text[i+1] == '/':
            i += 2
            while i < n and text[i] != '\n':
                i += 1
            continue

        # Block comment
        if c == '/' and i + 1 < n and text[i+1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i+1] == '/'):
                if text[i] == '\n':
                    line += 1
                i += 1
            i += 2
            continue

        # String literals (quotes, backticks)
        if c in ("'", '"', '`'):
            quote = c
            start_line = line
            val = [c]
            i += 1
            while i < n:
                cc = text[i]
                val.append(cc)
                if cc == '\n':
                    line += 1
                if cc == '\\' and i + 1 < n:
                    val.append(text[i+1])
                    if text[i+1] == '\n':
                        line += 1
                    i += 2
                    continue
                if cc == quote:
                    i += 1
                    break
                i += 1
            tokens.append({"type": "LITERAL", "value": "".join(val), "line": start_line})
            continue

        # Identifiers
        if c.isalpha() or c == '_' or c == '$':
            start_line = line
            val = [c]
            i += 1
            while i < n and (text[i].isalnum() or text[i] == '_' or text[i] == '$'):
                val.append(text[i])
                i += 1
            name = "".join(val)
            tokens.append({"type": "NAME", "value": name, "line": start_line})
            continue

        # Numbers
        if c.isdigit():
            start_line = line
            val = [c]
            i += 1
            while i < n and (text[i].isdigit() or text[i] == '.'):
                val.append(text[i])
                i += 1
            tokens.append({"type": "NUMBER", "value": "".join(val), "line": start_line})
            continue

        # Punctuators
        tokens.append({"type": "PUNCT", "value": c, "line": line})
        i += 1

    return tokens


def _collect_arrow_params(tokens: list[dict]) -> set:
    """Параметры стрелочных функций — это локальные имена: (a, b) => ... и x => ...

    Лексер не моделирует области видимости, поэтому собираем их отдельным проходом,
    иначе closed-world ложно ругается на параметры колбэков (acc, it и т.п.).
    """
    params = set()
    for k in range(1, len(tokens)):
        t = tokens[k]
        if t["type"] == "PUNCT" and t["value"] == ">" and \
                tokens[k - 1]["type"] == "PUNCT" and tokens[k - 1]["value"] == "=":
            p = k - 2  # токен перед '=>'
            if p >= 0 and tokens[p]["type"] == "PUNCT" and tokens[p]["value"] == ")":
                depth, q = 0, p
                while q >= 0:
                    tv = tokens[q]
                    if tv["type"] == "PUNCT" and tv["value"] == ")":
                        depth += 1
                    elif tv["type"] == "PUNCT" and tv["value"] == "(":
                        depth -= 1
                        if depth == 0:
                            break
                    q -= 1
                for r in range(q + 1, p):
                    if tokens[r]["type"] == "NAME" and tokens[r]["value"] not in JS_KEYWORDS:
                        params.add(tokens[r]["value"])
            elif p >= 0 and tokens[p]["type"] == "NAME" and tokens[p]["value"] not in JS_KEYWORDS:
                params.add(tokens[p]["value"])
    return params


class NodeAdapter:
    name = "node-ast"
    extensions = {".js"}
    # Глобалы/builtins JavaScript — для closed-world (аналог dir(builtins) в Python).
    builtins = frozenset({
        "Math", "Number", "String", "Boolean", "Object", "Array", "JSON", "Date",
        "RegExp", "Map", "Set", "WeakMap", "WeakSet", "Symbol", "Promise", "Error",
        "TypeError", "RangeError", "SyntaxError", "console", "parseInt", "parseFloat",
        "isNaN", "isFinite", "NaN", "Infinity", "undefined", "globalThis", "Reflect",
        "Proxy", "BigInt", "Intl", "structuredClone", "queueMicrotask", "setTimeout",
        "setInterval", "clearTimeout", "clearInterval", "process", "Buffer", "module",
        "exports", "require", "encodeURIComponent", "decodeURIComponent", "globalThis",
    })

    def parse(self, rel: str, text: str) -> ParseResult:
        try:
            tokens = tokenize_js(text)
        except Exception:
            return ParseResult()

        symbols = []
        imports = []
        refs = []
        defined = set()

        class_stack = []
        function_stack = []
        brace_depth = 0

        # Вспомогательный словарь для быстрого поиска символа по (name, kind)
        symbol_map = {}

        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]

            # 1. Сбор imports (ES6 & CommonJS)
            if tok["type"] == "NAME" and tok["value"] == "import":
                # import { a, b } from 'mod'
                # Ищем from
                j = i + 1
                names = []
                module_name = None
                while j < n and not (tokens[j]["type"] == "NAME" and tokens[j]["value"] == "from"):
                    if tokens[j]["type"] == "NAME" and tokens[j]["value"] not in JS_KEYWORDS:
                        names.append(tokens[j]["value"])
                    j += 1
                if j + 1 < n and tokens[j+1]["type"] == "LITERAL":
                    module_name = tokens[j+1]["value"].strip("'\"` ")
                if module_name:
                    imports.append({
                        "module": module_name,
                        "names": names,
                        "file": rel,
                        "line": tok["line"]
                    })
                    for name in names:
                        defined.add(name)

            elif tok["type"] == "NAME" and tok["value"] == "require":
                # const x = require('mod')
                if i + 2 < n and tokens[i+1]["type"] == "PUNCT" and tokens[i+1]["value"] == "(" \
                        and tokens[i+2]["type"] == "LITERAL":
                    module_name = tokens[i+2]["value"].strip("'\"` ")
                    # Ищем backward для переменных
                    j = i - 1
                    names = []
                    while j >= 0 and tokens[j]["type"] == "PUNCT" and tokens[j]["value"] in ("=", " ", "{", "}", ","):
                        j -= 1
                    while j >= 0 and tokens[j]["type"] == "NAME" and tokens[j]["value"] not in ("const", "let", "var"):
                        if tokens[j]["value"] not in JS_KEYWORDS:
                            names.append(tokens[j]["value"])
                        j -= 1
                    if module_name:
                        imports.append({
                            "module": module_name,
                            "names": names,
                            "file": rel,
                            "line": tok["line"]
                        })
                        for name in names:
                            defined.add(name)

            # 2. Определение классов
            if tok["type"] == "NAME" and tok["value"] == "class":
                if i + 1 < n and tokens[i+1]["type"] == "NAME":
                    class_name = tokens[i+1]["value"]
                    defined.add(class_name)
                    # Ищем opening brace {
                    j = i + 2
                    while j < n and not (tokens[j]["type"] == "PUNCT" and tokens[j]["value"] == "{"):
                        j += 1
                    if j < n:
                        # Записываем класс
                        sym = {
                            "name": class_name,
                            "kind": "class",
                            "file": rel,
                            "line": tok["line"],
                            "span_start": tok["line"],
                            "span_end": None
                        }
                        symbols.append(sym)
                        symbol_map[(class_name, "class")] = sym
                        class_stack.append({
                            "name": class_name,
                            "start_line": tok["line"],
                            "start_brace_depth": brace_depth
                        })

            # 3. Определение функций (стандартные и async)
            elif tok["type"] == "NAME" and tok["value"] == "function":
                # function name(...) {
                if i + 1 < n and tokens[i+1]["type"] == "NAME":
                    func_name = tokens[i+1]["value"]
                    defined.add(func_name)
                    kind = "method" if class_stack else "func"
                    j = i + 2
                    while j < n and not (tokens[j]["type"] == "PUNCT" and tokens[j]["value"] == "{"):
                        # Добавляем параметры функции в defined
                        if tokens[j]["type"] == "NAME" and tokens[j]["value"] not in JS_KEYWORDS:
                            defined.add(tokens[j]["value"])
                        j += 1
                    if j < n:
                        start_line = tok["line"]
                        # Проверяем, был ли async перед function
                        if i > 0 and tokens[i-1]["type"] == "NAME" and tokens[i-1]["value"] == "async":
                            start_line = tokens[i-1]["line"]
                        sym = {
                            "name": func_name,
                            "kind": kind,
                            "file": rel,
                            "line": start_line,
                            "span_start": start_line,
                            "span_end": None
                        }
                        symbols.append(sym)
                        symbol_map[(func_name, kind)] = sym
                        function_stack.append({
                            "name": func_name,
                            "kind": kind,
                            "start_brace_depth": brace_depth
                        })

            # 4. Методы внутри классов (без ключевого слова function, например: item_count() { ... })
            elif class_stack and brace_depth == class_stack[-1]["start_brace_depth"] + 1 \
                    and tok["type"] == "NAME" and tok["value"] not in JS_KEYWORDS:
                # name(...) {
                # Проверим, что дальше идет '('
                if i + 1 < n and tokens[i+1]["type"] == "PUNCT" and tokens[i+1]["value"] == "(":
                    method_name = tok["value"]
                    defined.add(method_name)
                    # Ищем opening brace {
                    j = i + 2
                    while j < n and not (tokens[j]["type"] == "PUNCT" and tokens[j]["value"] == "{"):
                        if tokens[j]["type"] == "NAME" and tokens[j]["value"] not in JS_KEYWORDS:
                            defined.add(tokens[j]["value"])
                        j += 1
                    if j < n:
                        start_line = tok["line"]
                        if i > 0 and tokens[i-1]["type"] == "NAME" and tokens[i-1]["value"] == "async":
                            start_line = tokens[i-1]["line"]
                        sym = {
                            "name": method_name,
                            "kind": "method",
                            "file": rel,
                            "line": start_line,
                            "span_start": start_line,
                            "span_end": None
                        }
                        symbols.append(sym)
                        symbol_map[(method_name, "method")] = sym
                        function_stack.append({
                            "name": method_name,
                            "kind": "method",
                            "start_brace_depth": brace_depth
                        })

            # 5. Стрелочные функции/константы: const name = (...) => { ... }
            elif tok["type"] == "NAME" and tok["value"] in ("const", "let", "var"):
                if i + 3 < n and tokens[i+1]["type"] == "NAME" and tokens[i+2]["type"] == "PUNCT" \
                        and tokens[i+2]["value"] == "=":
                    var_name = tokens[i+1]["value"]
                    defined.add(var_name)
                    # Ищем => {
                    j = i + 3
                    is_arrow = False
                    while j < n and not (tokens[j]["type"] == "PUNCT" and tokens[j]["value"] in (";", "\n")):
                        if tokens[j]["type"] == "PUNCT" and tokens[j]["value"] == "{" and j > 0 \
                                and tokens[j-1]["type"] == "PUNCT" and tokens[j-1]["value"] == ">":
                            # Нашли => {
                            is_arrow = True
                            break
                        j += 1
                    if is_arrow:
                        # Это стрелочная функция!
                        kind = "method" if class_stack else "func"
                        sym = {
                            "name": var_name,
                            "kind": kind,
                            "file": rel,
                            "line": tok["line"],
                            "span_start": tok["line"],
                            "span_end": None
                        }
                        symbols.append(sym)
                        symbol_map[(var_name, kind)] = sym
                        function_stack.append({
                            "name": var_name,
                            "kind": kind,
                            "start_brace_depth": brace_depth
                        })
                    else:
                        # Обычная константа/переменная верхнего уровня
                        if not class_stack and not function_stack:
                            symbols.append({
                                "name": var_name,
                                "kind": "const",
                                "file": rel,
                                "line": tok["line"],
                                "span_start": tok["line"],
                                "span_end": tok["line"]
                            })

            # 6. Отслеживание brace_depth и закрытия спанов
            if tok["type"] == "PUNCT":
                if tok["value"] == "{":
                    brace_depth += 1
                elif tok["value"] == "}":
                    brace_depth -= 1

                    # Закрытие функции/метода
                    if function_stack and brace_depth == function_stack[-1]["start_brace_depth"]:
                        popped = function_stack.pop()
                        # Обновляем span_end для этой функции
                        sym = symbol_map.get((popped["name"], popped["kind"]))
                        if sym:
                            sym["span_end"] = tok["line"]

                    # Закрытие класса
                    if class_stack and brace_depth == class_stack[-1]["start_brace_depth"]:
                        popped = class_stack.pop()
                        sym = symbol_map.get((popped["name"], "class"))
                        if sym:
                            sym["span_end"] = tok["line"]

            # 7. Сбор Name-ссылок (refs). Член-доступ (obj.prop) — НЕ свободное имя:
            # пропускаем имя, если перед ним точка (иначе this._items даёт ложный ref).
            if tok["type"] == "NAME" and tok["value"] not in JS_KEYWORDS:
                prev = tokens[i - 1] if i > 0 else None
                is_member = bool(prev and prev["type"] == "PUNCT" and prev["value"] == ".")
                if not is_member:
                    refs.append({
                        "name": tok["value"],
                        "file": rel,
                        "line": tok["line"]
                    })

            i += 1

        # Параметры стрелочных функций — локальные имена (лексер их не отследил).
        defined |= _collect_arrow_params(tokens)

        return ParseResult(
            symbols=symbols,
            imports=imports,
            refs=refs,
            defined=list(defined)
        )
