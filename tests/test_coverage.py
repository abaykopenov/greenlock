"""Юнит-тесты честного покрытия (WS-1): stmt_line_map / coverage_verdict + парсер дифа."""
from greenlock.coverage import coverage_verdict, stmt_line_map
from greenlock.gate import _changed_lines

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
