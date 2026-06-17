"""Closed-world для tree-sitter языков (Go/Rust) — консервативность важнее охвата.

Главная проверка: на легитимном коде (параметры, локальные, замыкания, stdlib
member-вызовы, builtins, кросс-файловые функции) — НОЛЬ ложных срабатываний;
флажится только реально несуществующий bare-вызов. Языки вне whitelist (ruby и др.)
остаются oracle-only (refs пусты).

Скипается, если tree-sitter не установлен.
"""
import textwrap

import pytest

from greenlock.adapters.tree_sitter_adapter import HAS_TREE_SITTER
from greenlock.closed_world import closed_world_check

pytestmark = pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter не установлен")

GO = textwrap.dedent('''
    package main
    import "fmt"
    func add(a int, b int) int { return a + b }
    func main() {
        x := add(1, 2)
        nums := make([]int, 0)
        nums = append(nums, x)
        fmt.Println(nums)
        double := func(n int) int { return n * 2 }
        y := double(x)
        helper(y)
        ghost(y)
    }
''')

RS = textwrap.dedent('''
    use std::mem::swap;
    fn add(a: i32, b: i32) -> i32 { a + b }
    fn main() {
        let mut x = add(1, 2);
        let mut y = 3;
        swap(&mut x, &mut y);
        let f = |n: i32| n * 2;
        let z = f(x);
        println!("{}", z);
        helper(z);
        ghost(z);
    }
''')


def _check(tmp_path, fn, code):
    f = tmp_path / fn
    f.write_text(code, encoding="utf-8")
    # 'helper' играет роль кросс-файловой функции проекта (в индексе символов)
    return closed_world_check(f, {"helper": [(str(f), 1)]})


def test_go_flags_only_undeclared(tmp_path):
    errs = _check(tmp_path, "m.go", GO)
    assert len(errs) == 1 and "ghost" in errs[0], errs


def test_rust_flags_only_undeclared(tmp_path):
    errs = _check(tmp_path, "m.rs", RS)
    assert len(errs) == 1 and "ghost" in errs[0], errs


def test_unlisted_language_stays_oracle_only(tmp_path):
    # ruby не во whitelist closed-world → refs пусты → ноль флагов (без ложняка)
    f = tmp_path / "m.rb"
    f.write_text("def foo\n  bar baz\nend\n", encoding="utf-8")
    assert closed_world_check(f, {}) == []
