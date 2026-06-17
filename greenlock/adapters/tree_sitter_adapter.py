"""adapters.tree_sitter_adapter — адаптер на основе tree-sitter для широкой мультиязычной поддержки.

Извлекает символы с точными спанами (начало/конец тела) для Go, Rust, Java, C++ и других языков,
заменяя грубый regex-fallback при наличии установленной библиотеки tree-sitter.

Совместим с двумя вариантами биндинга:
  - method-based (tree-sitter 0.25 + tree-sitter-language-pack): node.kind(), root_node(), …
  - property-based (классический py-tree-sitter): node.type, root_node, …
через шим _v (свойство-или-метод).
"""
import logging
from pathlib import Path
from greenlock.adapters import ParseResult

logger = logging.getLogger(__name__)

try:
    import tree_sitter  # noqa: F401
    try:
        import tree_sitter_language_pack as _ts_langs  # maintained, есть колёса 3.14
    except ImportError:
        import tree_sitter_languages as _ts_langs       # устаревший фолбэк
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False
    _ts_langs = None


def _v(x):
    """Свойство-или-метод: x() если вызываемо, иначе x."""
    return x() if callable(x) else x


def _kind(n) -> str:
    return _v(n.kind) if hasattr(n, "kind") else _v(n.type)


def _row(pos) -> int:
    pos = _v(pos)
    if isinstance(pos, (tuple, list)):
        return pos[0]
    return getattr(pos, "row", 0)


def _start_row(n) -> int:
    return _row(n.start_position if hasattr(n, "start_position") else n.start_point)


def _end_row(n) -> int:
    return _row(n.end_position if hasattr(n, "end_position") else n.end_point)


def _children(n) -> list:
    return [n.child(i) for i in range(_v(n.child_count))]


def _named_children(n) -> list:
    return [n.named_child(i) for i in range(_v(n.named_child_count))]


class TreeSitterAdapter:
    """Адаптер на основе tree-sitter для широкой языковой поддержки (Go, Rust, Java, C++ и др.)."""

    name = "tree-sitter"
    extensions = {
        ".go", ".rs", ".java", ".cpp", ".c", ".h", ".cs", ".rb", ".php"
    }

    # Маппинг расширений файлов на имена языков в tree-sitter-languages/-language-pack
    LANG_MAP = {
        ".go": "go", ".rs": "rust", ".java": "java", ".cpp": "cpp",
        ".c": "c", ".h": "c", ".cs": "csharp", ".rb": "ruby", ".php": "php",
    }

    def __init__(self):
        self.parsers = {}
        if HAS_TREE_SITTER:
            for ext, lang_name in self.LANG_MAP.items():
                try:
                    self.parsers[ext] = _ts_langs.get_parser(lang_name)
                except Exception as e:
                    logger.debug(f"Failed to load tree-sitter parser for {lang_name}: {e}")

    def parse(self, rel: str, text: str) -> ParseResult:
        if not HAS_TREE_SITTER:
            return ParseResult()

        suffix = Path(rel).suffix
        parser = self.parsers.get(suffix)
        if not parser:
            return ParseResult()

        text_bytes = text.encode("utf-8")
        try:
            # method-based биндинг хочет str, классический — bytes
            try:
                tree = parser.parse(text)
            except TypeError:
                tree = parser.parse(text_bytes)
        except Exception as e:
            logger.debug(f"tree-sitter parse failed for {rel}: {e}")
            return ParseResult()

        symbols = []

        def _name(node) -> str:
            return text_bytes[_v(node.start_byte):_v(node.end_byte)].decode("utf-8", errors="replace")

        def _add(name_node, span_node, kind):
            if name_node is None:
                return
            symbols.append({
                "name": _name(name_node), "kind": kind, "file": rel,
                "line": _start_row(name_node) + 1,
                "span_start": _start_row(span_node) + 1,
                "span_end": _end_row(span_node) + 1,
            })

        def traverse(node):
            nt = _kind(node)

            if suffix == ".go":
                if nt in ("function_declaration", "method_declaration"):
                    _add(node.child_by_field_name("name"), node,
                         "func" if nt == "function_declaration" else "method")
                elif nt == "type_declaration":
                    spec = node.child_by_field_name("name")
                    if spec is None:
                        kids = _named_children(node)
                        spec = kids[0] if kids else None
                    if spec is not None:
                        nm = spec.child_by_field_name("name") or spec
                        _add(nm, node, "type")

            elif suffix == ".rs":
                if nt == "function_item":
                    _add(node.child_by_field_name("name"), node, "func")
                elif nt in ("struct_item", "enum_item", "trait_item", "impl_item"):
                    nm = node.child_by_field_name("name")
                    kind = "type" if nt == "trait_item" else "class"
                    if nm is not None:
                        _add(nm, node, kind)

            elif suffix == ".java":
                if nt in ("class_declaration", "interface_declaration", "enum_declaration"):
                    _add(node.child_by_field_name("name"), node, "class")
                elif nt == "method_declaration":
                    _add(node.child_by_field_name("name"), node, "method")

            elif suffix in (".cpp", ".c", ".h"):
                if nt in ("class_specifier", "struct_specifier", "enum_specifier"):
                    _add(node.child_by_field_name("name"), node, "class")
                elif nt == "function_definition":
                    decl = node.child_by_field_name("declarator")
                    nm = None
                    seen = 0
                    while decl is not None and seen < 20:
                        seen += 1
                        if _kind(decl) in ("identifier", "field_identifier"):
                            nm = decl
                            break
                        nxt = None
                        for ch in _children(decl):
                            if _kind(ch) in ("identifier", "field_identifier", "function_declarator",
                                             "pointer_declarator", "reference_declarator", "qualified_identifier"):
                                nxt = ch
                                break
                        decl = nxt
                    _add(nm, node, "func")

            elif suffix == ".rb":
                if nt in ("class", "module"):
                    _add(node.child_by_field_name("name"), node, "class")
                elif nt in ("method", "singleton_method"):
                    _add(node.child_by_field_name("name"), node, "method")

            elif suffix == ".php":
                if nt in ("class_declaration", "interface_declaration", "trait_declaration"):
                    _add(node.child_by_field_name("name"), node, "class")
                elif nt in ("function_definition", "method_declaration"):
                    _add(node.child_by_field_name("name"), node, "func")

            for ch in _children(node):
                traverse(ch)

        root = _v(tree.root_node)
        traverse(root)
        # refs/imports/defined пока не извлекаются для tree-sitter-языков:
        # closed_world деградирует до oracle-only (безопасно), dep-closure не видит кросс-файл.
        return ParseResult(symbols=symbols, imports=[], refs=[], defined=[])
