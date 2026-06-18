"""core.config — единая точка настроек (env → локальный файл → дефолт).

Делает продукт независимым от конкретной машины/проекта: эндпоинт Ollama, модели,
путь к ключу Gemini — всё переопределяемо без правки кода.

Приоритет значения:
  1) переменная окружения (GREENLOCK_*),
  2) greenlock.local.json в корне (gitignored, личные настройки машины),
  3) дефолт в коде.

Дефолт эндпоинта — localhost (корректно для OSS). Свой адрес (напр. Tailscale)
держи в greenlock.local.json, он не попадёт в репозиторий.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_LOCAL_FILE = ROOT / "greenlock.local.json"

try:
    _LOCAL = json.loads(_LOCAL_FILE.read_text(encoding="utf-8"))
except Exception:
    _LOCAL = {}


def get(key: str, env: str, default: str) -> str:
    """Разрешить настройку по приоритету env > local-файл > дефолт."""
    v = os.environ.get(env)
    if v:
        return v
    if _LOCAL.get(key):
        return str(_LOCAL[key])
    return default


OLLAMA_URL     = get("ollama_url",    "GREENLOCK_OLLAMA_URL",    "http://localhost:11434")
WRITER_MODEL   = get("writer_model",  "GREENLOCK_WRITER_MODEL",  "qwen3-coder:latest")
QA_MODEL       = get("qa_model",      "GREENLOCK_QA_MODEL",      "gemma3:4b")
EMBED_MODEL    = get("embed_model",   "GREENLOCK_EMBED_MODEL",   "qwen3-embedding:4b")
ESCALATE_MODEL = get("escalate_model", "GREENLOCK_ESCALATE",     "")  # пусто = эскалация выкл
GEMINI_KEY_PATH = Path(get("gemini_key", "GREENLOCK_GEMINI_KEY", str(ROOT / ".gemini_key")))
# База для песочниц. Пусто = рядом с проектом (.groundqa_sandbox). В read-only
# контейнере указывает на writable tmpfs (напр. /tmp/gl-sandbox).
SANDBOX_DIR = get("sandbox_dir", "GREENLOCK_SANDBOX_DIR", "")
# STRONG изоляция: весь гейт в одном запертом контейнере (isolate.py). Управляет
# и CLI (--isolated), и MCP-сервером — единая семантика.
DOCKER = get("docker", "GREENLOCK_DOCKER", "")  # "1" or "true" = enabled (strong)
DOCKER_IMAGE = get("docker_image", "GREENLOCK_DOCKER_IMAGE", "")  # образ для strong
# WEAK per-command изоляция (adapters/docker_wrapper): только команда теста в
# официальном образе языка. ОТДЕЛЬНЫЙ ключ (WS-5) — чтобы не путать со strong.
VERIFIER_DOCKER = get("verifier_docker", "GREENLOCK_VERIFIER_DOCKER", "")
# Доверенный автор: danger-конструкции (eval/exec/subprocess/...) НЕ блокируют, а лишь
# сообщаются. Для self-CI/догфудинга над собственным кодом. Дефолт — выкл (защита вкл).
TRUST = get("trust", "GREENLOCK_TRUST", "")  # "1"/"true" = доверенный режим
