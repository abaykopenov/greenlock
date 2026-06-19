"""Юнит-тесты честного покрытия (WS-1): stmt_line_map / coverage_verdict + парсер дифа."""
import pytest

from greenlock.coverage import code_changed_lines, coverage_verdict, stmt_line_map
from greenlock.gate import _changed_lines


def _has_ts() -> bool:
    try:
        from greenlock.adapters.tree_sitter_adapter import HAS_TREE_SITTER
        return HAS_TREE_SITTER
    except Exception:
        return False

SRC = (
    "def foo(x):\n"          # 1  заголовок def — пропускается
    "    y = x + 1\n"        # 2  тело
    "    return y\n"         # 3  тело
    "\n"                     # 4
    "def bar(x):\n"          # 5  заголовок def — пропускается
    "    return x - 1\n"     # 6  тело
)


def test_stmt_map_skips_def_headers_and_blanks():
    m = stmt_line_map(SRC)
    assert 1 not in m and 5 not in m   # заголовки def исполняются при импорте — не покрытие
    assert 2 in m and 3 in m and 6 in m
    assert 4 not in m                  # пустая строка


def test_verdict_covered_when_changed_line_executed():
    has_code, covered = coverage_verdict(SRC, {2}, {2})
    assert has_code and covered


def test_verdict_uncovered_when_changed_body_not_executed():
    # изменено тело bar (стр.6), но исполнено только тело foo → не покрыто
    has_code, covered = coverage_verdict(SRC, {6}, {2, 3})
    assert has_code and not covered


def test_verdict_comment_only_is_not_code():
    src = "x = 1\n# просто комментарий\n"
    has_code, covered = coverage_verdict(src, {2}, set())
    assert not has_code and covered     # не код → покрытие не требуется


def test_changed_lines_parses_added_new_side():
    diff = ("--- a/f.py\n+++ b/f.py\n"
            "@@ -1,2 +1,3 @@\n ctx\n+added_a\n+added_b\n ctx2\n")
    assert _changed_lines(diff)["f.py"] == {2, 3}


def test_v8_parser_distinguishes_covered_from_uncovered(tmp_path):
    """V8-парсер: строка тела вызванной функции — covered; тело невызванной — нет
    (самый узкий охватывающий range с count=0). Без node — синтетические данные."""
    import json
    from greenlock.coverage import v8_executed_changed_lines

    src = "function a(){\n  return 1;\n}\nfunction b(){\n  return 2;\n}\n"
    f = tmp_path / "m.js"
    f.write_text(src, encoding="utf-8")
    a_s, a_e = src.index("function a"), src.index("}\n") + 1
    b_s, b_e = src.index("function b"), len(src)
    cov = {"result": [{"url": f.as_uri(), "functions": [
        {"ranges": [{"startOffset": 0, "endOffset": len(src), "count": 1}]},
        {"ranges": [{"startOffset": a_s, "endOffset": a_e, "count": 1}]},  # a вызвана
        {"ranges": [{"startOffset": b_s, "endOffset": b_e, "count": 0}]},  # b НЕ вызвана
    ]}]}
    covdir = tmp_path / "cov"
    covdir.mkdir()
    (covdir / "c.json").write_text(json.dumps(cov), encoding="utf-8")

    measured, ex = v8_executed_changed_lines(str(covdir), str(f), {2, 5})
    assert measured
    assert 2 in ex and 5 not in ex


def test_go_coverprofile_parser():
    """Go coverprofile: блок count>0 → строки исполнены; count=0 → нет."""
    from greenlock.coverage import go_cover_executed_lines
    prof = ("mode: set\n"
            "example.com/m/pkg/foo.go:10.2,12.16 2 1\n"   # исполнено 10–12
            "example.com/m/pkg/foo.go:20.2,21.3 1 0\n")   # НЕ исполнено 20–21
    cov = go_cover_executed_lines(prof)
    name = "example.com/m/pkg/foo.go"
    assert cov[name] == {10, 11, 12}
    assert 20 not in cov[name] and 21 not in cov[name]


def test_lcov_parser():
    """LCOV (cargo-llvm-cov): DA:line,count с count>0 → исполнено."""
    from greenlock.coverage import lcov_executed_lines
    text = ("SF:/abs/src/lib.rs\nDA:3,1\nDA:4,0\nDA:5,2\nend_of_record\n"
            "SF:/abs/src/other.rs\nDA:1,0\nend_of_record\n")
    cov = lcov_executed_lines(text)
    assert cov["/abs/src/lib.rs"] == {3, 5}
    assert cov["/abs/src/other.rs"] == set()


def test_code_changed_lines_fallback_excludes_noncode():
    """Без tree-sitter (неизвестный ext) — эвристика: только тело-код требует покрытия."""
    src = "x = 1\n// comment\n\n   \n}\n"
    assert code_changed_lines(src, ".unknownlang", {1, 2, 3, 4, 5}) == {1}


@pytest.mark.skipif(not _has_ts(), reason="нужен tree-sitter")
def test_code_changed_lines_ts_parity_js_go_rust():
    """Паритет с Python-AST: комментарии/import/сигнатуры/скобки НЕ требуют покрытия,
    тело функции — требует (точно, через tree-sitter)."""
    js = "// c\nimport x from 'y';\nfunction f(a) {\n  return a + 1;\n}\n"
    assert code_changed_lines(js, ".js", {1, 2, 3, 4, 5}) == {4}
    go = "package m\n// c\nimport \"fmt\"\nfunc F(a int) int {\n\treturn a + 1\n}\n"
    assert code_changed_lines(go, ".go", {1, 2, 3, 4, 5, 6}) == {5}
    rs = "// c\nuse std::fmt;\nfn f(a: i32) -> i32 {\n    a + 1\n}\n"
    assert code_changed_lines(rs, ".rs", {1, 2, 3, 4, 5}) == {4}
