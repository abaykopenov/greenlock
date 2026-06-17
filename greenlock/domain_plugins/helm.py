"""domain_plugins.helm — Helm-специфичный плагин.

RU_KEY_HINTS: маппинг русских слов → имена YAML-ключей (зависимост→dependencies и т.п.).
ROOT_AUTH_FILES: авторитетные корневые файлы Helm-чарта.
_database_answer: определение БД среди зависимостей + коррекция ложной посылки.
"""
import re

from greenlock.utils import _norm, _zero_usage
from greenlock.citations import verify_citations

__all__ = ["HelmPlugin"]

# Узкая карта «русское понятие -> имя ключа». Намеренно маленькая: каждый намёк
# должен соответствовать реальному ключу в инфраструктуре, иначе шум.
RU_KEY_HINTS = {
    "зависимост": ["dependencies"],
    "порт": ["nodeport", "port"],
    "включ": ["enabled"], "выключ": ["enabled"], "отключ": ["enabled"],
}

# Авторитетные корневые YAML — где живут «значения по умолчанию» чарта.
ROOT_AUTH_FILES = {"chart.yaml", "values.yaml", "requirements.yaml"}

# Вопросы о хранилище данных + известные имена БД-чартов (домен Helm).
DB_QUERY_TERMS = ("база данных", "базе данных", "database", "реляцион",
                  "хранятся данн", "хранилищ", "где хранят")
DB_CHART_NAMES = {"postgresql", "postgres", "mysql", "mariadb", "mongodb",
                  "mongo", "redis", "cassandra", "influxdb", "influxdb2",
                  "cockroachdb", "elasticsearch", "clickhouse", "couchdb", "neo4j"}


def _database_answer(index: dict, query: str):
    """«В какой базе хранятся данные» — определяем реальные БД среди зависимостей
    (mongodb/influxdb2/...) и поправляем ложную посылку (PostgreSQL и т.п.)."""
    low = query.lower()
    if not any(t in low for t in DB_QUERY_TERMS):
        return None
    deps = [e for e in index.get("yaml_keys") or []
            if e["path"] == "dependencies[].name" and e["value"]]
    db_deps = [e for e in deps if _norm(e["value"]) in DB_CHART_NAMES]
    if not db_deps:
        return None
    listed = ", ".join(f"{e['value']} ({e['file']}:{e['line']})" for e in db_deps)
    present = {_norm(e["value"]) for e in deps}
    named_absent = sorted({d for d in DB_CHART_NAMES
                           if d in low.replace(" ", "") and d not in present})
    note = (f" Реляционной БД ({', '.join(named_absent)}) в проекте нет."
            if named_absent else "")
    ans = f"Данные хранятся в: {listed}.{note}"
    return ans, verify_citations(ans, index), _zero_usage()


class HelmPlugin:
    """Плагин для Helm-чартов: RU-хинты, авторитетные файлы, БД-зависимости."""

    name = "helm"

    def key_hints(self) -> dict[str, list[str]]:
        return RU_KEY_HINTS

    def auth_files(self) -> set[str]:
        return ROOT_AUTH_FILES

    def extra_authoritative(self) -> set[str]:
        return {"chart.yaml", "values.yaml", "requirements.yaml"}

    def handlers(self, index: dict, query: str, qtoks: set[str]) -> list:
        return [_database_answer(index, query)]
