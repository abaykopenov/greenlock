"""core.qa_stream — grounded-Q&A как ПОТОК событий (для веб-UI / live-прогресса).

answer_stream(...) — генератор, выдающий словари-события по ходу конвейера:
  start → index → structural → (embed* → retrieve → gate) → generate → answer
        → [escalate → answer] → done
Каждое событие несёт "t" (секунд с начала). Веб-сервер транслирует их в SSE.

Переиспользует ядро (build_index/structural_answer/embed/retrieve/answer_with/…);
цикл эмбеддинга развёрнут здесь, чтобы стримить прогресс и ETA по батчам.
"""
import re
import time
import types
from pathlib import Path

from greenlock import groundqa as g

# Приветствия/общие фразы — не вопросы по коду. Отвечаем подсказкой, НЕ запуская
# пайплайн (иначе gate честно отказывает, и для чата это выглядит как поломка).
_GREET = {"привет", "приветствую", "здравствуй", "здравствуйте", "хай", "ку",
          "йо", "прив", "здаров", "здарова", "дароу", "здрасте", "ало", "алло",
          "hello", "hi", "hey", "yo", "sup", "hola", "тест", "test"}
_HELP = ("Привет! Я отвечаю строго по коду выбранного репозитория — без выдумок. "
         "Спросите что-то конкретное: про функцию, класс, файл, конфиг или "
         "зависимость. Например: «где определена функция X?», «какие зависимости "
         "у проекта?», «что делает Y?».")


def _is_chitchat(q: str) -> bool:
    s = re.sub(r"[^\w\s]", "", (q or "").strip().lower())
    words = s.split()
    if not words:
        return True
    if s in _GREET:
        return True
    return len(words) <= 2 and any(w in _GREET for w in words)
from greenlock.search import (
    embed, chunk_key, load_cache, save_cache, retrieve,
    render_context, SIM_FLOOR, default_cache_path,
)
from greenlock.config import OLLAMA_URL, EMBED_MODEL, QA_MODEL


def _args(base_url, model, escalate, timeout):
    return types.SimpleNamespace(
        base_url=base_url, model=model, escalate=escalate,
        embed_model=EMBED_MODEL, timeout=timeout,
        show_context=False, question=None,
    )


def answer_stream(repo, question, model=QA_MODEL, escalate="",
                  base_url=OLLAMA_URL,
                  embed_model=EMBED_MODEL, batch=64, timeout=120):
    """Генератор событий grounded-Q&A. Не бросает — ошибки идут событием type='error'."""
    t0 = time.time()

    def ev(etype, **kw):
        return {"type": etype, "t": round(time.time() - t0, 2), **kw}

    try:
        yield ev("start", repo=repo, question=question, model=model,
                 escalate=escalate or None)

        # 0. Приветствие/болтовня — не вопрос по коду: подсказка без пайплайна.
        if _is_chitchat(question):
            yield ev("done", source="приветствие (0 токенов)", model="—",
                     answer=_HELP, cites=[], usage=g._zero_usage(),
                     paid_tokens=0, total_tokens=0)
            return

        # 1. Индексация
        index = g.build_index(Path(repo))
        yield ev("index", files=len(index["files"]), chunks=len(index["chunks"]),
                 symbols=len(index["symbols"]), yaml_keys=len(index["yaml_keys"]))

        # 2. Структурный слой (детерминированно, 0 токенов)
        hit = g.structural_answer(index, question)
        if hit:
            ans, cites, usage = hit
            yield ev("structural", hit=True)
            yield ev("done", source="структурный (0 токенов)", model="—",
                     answer=ans, cites=_cites(cites), usage=usage,
                     paid_tokens=0, total_tokens=0)
            return
        yield ev("structural", hit=False)

        # 3. Эмбеддинги (с прогрессом и ETA по батчам)
        cache_path = default_cache_path(repo)
        chunks = index["chunks"]
        keys = [chunk_key(embed_model, c["text"]) for c in chunks]
        cache = load_cache(cache_path)
        missing = [(k, c["text"]) for k, c in zip(keys, chunks) if k not in cache]
        embed_tokens = 0
        if missing:
            total = len(missing)
            te0 = time.time()
            for i in range(0, total, batch):
                part = missing[i:i + batch]
                vecs, tok = embed(base_url, embed_model, [t for _, t in part])
                embed_tokens += tok
                for (k, _), v in zip(part, vecs):
                    cache[k] = v
                save_cache(cache_path, cache)
                done = min(i + batch, total)
                el = time.time() - te0
                rate = done / el if el > 0 else 0
                eta = round((total - done) / rate, 1) if rate > 0 else 0
                yield ev("embed", done=done, total=total, eta=eta,
                         cached=len(chunks) - total)
        else:
            yield ev("embed", done=0, total=0, eta=0, cached=len(chunks))
        index["_keys"] = keys

        # 4. Поиск (retrieve) + 5. gate
        scored, qtok = retrieve(index, question, base_url, embed_model, cache)
        embed_tokens += qtok
        results = [{"sim": round(sim, 3), "file": c["rel"], "line": c["start"]}
                   for _total, sim, c in scored]
        yield ev("retrieve", results=results, embed_tokens=embed_tokens)

        top = scored[0][1] if scored else 0.0
        if top < SIM_FLOOR:
            yield ev("gate", top_sim=round(top, 3), floor=SIM_FLOOR, passed=False)
            yield ev("done", source="gate (модель не вызвана)", model="—",
                     answer=(f"Не нашёл в коде релевантного (похожесть {top:.3f} < "
                             f"порога {SIM_FLOOR}). Это нормально для общих фраз — "
                             f"я отвечаю строго по коду репозитория. Спросите про "
                             f"конкретную функцию, файл, конфиг или зависимость."),
                     cites=[], usage=g._zero_usage(),
                     paid_tokens=0, total_tokens=embed_tokens)
            return
        yield ev("gate", top_sim=round(top, 3), floor=SIM_FLOOR, passed=True)

        # 6. Генерация маленькой моделью
        context = g.render_context(scored)
        user = f"Вопрос: {question}\n\nКОНТЕКСТ:\n{context}"
        args = _args(base_url, model, escalate, timeout)
        spent = []

        yield ev("generate", model=model, stage="маленькая модель отвечает")
        answer, cites, usage = g.answer_with(args, model, index, user)
        spent.append((model, usage))
        yield ev("answer", model=model, answer=answer, cites=_cites(cites),
                 usage=usage)

        final_model = model
        # 7. Эскалация при необходимости
        if escalate:
            why = g.escalation_reason(answer, cites)
            if why:
                yield ev("escalate", reason=why, model=escalate)
                answer, cites, usage = g.answer_with(args, escalate, index, user)
                spent.append((escalate, usage))
                final_model = escalate
                yield ev("answer", model=escalate, answer=answer,
                         cites=_cites(cites), usage=usage)

        paid = sum(u["total"] for m, u in spent if str(m).startswith("gemini"))
        total_tok = embed_tokens + sum(u["total"] for _, u in spent)
        yield ev("done", source="семантика+модель", model=final_model,
                 answer=answer, cites=_cites(cites), usage=usage,
                 paid_tokens=paid, total_tokens=total_tok)

    except Exception as e:
        yield ev("error", message=f"{type(e).__name__}: {e}")


def _cites(cites):
    return [{"cite": c, "ok": bool(ok)} for c, ok in (cites or [])]
