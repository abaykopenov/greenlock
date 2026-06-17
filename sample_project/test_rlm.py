"""Unit tests for core.rlm (Phase 2)."""
import os
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from greenlock.rlm import (
    get_symbol_span_hash,
    extract_verified_fields,
    get_or_build_card,
    rlm_cache_path,
    build_rlm_card,
)
from greenlock.index import build_index


def test_get_symbol_span_hash():
    content = "line1\nline2\nline3\nline4\n"
    h = get_symbol_span_hash(content, 2, 3)
    
    import hashlib
    expected = hashlib.md5("line2\nline3".encode("utf-8")).hexdigest()
    assert h == expected

    # Invalid spans
    assert get_symbol_span_hash(content, None, 3) == ""
    assert get_symbol_span_hash(content, 2, None) == ""
    assert get_symbol_span_hash(content, 5, 6) == ""
    assert get_symbol_span_hash(content, 3, 2) == ""


def test_extract_verified_fields_real():
    repo_path = Path("sample_project")
    index = build_index(repo_path)
    
    assert "storage.py" in index["files"]
    
    from greenlock.rlm import find_symbol_in_file
    sym = find_symbol_in_file(index, "storage.py", "add_task")
    assert sym is not None
    assert sym["name"] == "add_task"
    assert sym["kind"] == "method"
    
    fields = extract_verified_fields(index, "storage.py", sym)
    assert fields["signature"] == "def add_task(self, title: str) -> Task:"
    assert fields["params"] == ["title"]
    assert fields["returns"] == "Task"
    assert "storage.py" in fields["location"]
    
    # Check imports
    assert any("Task" in imp for imp in fields["imports"])
    
    # Check callers: add_task is called in cli.py on line 21
    assert "cli.py:21" in fields["callers"]
    
    # Check callees: add_task references Task and calls self._save()
    assert "Task" in fields["callees"]


def test_build_rlm_card():
    # Mock generate to return a specific JSON response
    mock_response = json.dumps({
        "purpose": "Обеспечивает добавление новой задачи с уникальным идентификатором. См. storage.py:25",
        "recipe": "store = TaskStore()\nstore.add_task('Купить молоко')",
        "citations": ["storage.py:25"]
    })
    
    args = SimpleNamespace(
        repo="sample_project",
        model="gemma3:4b",
        escalate="",
        timeout=120
    )
    
    index = {
        "files": {
            "storage.py": "def add_task(self, title: str):\n    pass\n"
        },
        "symbols": {
            "add_task": [("storage.py", 25)]
        }
    }
    
    sym = {
        "name": "add_task",
        "kind": "method",
        "line": 25,
        "span_start": 25,
        "span_end": 30
    }
    
    with patch("core.rlm.generate", return_value=(mock_response, {"prompt": 10, "completion": 5, "total": 15})):
        card = build_rlm_card(args, index, "storage.py", sym)
        assert card["purpose"] == "Обеспечивает добавление новой задачи с уникальным идентификатором. См. storage.py:25"
        assert card["recipe"] == "store = TaskStore()\nstore.add_task('Купить молоко')"
        # Since storage.py in the mocked index has only 1 line, citation verification will fail (25 is out of bounds)
        assert card["citations"] == []


def test_get_or_build_card_caching():
    import shutil
    tmp_path = Path("sample_project") / "tmp_test_dir"
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    
    try:
        file_path = tmp_path / "foo.py"
        file_path.write_text("def my_func():\n    return 42\n", encoding="utf-8")
        
        args = SimpleNamespace(
            repo=str(tmp_path),
            model="gemma3:4b",
            escalate="",
            timeout=120
        )
        
        index = build_index(tmp_path)
        assert "my_func" in index["symbols"]
        
        mock_response = json.dumps({
            "purpose": "Возвращает 42.",
            "recipe": "my_func()",
            "citations": []
        })
        
        with patch("core.rlm.generate", return_value=(mock_response, {})) as mock_gen:
            # 1. First build (cache miss)
            card1 = get_or_build_card(args, index, "foo.py", "my_func")
            assert card1 is not None
            assert card1["symbol"] == "my_func"
            assert card1["advisory"]["purpose"] == "Возвращает 42."
            assert mock_gen.call_count == 1
            
            # The cache file should now exist
            cache_file = rlm_cache_path(str(tmp_path))
            assert cache_file.exists()
            
            # 2. Second read (cache hit)
            mock_gen.reset_mock()
            card2 = get_or_build_card(args, index, "foo.py", "my_func")
            assert card2 is not None
            assert card2["span_hash"] == card1["span_hash"]
            assert card2["advisory"]["purpose"] == "Возвращает 42."
            mock_gen.assert_not_called()
            
            # 3. Content modification (cache invalidation)
            file_path.write_text("def my_func():\n    return 100\n", encoding="utf-8")
            index_updated = build_index(tmp_path)
            
            mock_gen.reset_mock()
            card3 = get_or_build_card(args, index_updated, "foo.py", "my_func")
            assert card3 is not None
            assert card3["span_hash"] != card1["span_hash"]
            assert mock_gen.call_count == 1
            
            # Clean up the cache file
            if cache_file.exists():
                cache_file.unlink()
    finally:
        if tmp_path.exists():
            shutil.rmtree(tmp_path)

