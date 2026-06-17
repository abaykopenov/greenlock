"""Model-free тесты testgen: резолвинг import-root/module и эмиссия тестов.

Не требуют Ollama (LLM-часть testgen здесь не вызывается) — гоняются в CI.
"""
from pathlib import Path

from greenlock.testgen import _import_target, _emit_tests, _STAR_IMPORT


def _mkfile(root: Path, rel: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x = 1\n", encoding="utf-8")


def test_import_target_flat(tmp_path):
    _mkfile(tmp_path, "pricing.py")
    assert _import_target(tmp_path, "pricing.py") == (".", "pricing")


def test_import_target_nested_package(tmp_path):
    _mkfile(tmp_path, "pkg/__init__.py")
    _mkfile(tmp_path, "pkg/sub/__init__.py")
    _mkfile(tmp_path, "pkg/sub/mod.py")
    assert _import_target(tmp_path, "pkg/sub/mod.py") == (".", "pkg.sub.mod")


def test_import_target_src_layout(tmp_path):
    _mkfile(tmp_path, "src/shop/__init__.py")
    _mkfile(tmp_path, "src/shop/cart.py")
    # src/ без __init__.py → это import-root, модуль без префикса src
    assert _import_target(tmp_path, "src/shop/cart.py") == ("src", "shop.cart")


def test_emit_strips_indented_star_import():
    # 'from x import *' внутри функции — SyntaxError; должно отфильтроваться
    kept = [{"setup": "from shop.cart import *\nc = Cart()", "expr": "c.total()",
             "repr": "0"}]
    src = _emit_tests("shop.cart", kept)
    assert "    from shop.cart import *" not in src      # не внутри функции
    assert src.count("from shop.cart import *") == 1      # только модульный, в шапке
    compile(src, "<gen>", "exec")                          # синтаксически валиден


def test_star_import_regex():
    assert _STAR_IMPORT.match("from a.b import *")
    assert _STAR_IMPORT.match("  from a import *")
    assert not _STAR_IMPORT.match("from a import name")
