#!/usr/bin/env python3
"""webapp/server.py — лёгкий веб-UI поверх grounded-Q&A (stdlib, без зависимостей).

Запуск:  python3 webapp/server.py   (затем открыть http://127.0.0.1:8000)

Эндпоинты:
  GET /                — страница (index.html)
  GET /api/repos       — список локальных репо
  GET /api/models      — модели Ollama (+ Gemini)
  GET /api/files?repo= — список редактируемых файлов репо (для режима правки)
  GET /api/ask?...     — SSE-поток grounded-Q&A (repo, question, model, escalate)
  GET /api/write?...   — SSE-поток письма кода (repo, instruction, target, model,
                         escalate, test_file, test_content)
"""
import json
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from greenlock.qa_stream import answer_stream  # noqa: E402
from greenlock.code_stream import write_stream, TEXT_EXT  # noqa: E402
from greenlock.config import OLLAMA_URL  # noqa: E402

DEFAULT_BASE_URL = OLLAMA_URL


def list_files(repo):
    """Редактируемые текстовые файлы репо (относительные пути). Для режима правки."""
    base = (ROOT / repo).resolve()
    try:
        base.relative_to(ROOT)  # не выпускаем за корень проекта
    except ValueError:
        return []
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if any(part.startswith(".") for part in p.relative_to(base).parts):
            continue
        if p.suffix.lower() in TEXT_EXT:
            out.append(str(p.relative_to(base)))
        if len(out) >= 2000:
            break
    return out


def list_repos():
    repos = []
    rdir = ROOT / "repos"
    if rdir.exists():
        repos += [f"repos/{p.name}" for p in sorted(rdir.iterdir())
                  if p.is_dir() and not p.name.startswith((".", "__"))]
    for extra in ("sample_project",):
        if (ROOT / extra).is_dir():
            repos.append(extra)
    return repos


def list_models(base_url):
    models = []
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=5) as r:
            data = json.loads(r.read().decode())
        models = [{"name": m["name"], "where": "локально"}
                  for m in sorted(data.get("models", []), key=lambda m: m["name"])]
    except Exception:
        pass
    models.append({"name": "gemini-2.5-flash", "where": "облако (платно)"})
    return models


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тише в консоли
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            html = (Path(__file__).parent / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if u.path == "/api/repos":
            self._json({"repos": list_repos()})
            return

        if u.path == "/api/models":
            base = q.get("base_url", [DEFAULT_BASE_URL])[0]
            self._json({"models": list_models(base)})
            return

        if u.path == "/api/files":
            repo = q.get("repo", [""])[0]
            self._json({"files": list_files(repo) if repo else []})
            return

        if u.path == "/api/ask":
            self._sse(q)
            return

        if u.path == "/api/write":
            self._sse_write(q)
            return

        self.send_error(404)

    def _sse_open(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _sse_pump(self, events):
        try:
            for event in events:
                chunk = f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # клиент отключился

    def _sse(self, q):
        repo = q.get("repo", [""])[0]
        question = q.get("question", [""])[0]
        model = q.get("model", ["gemma3:4b"])[0]
        escalate = q.get("escalate", [""])[0]
        base = q.get("base_url", [DEFAULT_BASE_URL])[0]
        if not repo or not question:
            self._json({"error": "нужны repo и question"}, 400)
            return
        self._sse_open()
        self._sse_pump(answer_stream(repo, question, model=model,
                                     escalate=escalate, base_url=base))

    def _sse_write(self, q):
        repo = q.get("repo", [""])[0]
        instruction = q.get("instruction", [""])[0]
        target = q.get("target", [""])[0]
        model = q.get("model", ["qwen3-coder:latest"])[0]
        escalate = q.get("escalate", [""])[0]
        base = q.get("base_url", [DEFAULT_BASE_URL])[0]
        test_file = q.get("test_file", [""])[0] or None
        test_content = q.get("test_content", [""])[0] or None
        if not repo or not instruction or not target:
            self._json({"error": "нужны repo, instruction и target"}, 400)
            return
        self._sse_open()
        self._sse_pump(write_stream(repo, instruction, target, model=model,
                                    escalate=escalate, base_url=base,
                                    test_file=test_file, test_content=test_content))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Low-model UI:  http://127.0.0.1:{port}   (Ctrl+C для остановки)")
    print(f"репо: {', '.join(list_repos()) or '—'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nстоп")


if __name__ == "__main__":
    main()
