#!/usr/bin/env python3
"""Прогон полного набора вопросов через groundqa с эскалацией + сводка.

Три типа вопросов:
  fact         — ответ в коде есть; ждём верный ответ с цитатой.
  false_premise — в вопросе ложная посылка; большая модель должна поправить.
  unanswerable — в коде этого нет; ждём отказ или отсечение по порогу (gate).

Запуск:  python3 run_suite.py            (gemma3:4b -> эскалация gemini-2.5-flash)
         python3 run_suite.py --no-escalate
"""
import argparse
import types

from greenlock import groundqa as g
from greenlock.config import OLLAMA_URL, EMBED_MODEL
from pathlib import Path

# (тип, вопрос, ожидаемый_файл, [ключевые слова; "a|b" = синонимы])
SAMPLE_Q = [
    # --- факты (ответ в коде есть) ---
    ("fact", "Какие поля у задачи Task?", "models.py", ["priority", "title"]),
    ("fact", "В каком файле и в каком формате хранятся задачи?", "storage.py", ["json"]),
    ("fact", "Как формируется новый id при добавлении задачи?", "storage.py", ["макс|max", "1|едини|один"]),
    ("fact", "Какие команды поддерживает CLI?", "cli.py", ["add", "list", "done"]),
    ("fact", "На какой хост и порт отправляется почта?", "notify.py", ["localhost", "25"]),
    ("fact", "Какое значение priority у задачи по умолчанию?", "models.py", ["normal"]),
    ("fact", "Какая функция отправляет напоминание?", "notify.py", ["send_reminder"]),
    # --- ложные посылки (надо поправить) ---
    ("false_premise", "Как именно настроена отправка напоминаний через Slack?", "notify.py", ["email", "smtp"]),
    ("false_premise", "Где задачи хранятся в базе данных SQLite?", "storage.py", ["json"]),
    # --- нет в коде (надо отказаться/отсечь) ---
    ("unanswerable", "Как настроено подключение к базе PostgreSQL?", "", []),
    ("unanswerable", "Как работает аутентификация пользователей?", "", []),
    ("unanswerable", "Какой REST API endpoint поднимает приложение?", "", []),
]

# openegiz: Helm-чарт платформы цифровых двойников (на базе OpenTwins)
OPENEGIZ_Q = [
    # --- факты ---
    ("fact", "Какие сервисы-зависимости разворачивает чарт?", "requirements.yaml", ["ditto", "mongodb"]),
    ("fact", "На каком проекте основан OpenEgiz?", "README.md", ["opentwins"]),
    ("fact", "Включён ли Hono по умолчанию?", "values.yaml", ["false|выключ|отключ|disabled|не"]),
    ("fact", "Какой nodePort у mosquitto?", "values.yaml", ["30511"]),
    ("fact", "Какие предварительные требования (prerequisites) нужны?", "README.md", ["docker", "kubernetes", "helm"]),
    ("fact", "Какую команду выполнить, если Grafana не подключается к ditto-extended-api?", "README.md", ["rollout", "restart"]),
    ("fact", "Какая appVersion у чарта?", "Chart.yaml", ["1.0.0"]),
    # --- ложные посылки ---
    ("false_premise", "В какой реляционной базе (PostgreSQL) хранятся данные?", "requirements.yaml", ["mongo"]),
    ("false_premise", "Какой брокер сообщений используется — RabbitMQ?", "values.yaml", ["mosquitto|mqtt|kafka"]),
    # --- нет в репо ---
    ("unanswerable", "Как настроена интеграция оплаты через Stripe?", "", []),
    ("unanswerable", "Как настроено резервное копирование в AWS S3?", "", []),
    ("unanswerable", "Где описан CI/CD pipeline в GitLab?", "", []),
]

# 20 СОВЕРШЕННО НОВЫХ вопросов — НИ ОДИН обработчик под них не настраивался.
# Тест обобщения: ground truth выверен по самому репозиторию (grep).
OPENEGIZ2_Q = [
    # --- факты: конфиг/ключи (структурный слой может зацепить) ---
    ("fact", "Какой nodePort у grafana?", "values.yaml", ["30718"]),
    ("fact", "Какой nodePort у influxdb2?", "values.yaml", ["30716"]),
    ("fact", "Какой nodePort у ditto?", "values.yaml", ["30525"]),
    ("fact", "Какой тег образа используется для mongodb?", "values.yaml", ["6.0.10"]),
    ("fact", "Какая версия зависимости ditto указана?", "requirements.yaml", ["3.3.7"]),
    ("fact", "Какие логин и пароль у grafana по умолчанию?", "values.yaml", ["admin"]),
    # --- факты: проза/доки (только семантика, риск gate) ---
    ("fact", "Какой namespace нужно указать в плагине Grafana?", "README.md", ["org.openegiz"]),
    ("fact", "Как называется источник данных (data source) в Grafana?", "README.md", ["opentwins"]),
    ("fact", "Что делает команда make status?", "", ["pod"]),
    ("fact", "Какую команду Helm запускает make install?", "Makefile", ["helm install"]),
    ("fact", "Как установить k3s по инструкции README?", "README.md", ["k3s"]),
    ("fact", "Сколько сервисов-зависимостей у чарта?", "requirements.yaml", ["7|семь"]),
    # --- ложные посылки ---
    ("false_premise", "Grafana ведь работает на стандартном порту 3000?", "values.yaml", ["30718"]),
    ("false_premise", "Образы mongodb берутся из Google Container Registry (gcr.io), верно?", "values.yaml", ["docker.io|bitnami"]),
    ("false_premise", "Telegraf пишет данные напрямую в PostgreSQL?", "values.yaml", ["influx"]),
    # --- нет в репо (ждём отказ/gate) ---
    ("unanswerable", "Как настроена интеграция с Telegram-ботом?", "", []),
    ("unanswerable", "Как настроена отправка SMS через Twilio?", "", []),
    ("unanswerable", "Кто указан как maintainer (с email) в Chart.yaml?", "", []),
    ("unanswerable", "Как настроена интеграция с Jira?", "", []),
    ("unanswerable", "Какой облачный провайдер (AWS/GCP/Azure) используется для деплоя?", "", []),
]

# Локализация символов кода (таблица символов). Ответы выверены по sample_project.
SYMBOLS_Q = [
    ("fact", "Где определена функция send_reminder?", "notify.py", ["notify"]),
    ("fact", "В каком файле определён класс TaskStore?", "storage.py", ["storage"]),
    ("fact", "Где находится метод add_task?", "storage.py", ["storage"]),
    ("fact", "В каком файле определена функция main?", "cli.py", ["cli"]),
    ("fact", "Где определён класс Task?", "models.py", ["models"]),
    # символа нет → не выдумать локацию
    ("unanswerable", "Где определена функция delete_user?", "", []),
]

SETS = {
    "sample": ("sample_project", SAMPLE_Q),
    "openegiz": ("repos/openegiz", OPENEGIZ_Q),
    "openegiz2": ("repos/openegiz", OPENEGIZ2_Q),
    "symbols": ("sample_project", SYMBOLS_Q),
}


def make_args(model: str, escalate: str, repo: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        repo=repo, model=model, escalate=escalate,
        embed_model=EMBED_MODEL,
        base_url=OLLAMA_URL,
        timeout=120, show_context=False, question=None,
    )


def run_one(index: dict, args, question: str, cache: dict,
            plugins=None) -> dict:
    res = {"embed": 0, "top_sim": 0.0, "gated": False, "error": False,
           "escalated": False, "structural": False, "spent": [], "answer": "",
           "cites": [], "final_model": "—"}
    try:
        hit = g.structural_answer(index, question, plugins=plugins)
        if hit:
            answer, cites, usage = hit
            res["structural"] = True
            res["spent"].append(("структурный", usage))
            res["final_model"] = "структурный"
            res["answer"], res["cites"] = answer, cites
            return res

        scored, embed_tokens = g.retrieve(index, question, args.base_url,
                                          args.embed_model, cache)
        res["embed"] = embed_tokens
        res["top_sim"] = scored[0][1] if scored else 0.0
        if res["top_sim"] < g.SIM_FLOOR:
            res["gated"] = True
            res["answer"] = f"(gate {res['top_sim']:.3f} < {g.SIM_FLOOR}: модель не вызвана)"
            return res

        context = g.render_context(scored)
        user = f"Вопрос: {question}\n\nКОНТЕКСТ:\n{context}"
        answer, cites, usage = g.answer_with(args, args.model, index, user)
        res["spent"].append((args.model, usage))
        res["final_model"] = args.model

        if args.escalate and g.escalation_reason(answer, cites):
            res["escalated"] = True
            answer, cites, usage2 = g.answer_with(args, args.escalate, index, user)
            res["spent"].append((args.escalate, usage2))
            res["final_model"] = args.escalate

        res["answer"], res["cites"] = answer, cites
    except Exception as e:  # один сбойный вопрос не валит весь прогон
        res["error"] = True
        res["answer"] = f"(ошибка: {type(e).__name__}: {e})"
    return res


# Признаки «модель отказалась / сообщила, что в коде этого нет» — шире, чем
# каноничное "не знаю": большая модель часто формулирует иначе ("нет информации",
# "не упоминается"). Используется ТОЛЬКО для unanswerable (успех = не выдумала).
DECLINE_MARKS = ("не знаю", "нет информации", "этого нет", "не упоминается",
                 "не содержит", "не настроен", "не указан", "отсутству")


def _declines(answer: str) -> bool:
    a = answer.lower()
    return any(m in a for m in DECLINE_MARKS)


def judge(kind: str, res: dict, expect_file: str, keywords: list) -> str:
    """Оценка: факты — верный текст (ключевые слова) + цитата на нужный файл."""
    if res.get("error"):
        return "ERROR"
    ans = res["answer"].lower()
    refused = (not res["gated"]) and g.is_refusal(res["answer"])
    cited_files = {Path(c.split(":")[0]).name for c, ok in res["cites"] if ok}
    file_ok = (not expect_file) or (expect_file in cited_files)
    # ключевое слово может содержать синонимы через "|"
    kw_ok = all(any(alt in ans for alt in k.lower().split("|")) for k in keywords)

    if kind == "unanswerable":
        # успех = НЕ выдумала: либо gate, либо отказ (в любой формулировке)
        declined = (not res["gated"]) and _declines(res["answer"])
        return "PASS" if (res["gated"] or declined) else "FAIL(выдумала)"
    if res["gated"]:
        return "FAIL(gate-отсёк-верное)"
    if refused:
        return "FAIL(отказ)"
    if kind == "fact":
        if kw_ok and file_ok:
            return "PASS"
        if kw_ok and not file_ok:
            return "WARN(факт ок, цитата не туда)"
        return "FAIL(факт неверный)"
    if kind == "false_premise":
        return "CORRECT" if (kw_ok and file_ok) else "WARN/SAFE"
    return "?"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gemma3:4b")
    ap.add_argument("--escalate", default="gemini-2.5-flash")
    ap.add_argument("--no-escalate", action="store_true")
    ap.add_argument("--no-plugins", action="store_true",
                    help="отключить доменные плагины")
    ap.add_argument("--set", default="sample", choices=list(SETS))
    ap.add_argument("--repo", default="")
    a = ap.parse_args()
    escalate = "" if a.no_escalate else a.escalate
    plugins = [] if a.no_plugins else None  # None = авто-загрузка
    default_repo, questions = SETS[a.set]
    repo = a.repo or default_repo
    args = make_args(a.model, escalate, repo)

    index = g.build_index(Path(args.repo))
    print(f"набор: {a.set}   проект: {args.repo} "
          f"({len(index['files'])} файлов, {len(index['chunks'])} чанков)")
    print(f"маленькая: {args.model}   эскалация: {escalate or '(выкл)'}")
    cache, _ = g.embed_chunks_cached(index, args.base_url, args.embed_model,
                                     g.default_cache_path(args.repo))
    print("=" * 70)

    tot_embed = tot_local = tot_cloud = 0
    rows = []
    for kind, q, expect_file, keywords in questions:
        r = run_one(index, args, q, cache, plugins=plugins)
        verdict = judge(kind, r, expect_file, keywords)
        tot_embed += r["embed"]
        for model, u in r["spent"]:
            if model.startswith("gemini"):
                tot_cloud += u["total"]
            else:
                tot_local += u["total"]
        esc = " ⊕str" if r["structural"] else " ⇗esc" if r["escalated"] else ""
        ok_cites = ",".join(c for c, ok in r["cites"] if ok) or "—"
        print(f"\n[{kind}] {q}")
        print(f"  → {verdict}{esc}  (модель: {r['final_model']}, цитаты: {ok_cites})")
        print(f"    {r['answer'].strip()[:220].replace(chr(10), ' ')}")
        rows.append((kind, verdict, esc.strip()))

    print("\n" + "=" * 70)
    print("СВОДКА:")
    for kind in ("fact", "false_premise", "unanswerable"):
        items = [v for k, v, _ in rows if k == kind]
        print(f"  {kind:14} {len(items)} вопр.: " + ", ".join(items))
    n_str = sum(1 for *_, e in rows if e == "⊕str")
    n_esc = sum(1 for *_, e in rows if e == "⇗esc")
    print(f"  структурных (0 токенов): {n_str}, эскалаций: {n_esc} из {len(rows)}")
    print("\nТОКЕНЫ:")
    print(f"  эмбеддинги (локально, беспл.): {tot_embed}")
    print(f"  маленькая модель (локально, беспл.): {tot_local}")
    print(f"  облако (Gemini, ПЛАТНО): {tot_cloud}")
    print(f"  ВСЕГО: {tot_embed + tot_local + tot_cloud}  |  платно: {tot_cloud}")


if __name__ == "__main__":
    main()
