"""core.qa — взаимодействие с моделями и CLI точка входа.

Маршрутизация: gemini-* → Google API, иначе → локальная Ollama.
Эскалация: маленькая модель → проверка → при отказе/плохих цитатах → большая.
"""
import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from greenlock.citations import verify_citations, escalation_reason, is_refusal
from greenlock.index import build_index
from greenlock.search import (
    ollama_post, embed_chunks_cached, retrieve, render_context,
    default_cache_path, SIM_FLOOR,
)
from greenlock.structural import structural_answer
from greenlock.utils import TRANSIENT_CODES, urlopen_retry
from greenlock.config import (
    GEMINI_KEY_PATH, OLLAMA_URL, QA_MODEL, EMBED_MODEL, ESCALATE_MODEL,
)

__all__ = [
    "TRANSIENT_CODES", "urlopen_retry", "SYSTEM_PROMPT",
    "ask_ollama", "gemini_chat", "generate",
    "answer_with", "print_answer", "main",
]


def _ssl_context() -> ssl.SSLContext:
    """CA-сертификаты из certifi, если есть (python.org-сборки их не ставят)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SYSTEM_PROMPT = (
    "Ты отвечаешь на вопросы по кодовой базе СТРОГО по предоставленному КОНТЕКСТУ.\n"
    "Правила:\n"
    "1. Используй ТОЛЬКО факты из контекста. Ничего не придумывай и не додумывай.\n"
    "2. После КАЖДОГО факта ставь ссылку формата имя_файла:номер_строки.\n"
    "   Имя файла бери ИЗ ЗАГОЛОВКА [ФАЙЛ ...] того куска, откуда взял факт;\n"
    "   номер строки — из этого же куска. Не выдумывай имя файла.\n"
    "3. Если вопрос предполагает то, чего в коде нет, но в контексте есть верный\n"
    "   факт — поправь спрашивающего и укажи, как на самом деле, со ссылкой.\n"
    "4. Если ответа в контексте НЕТ вообще — ответь ровно одной фразой:\n"
    "   'Не знаю — в предоставленном коде этого нет.'\n"
    "Отвечай кратко, на русском. Не копируй формулировки из этих правил —\n"
    "отвечай по существу заданного вопроса."
)


def ask_ollama(base_url: str, model: str, system: str, user: str,
               timeout: int = 600):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.1},
    }
    data = ollama_post(base_url, "/api/chat", payload, timeout=timeout)
    usage = {
        "prompt": data.get("prompt_eval_count", 0),
        "completion": data.get("eval_count", 0),
    }
    usage["total"] = usage["prompt"] + usage["completion"]
    return data["message"]["content"], usage


def gemini_chat(model: str, system: str, user: str, timeout: int = 600) -> str:
    """Большая облачная модель для разовых задач.

    Ключ берётся из GEMINI_API_KEY (или GOOGLE_API_KEY) — в коде его нет.
    """
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        key_file = GEMINI_KEY_PATH
        if key_file.exists():
            api_key = key_file.read_text().strip()
    if not api_key:
        raise RuntimeError(
            "Нет ключа. Либо `export GEMINI_API_KEY=...`, либо положи ключ в "
            "файл .gemini_key рядом со скриптом. В чат ключ писать не нужно.")
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    payload = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.1},
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    with urlopen_retry(req, timeout=timeout, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    um = data.get("usageMetadata", {})
    usage = {
        "prompt": um.get("promptTokenCount", 0),
        "completion": um.get("candidatesTokenCount", 0),
        "total": um.get("totalTokenCount", 0),
    }
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts), usage
    except (KeyError, IndexError):
        return json.dumps(data, ensure_ascii=False)[:500], usage


def generate(args, model: str, system: str, user: str):
    """Маршрутизация: gemini-* -> Google API, иначе -> локальная Ollama."""
    if model.startswith("gemini"):
        return gemini_chat(model, system, user, timeout=args.timeout)
    return ask_ollama(args.base_url, model, system, user, timeout=args.timeout)


def answer_with(args, model: str, index: dict, user: str):
    """Один прогон модели + проверка цитат."""
    answer, usage = generate(args, model, SYSTEM_PROMPT, user)
    cites = verify_citations(answer, index)
    return answer, cites, usage


def print_answer(model: str, answer: str, cites, usage) -> None:
    print(f"=== ОТВЕТ ({model}) ===\n{answer}\n")
    if cites:
        print("проверка цитат:")
        for c, ok in cites:
            print(f"  {'OK ' if ok else 'НЕТ'}  {c}")
    else:
        print("проверка цитат: модель не привела ссылок файл:строка")
    print(f"токены {model}: ввод {usage['prompt']} + вывод "
          f"{usage['completion']} = {usage['total']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--repo", default="sample_project")
    ap.add_argument("--model", default=QA_MODEL,
                    help="маленькая модель рантайма (отвечает первой)")
    ap.add_argument("--escalate", default=ESCALATE_MODEL,
                    help="большая модель для эскалации, если маленькая ушла в "
                         "отказ или цитаты не прошли (напр. gemini-2.5-flash)")
    ap.add_argument("--embed-model", default=EMBED_MODEL)
    ap.add_argument("--base-url", default=OLLAMA_URL)
    ap.add_argument("--timeout", type=int, default=600,
                    help="таймаут генерации в секундах (большие модели медленнее)")
    ap.add_argument("--cache", default="",
                    help="файл кэша эмбеддингов (по умолчанию — рядом со скриптом)")
    ap.add_argument("--batch", type=int, default=64,
                    help="размер батча эмбеддинга при индексации")
    ap.add_argument("--reindex", action="store_true",
                    help="пересчитать кэш эмбеддингов заново")
    ap.add_argument("--show-context", action="store_true")
    ap.add_argument("--no-plugins", action="store_true",
                    help="отключить доменные плагины (структурные хендлеры)")
    args = ap.parse_args()

    # Определяем плагины: None = авто-загрузка, [] = отключены
    plugins = [] if args.no_plugins else None

    index = build_index(Path(args.repo))
    print(f"indexed: {len(index['files'])} файлов, "
          f"{len(index['symbols'])} символов, {len(index['chunks'])} чанков, "
          f"{len(index['yaml_keys'])} yaml-ключей")

    # Структурный слой: точный ответ из индекса ключей — до семантики и без
    # модели. Если ключ найден уверенно, отвечаем детерминированно и выходим.
    hit = structural_answer(index, args.question, plugins=plugins)
    if hit:
        answer, cites, usage = hit
        print("\n=== СТРУКТУРНЫЙ ОТВЕТ (индекс ключей, без модели) ===")
        print_answer("структурный", answer, cites, usage)
        print("\nИТОГО ТОКЕНОВ: 0 — ответ из структурного индекса, "
              "ни эмбеддинги, ни модель не вызывались.")
        return

    cache_path = Path(args.cache) if args.cache else default_cache_path(args.repo)
    if args.reindex and cache_path.exists():
        cache_path.unlink()
    try:
        cache, build_tokens = embed_chunks_cached(
            index, args.base_url, args.embed_model, cache_path, batch=args.batch)
        scored, query_tokens = retrieve(index, args.question, args.base_url,
                                        args.embed_model, cache)
        embed_tokens = build_tokens + query_tokens
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        print(f"Ошибка поиска (эмбеддинги): {e}", file=sys.stderr)
        sys.exit(1)
    print()

    print("найдено (sim / файл:строка):")
    for _total, sim, c in scored:
        print(f"  {sim:.3f}  {c['rel']}:{c['start']}")
    print()

    # Порог уверенности: лучший кусок слишком далёк -> не зовём модель.
    top_sim = scored[0][1] if scored else 0.0
    if top_sim < SIM_FLOOR:
        print(f"ОТВЕТ: Недостаточно данных — лучший результат {top_sim:.3f} "
              f"ниже порога {SIM_FLOOR}. Модель не вызывалась.")
        print(f"\nИТОГО ТОКЕНОВ: эмбеддинги {embed_tokens}, генерация 0 "
              f"(gate отсёк до модели) = {embed_tokens}")
        return

    context = render_context(scored)
    if args.show_context:
        print("=== КОНТЕКСТ ===\n" + context + "\n================\n")

    user = f"Вопрос: {args.question}\n\nКОНТЕКСТ:\n{context}"
    spent = []  # (модель, usage) по каждому вызову — для итога
    try:
        answer, cites, usage = answer_with(args, args.model, index, user)
        print_answer(args.model, answer, cites, usage)
        spent.append((args.model, usage))

        # Эскалация: отказ / нет цитат / непрошедшие цитаты / без фактов -> большая.
        if args.escalate:
            why = escalation_reason(answer, cites)
            if why:
                print(f"\n>>> эскалация на {args.escalate} (причина: {why})\n")
                answer, cites, usage = answer_with(args, args.escalate, index, user)
                print_answer(args.escalate, answer, cites, usage)
                spent.append((args.escalate, usage))
    except urllib.error.HTTPError as e:
        print(f"Ошибка API {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Не достучался до сервера: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)

    gen_total = sum(u["total"] for _, u in spent)
    print("\nИТОГО ТОКЕНОВ:")
    print(f"  эмбеддинги (локально): {embed_tokens}")
    for model, u in spent:
        print(f"  {model}: ввод {u['prompt']} + вывод {u['completion']} = {u['total']}")
    print(f"  ВСЕГО (эмбеддинги + генерация): {embed_tokens + gen_total}")
