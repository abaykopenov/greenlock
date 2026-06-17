"""adapters.python_adapter — точный AST-анализатор для Python.

Извлекает символы с точными спанами (включая декораторы), импорты и Name-ссылки.
"""
import ast

from greenlock.adapters import ParseResult

__all__ = ["PythonAdapter"]


class PythonASTVisitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.symbols = []
        self.imports = []
        self.refs = []
        self.defined = set()
        self._class_stack = []
        self._function_stack = []

    def visit_ClassDef(self, node):
        self.defined.add(node.name)
        kind = "class"
        span_start = node.lineno
        if node.decorator_list:
            span_start = node.decorator_list[0].lineno
        span_end = getattr(node, "end_lineno", node.lineno)
        self.symbols.append({
            "name": node.name,
            "kind": kind,
            "file": self.rel,
            "line": node.lineno,
            "span_start": span_start,
            "span_end": span_end,
        })
        self._class_stack.append(node)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node):
        self.defined.add(node.name)
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node):
        self.defined.add(node.name)
        self._visit_func(node)

    def _visit_func(self, node):
        kind = "method" if self._class_stack else "func"
        span_start = node.lineno
        if node.decorator_list:
            span_start = node.decorator_list[0].lineno
        span_end = getattr(node, "end_lineno", node.lineno)
        self.symbols.append({
            "name": node.name,
            "kind": kind,
            "file": self.rel,
            "line": node.lineno,
            "span_start": span_start,
            "span_end": span_end,
        })
        self._function_stack.append(node)
        self.generic_visit(node)
        self._function_stack.pop()

    def visit_Assign(self, node):
        # Переменные верхнего уровня (константы)
        if not self._class_stack and not self._function_stack:
            for target in node.targets:
                for name in self._get_names(target):
                    span_start = node.lineno
                    span_end = getattr(node, "end_lineno", node.lineno)
                    self.symbols.append({
                        "name": name,
                        "kind": "const",
                        "file": self.rel,
                        "line": node.lineno,
                        "span_start": span_start,
                        "span_end": span_end,
                    })
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # Аннотированные переменные верхнего уровня (константы)
        if not self._class_stack and not self._function_stack:
            for name in self._get_names(node.target):
                span_start = node.lineno
                span_end = getattr(node, "end_lineno", node.lineno)
                self.symbols.append({
                    "name": name,
                    "kind": "const",
                    "file": self.rel,
                    "line": node.lineno,
                    "span_start": span_start,
                    "span_end": span_end,
                })
        self.generic_visit(node)

    def visit_arg(self, node):
        self.defined.add(node.arg)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.defined.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node):
        # Имя, ВВОДИМОЕ в неймспейс: алиас, иначе модуль (closed_world сам срежет
        # точечный путь до верхнего пакета). `import a.b as c` → c; `import a.b` → a.b→a.
        names = [alias.asname or alias.name for alias in node.names]
        self.imports.append({
            "module": None,
            "names": names,
            "file": self.rel,
            "line": node.lineno,
        })
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # `from m import x as y` → y; `from m import x` → x; `from m import *` → *.
        names = [alias.asname or alias.name for alias in node.names]
        self.imports.append({
            "module": node.module,
            "names": names,
            "file": self.rel,
            "line": node.lineno,
        })
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Store):
            self.defined.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            self.refs.append({
                "name": node.id,
                "file": self.rel,
                "line": node.lineno,
            })
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            if isinstance(node.ctx, ast.Store):
                self.defined.add(node.attr)
        self.generic_visit(node)

    def _get_names(self, node):
        if isinstance(node, ast.Name):
            return [node.id]
        elif isinstance(node, (ast.Tuple, ast.List)):
            names = []
            for elt in node.elts:
                names.extend(self._get_names(elt))
            return names
        return []


class PythonAdapter:
    name = "python-ast"
    extensions = {".py"}

    def parse(self, rel: str, text: str) -> ParseResult:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return ParseResult()

        visitor = PythonASTVisitor(rel)
        visitor.visit(tree)
        return ParseResult(
            symbols=visitor.symbols,
            imports=visitor.imports,
            refs=visitor.refs,
            defined=list(visitor.defined)
        )
