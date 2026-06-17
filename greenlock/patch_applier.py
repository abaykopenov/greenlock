"""core.patch_applier — создание песочниц и применение AST-патчей.

Поддерживает режимы:
1. replace_symbol: точечная замена тела функции/класса по его спану.
2. insert_symbol: вставка нового символа после существующего.
3. new_file: создание нового файла.
"""
import os
import shutil
import uuid
from pathlib import Path

from greenlock.adapters import detect_adapters
from greenlock.config import SANDBOX_DIR

__all__ = ["create_sandbox_dir", "clean_sandbox_dir", "apply_patch"]


def create_sandbox_dir(root: Path) -> Path:
    """Создать изолированную директорию песочницы.

    Копирует директорию root в <base>/sandbox_XXXX/name_директории и возвращает
    путь к sandbox_XXXX (базовый каталог для запуска тестов). База — из
    config.SANDBOX_DIR (для read-only контейнера → tmpfs), иначе .groundqa_sandbox
    рядом с проектом.
    """
    root = Path(root).resolve()

    if SANDBOX_DIR:
        sandbox_base = Path(SANDBOX_DIR)
    else:
        workspace_root = Path(__file__).parent.parent.resolve()
        sandbox_base = workspace_root / ".groundqa_sandbox"
    sandbox_base.mkdir(parents=True, exist_ok=True)

    unique_id = uuid.uuid4().hex[:8]
    sandbox_dir = sandbox_base / f"sandbox_{unique_id}"
    sandbox_dir.mkdir()

    dest_dir = sandbox_dir / root.name

    ignore_dirs = {
        ".git", "node_modules", "vendor", "dist", "__pycache__",
        ".venv", "venv", ".idea", ".gradle", ".groundqa_sandbox"
    }

    def ignore_func(path, names):
        ignored = []
        for name in names:
            full_path = os.path.join(path, name)
            if name in ignore_dirs or os.path.islink(full_path):
                ignored.append(name)
        return ignored

    shutil.copytree(str(root), str(dest_dir), ignore=ignore_func)
    return sandbox_dir


def clean_sandbox_dir(sandbox_dir: Path) -> None:
    """Удалить директорию песочницы (только наши каталоги sandbox_*)."""
    if not sandbox_dir.exists():
        return
    safe = ".groundqa_sandbox" in str(sandbox_dir) or (
        SANDBOX_DIR and sandbox_dir.name.startswith("sandbox_")
        and str(sandbox_dir.resolve()).startswith(str(Path(SANDBOX_DIR).resolve()))
    )
    if safe:
        shutil.rmtree(str(sandbox_dir), ignore_errors=True)


def find_symbol_span(symbols: list[dict], query_name: str) -> dict | None:
    """Найти символ по имени (поддерживает ClassName.method_name)."""
    if "." in query_name:
        parent_name, child_name = query_name.split(".", 1)
        parents = [s for s in symbols if s["name"] == parent_name and s["kind"] == "class"]
        if not parents:
            return None
        parent = parents[0]
        # Ищем метод внутри спана класса
        children = [
            s for s in symbols
            if s["name"] == child_name and s["kind"] == "method"
            and parent["span_start"] <= s["span_start"] <= parent["span_end"]
        ]
        if not children:
            return None
        return children[0]
    else:
        matches = [s for s in symbols if s["name"] == query_name]
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            raise ValueError(f"Ambiguous symbol name '{query_name}': found {len(matches)} matches.")
        return None


def apply_patch(workdir: Path, patch: dict) -> str | None:
    """Применить патч в рабочей директории.

    Режимы (mode):
        add_method     — добавить метод в класс ("class" + "replacement"); без якоря.
        replace_symbol — заменить символ целиком по имени ("symbol", напр. "C.m").
        new_file       — создать новый файл ("replacement" = полный текст).
        rewrite_file   — переписать файл целиком ("replacement" = полный текст).
        insert_symbol  — (устар.) вставка после символа; оставлен для совместимости.

    Возвращает None в случае успеха, иначе строку с описанием ошибки.
    """
    mode = patch.get("mode")
    rel_file = patch.get("file")
    if not rel_file:
        return "Missing 'file' field in patch."

    filepath = workdir / rel_file

    if mode in ("new_file", "rewrite_file"):
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(patch.get("replacement", ""), encoding="utf-8")
            return None
        except Exception as e:
            return f"Failed to write file ({mode}): {e}"

    if not filepath.exists():
        return f"File does not exist: {rel_file}"

    # Ищем подходящий адаптер по расширению для AST/span-режимов
    adapter = None
    for a in detect_adapters():
        if filepath.suffix in a.extensions:
            adapter = a
            break

    if adapter and adapter.name != "regex-fallback":
        try:
            content = filepath.read_text(encoding="utf-8")
            res = adapter.parse(rel_file, content)
        except Exception as e:
            return f"Failed to parse target file: {e}"

        if mode == "replace_symbol":
            sym_name = patch.get("symbol")
            if not sym_name:
                return "Missing 'symbol' field in replace_symbol patch."
            try:
                sym = find_symbol_span(res.symbols, sym_name)
            except ValueError as e:
                return str(e)

            if not sym:
                return f"Symbol '{sym_name}' not found in {rel_file}."

            start, end = sym["span_start"], sym["span_end"]
            if start is None or end is None:
                return f"Symbol '{sym_name}' has no span information."

            lines = content.splitlines(keepends=True)
            # Заменяем строки (1-based, inclusive)
            replacement_lines = patch.get("replacement", "").splitlines(keepends=True)
            # Добавим перевод строки, если его нет
            if replacement_lines and not replacement_lines[-1].endswith(("\n", "\r")):
                replacement_lines[-1] += "\n"

            new_lines = lines[:start - 1] + replacement_lines + lines[end:]
            try:
                filepath.write_text("".join(new_lines), encoding="utf-8")
                return None
            except Exception as e:
                return f"Failed to write changes: {e}"

        elif mode == "add_method":
            # Добавить метод в конец тела класса. Без якоря: достаточно имени класса.
            cls_name = patch.get("class") or patch.get("symbol")
            if not cls_name:
                return "Missing 'class' field in add_method patch."
            classes = [s for s in res.symbols
                       if s["name"] == cls_name and s["kind"] == "class"]
            if not classes:
                return f"Class '{cls_name}' not found in {rel_file}."
            end = classes[0]["span_end"]
            if end is None:
                return f"Class '{cls_name}' has no span information."
            method = patch.get("replacement", "").rstrip("\n")
            if not method.strip():
                return "Empty 'replacement' in add_method patch."
            lines = content.splitlines(keepends=True)
            # Для brace-языков span_end — это строка с закрывающей '}' класса:
            # метод нужно вставить ПЕРЕД ней (внутрь класса). Для Python span_end —
            # последняя строка тела, поэтому добавляем после неё.
            insert_at = (end - 1) if filepath.suffix in (".js", ".ts") else end
            new_lines = lines[:insert_at] + ["\n" + method + "\n"] + lines[insert_at:]
            try:
                filepath.write_text("".join(new_lines), encoding="utf-8")
                return None
            except Exception as e:
                return f"Failed to write changes: {e}"

        elif mode == "insert_symbol":
            after_sym_name = patch.get("after_symbol")
            lines = content.splitlines(keepends=True)
            insert_idx = len(lines)

            if after_sym_name:
                try:
                    sym = find_symbol_span(res.symbols, after_sym_name)
                except ValueError as e:
                    return str(e)
                if sym and sym["span_end"] is not None:
                    insert_idx = sym["span_end"]
                else:
                    return f"Reference symbol '{after_sym_name}' not found or has no span."

            replacement_text = patch.get("replacement", "")
            if not replacement_text.startswith(("\n", "\r")):
                replacement_text = "\n" + replacement_text
            if not replacement_text.endswith(("\n", "\r")):
                replacement_text += "\n"

            # Добавление в указанную позицию
            new_content = "".join(lines[:insert_idx]) + replacement_text + "".join(lines[insert_idx:])
            try:
                filepath.write_text(new_content, encoding="utf-8")
                return None
            except Exception as e:
                return f"Failed to write changes: {e}"

        else:
            return f"Unsupported patch mode: {mode}"
    else:
        # Файлы без структурного адаптера (например, .txt / .yaml / fallback) — замена по target/replacement тексту
        target = patch.get("target")
        replacement = patch.get("replacement", "")
        if target is None:
            return "Missing 'target' field for fallback text replacement."
        try:
            content = filepath.read_text(encoding="utf-8")
            if content.count(target) == 0:
                return f"Target block not found in {rel_file}."
            if content.count(target) > 1:
                return f"Target block is ambiguous (found {content.count(target)} matches) in {rel_file}."
            new_content = content.replace(target, replacement)
            filepath.write_text(new_content, encoding="utf-8")
            return None
        except Exception as e:
            return f"Failed to replace content: {e}"
