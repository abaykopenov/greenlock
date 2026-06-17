import pytest
from greenlock.adapters import detect_adapters
from greenlock.adapters.tree_sitter_adapter import HAS_TREE_SITTER, TreeSitterAdapter

# Sample code for testing
GO_CODE = """package main

import "fmt"

type User struct {
    Name string
}

func (u *User) GetName() string {
    return u.Name
}

func main() {
    fmt.Println("Hello")
}
"""

RUST_CODE = """struct Item {
    id: u32,
}

impl Item {
    fn new(id: u32) -> Self {
        Item { id }
    }
}

fn process() {
    println!("processing");
}
"""

JAVA_CODE = """public class Calculator {
    private int value;

    public int add(int x) {
        return value + x;
    }
}
"""

CPP_CODE = """#include <iostream>

class Box {
public:
    double length;
};

void show() {
    std::cout << "box" << std::endl;
}
"""


@pytest.mark.skipif(not HAS_TREE_SITTER, reason="tree-sitter or tree-sitter-languages not installed")
def test_tree_sitter_parsing():
    adapter = TreeSitterAdapter()
    
    # 1. Test Go
    res_go = adapter.parse("main.go", GO_CODE)
    syms_go = {s["name"]: s for s in res_go.symbols}
    assert "User" in syms_go
    assert syms_go["User"]["kind"] == "type"
    assert "GetName" in syms_go
    assert syms_go["GetName"]["kind"] == "method"
    assert syms_go["GetName"]["span_start"] == 9
    assert syms_go["GetName"]["span_end"] == 11
    assert "main" in syms_go
    assert syms_go["main"]["kind"] == "func"

    # 2. Test Rust
    res_rs = adapter.parse("lib.rs", RUST_CODE)
    syms_rs = {s["name"]: s for s in res_rs.symbols}
    assert "Item" in syms_rs
    assert syms_rs["Item"]["kind"] == "class"
    assert "process" in syms_rs
    assert syms_rs["process"]["kind"] == "func"

    # 3. Test Java
    res_java = adapter.parse("Calculator.java", JAVA_CODE)
    syms_java = {s["name"]: s for s in res_java.symbols}
    assert "Calculator" in syms_java
    assert syms_java["Calculator"]["kind"] == "class"
    assert "add" in syms_java
    assert syms_java["add"]["kind"] == "method"

    # 4. Test C++
    res_cpp = adapter.parse("main.cpp", CPP_CODE)
    syms_cpp = {s["name"]: s for s in res_cpp.symbols}
    assert "Box" in syms_cpp
    assert syms_cpp["Box"]["kind"] == "class"
    assert "show" in syms_cpp
    assert syms_cpp["show"]["kind"] == "func"
