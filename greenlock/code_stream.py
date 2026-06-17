"""core.code_stream — write_code как ПОТОК событий (для веб-UI / live-прогресса).

write_stream(...) — генератор событий цикла письма кода:
  start → index → sandbox → baseline → [precondition] →
  attempt* (generate → generated → parse → apply → closed_world → verify → verified
            [→ verify_fail]) →
  [applied] → done(+diffs)
Каждое событие несёт "t" (секунд с начала). Веб-сервер транслирует их в SSE.

Мост колбэк→генератор: write_code синхронный и зовёт on_event; гоняем его в треде,
события идут через очередь, по завершении считаем unified-дифы изменённых файлов.
Логика оракула не дублируется — единственный источник истины это core.code_writer.
"""
import difflib
import queue
import threading
import time
import types
from pathlib import Path

from greenlock import groundqa as g
from greenlock.code_writer import write_code
from greenlock.config import OLLAMA_URL, EMBED_MODEL, WRITER_MODEL

MAX_SNAP_FILES = 800
MAX_SNAP_BYTES = 300_000
TEXT_EXT = {".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c",
            ".h", ".cpp", ".hpp", ".cs", ".rb", ".php", ".json", ".yaml", ".yml",
            ".toml", ".md", ".txt", ".cfg", ".ini"}


def _args(repo, model, escalate, base_url, timeout):
    return types.SimpleNamespace(
        repo=repo, model=model, escalate=escalate,
        embed_model=EMBED_MODEL, base_url=base_url,
        timeout=timeout, show_context=False, question=None,
    )


def _snapshot(repo_path: Path) -> dict:
    """Снимок текстовых файлов репо ДО правки (для последующего дифа). С лимитами."""
    snap, n = {}, 0
    for p in sorted(repo_path.rglob("*")):
        if n >= MAX_SNAP_FILES:
            break
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            if p.stat().st_size > MAX_SNAP_BYTES:
                continue
            snap[str(p.relative_to(repo_path))] = p.read_text(encoding="utf-8")
            n += 1
        except Exception:
            pass
    return snap


def _diffs(repo_path: Path, before: dict, changed_files: list) -> list:
    """changed_files — пути относительно песочницы (<root>/<rel>). Срезаем корень и дифаем.

    Файлы, реально не изменившиеся в репо (например тест приёмки, живущий только в
    песочнице), отсеиваются — для них before == after == "".
    """
    out = []
    for cf in changed_files or []:
        parts = Path(cf).parts
        rel = str(Path(*parts[1:])) if len(parts) > 1 else cf  # срезаем имя корня
        after_path = repo_path / rel
        after = ""
        if after_path.exists():
            try:
                after = after_path.read_text(encoding="utf-8")
            except Exception:
                after = ""
        before_txt = before.get(rel, "")
        if before_txt == after:
            continue
        diff = "".join(difflib.unified_diff(
            before_txt.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}",
        ))
        out.append({"file": rel,
                    "status": "new" if rel not in before else "modified",
                    "diff": diff[:20000]})
    return out


def write_stream(repo, instruction, target_file, model=WRITER_MODEL,
                 escalate="", base_url=OLLAMA_URL,
                 test_file=None, test_content=None, timeout=180):
    """Генератор событий письма кода. Не бросает — ошибки идут событием type='error'."""
    t0 = time.time()

    def stamp(e: dict) -> dict:
        e = dict(e)
        e["t"] = round(time.time() - t0, 2)
        return e

    try:
        repo_path = Path(repo)
        yield stamp({"type": "start", "repo": repo, "instruction": instruction,
                     "target": target_file, "model": model,
                     "escalate": escalate or None, "test": test_file or None})

        if not repo_path.is_dir():
            yield stamp({"type": "error", "message": f"Нет такого репо: {repo}"})
            return
        if not (instruction or "").strip():
            yield stamp({"type": "error", "message": "Пустая инструкция"})
            return
        if not (target_file or "").strip():
            yield stamp({"type": "error", "message": "Не указан файл-цель (target)"})
            return

        index = g.build_index(repo_path)
        yield stamp({"type": "index", "files": len(index["files"]),
                     "symbols": len(index["symbols"])})

        before = _snapshot(repo_path)
        args = _args(repo, model, escalate, base_url, timeout)

        q: queue.Queue = queue.Queue()
        result: dict = {}

        def on_event(e):
            q.put(("ev", e))

        def run():
            try:
                success, msg, usage, status = write_code(
                    args, index, instruction, target_file,
                    additional_test_file=(test_file or None),
                    additional_test_content=(test_content or None),
                    on_event=on_event)
                result.update(success=success, msg=msg, usage=usage, status=status)
            except Exception as ex:
                result.update(success=False, msg=f"{type(ex).__name__}: {ex}",
                              usage={"prompt": 0, "completion": 0, "total": 0},
                              status="error")
            finally:
                q.put(("done", None))

        th = threading.Thread(target=run, daemon=True)
        th.start()

        changed_files = []
        while True:
            kind, payload = q.get()
            if kind == "ev":
                if payload.get("type") == "applied":
                    changed_files = payload.get("files", [])
                yield stamp(payload)
            else:
                break
        th.join(timeout=1)

        status = result.get("status", "error")
        usage = result.get("usage", {"prompt": 0, "completion": 0, "total": 0})
        diffs = _diffs(repo_path, before, changed_files) if status == "applied" else []
        yield stamp({"type": "done", "status": status,
                     "applied": status == "applied",
                     "message": result.get("msg", ""),
                     "diffs": diffs, "usage": usage,
                     "total_tokens": usage.get("total", 0)})

    except Exception as e:
        yield stamp({"type": "error", "message": f"{type(e).__name__}: {e}"})
