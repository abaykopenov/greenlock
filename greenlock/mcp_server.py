"""greenlock.mcp_server — MCP-сервер: гейт как инструмент для любого ИИ-агента.

Запуск:  greenlock-mcp   (или python -m greenlock.mcp_server) — транспорт stdio.
Подключи в MCP-клиенте (Claude Code / Cursor / …). Требует extra:
    pip install "greenlock[mcp]"

Модуль импортируется и без установленного MCP SDK (полезно для упаковки/тестов);
сам SDK подтягивается только при запуске сервера в main().
"""

__all__ = ["main"]


def verify_patch(repo: str, diff: str) -> dict:
    """Детерминированно проверить unified-diff против репозитория.

    Применяет diff в песочнице, гоняет closed-world + родной тест-сет проекта и
    сравнивает с baseline. Возвращает вердикт: decision ('merge' | 'reject') и
    причины. Модель НЕ вызывается — чистый детерминированный гейт.

    repo — путь к репозиторию; diff — текст unified-diff (как `git diff`).
    """
    from greenlock.gate import verify_patch as _impl
    return _impl(repo, diff)


def harden_and_verify(repo: str, diff: str) -> dict:
    """Как verify_patch, но при отсутствии покрытия (confidence=degraded) сначала
    авто-генерирует характеризационные тесты для изменённых .py и перепроверяет —
    так ловится изменение поведения даже в репо без тестов.

    Требует доступный Ollama-эндпоинт (см. greenlock.config). repo/diff — как выше.
    """
    from greenlock.testgen import harden_and_verify as _impl
    return _impl(repo, diff)


def generate_characterization_tests(repo: str, target_file: str) -> dict:
    """Сгенерировать golden-master тесты, фиксирующие ТЕКУЩЕЕ поведение target_file
    (rel-путь внутри repo). Истина берётся из реального исполнения кода, не из
    догадки модели. Возвращает {test_file, content, kept, covered_symbols, …}.

    Требует доступный Ollama-эндпоинт. repo — путь к репо; target_file — путь к .py.
    """
    from greenlock.testgen import generate_characterization_tests as _impl
    return _impl(repo, target_file)


def _build_server():
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("greenlock")
    server.tool()(verify_patch)
    server.tool()(harden_and_verify)
    server.tool()(generate_characterization_tests)
    return server


def main():
    try:
        server = _build_server()
    except ModuleNotFoundError as e:
        raise SystemExit(
            "Нужен MCP SDK. Установи: pip install \"greenlock[mcp]\""
        ) from e
    server.run()


if __name__ == "__main__":
    main()
