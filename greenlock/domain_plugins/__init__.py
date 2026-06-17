"""domain_plugins — отключаемые доменные плагины для структурного слоя.

StructuralPlugin — контракт плагина. load_plugins() — загрузка с кэшированием.

plugins=None в structural_answer → авто-загрузка (дефолт = ВКЛ).
plugins=[] → явное отключение.
"""
from typing import Protocol

__all__ = ["StructuralPlugin", "load_plugins"]


class StructuralPlugin(Protocol):
    """Доменный плагин для structural_answer."""

    name: str

    def key_hints(self) -> dict[str, list[str]]:
        """Дополнительные маппинги 'фраза → имена ключей' для query_key_tokens."""
        ...

    def auth_files(self) -> set[str]:
        """Авторитетные корневые файлы для boost в _struct_score/_enumerate_list."""
        ...

    def extra_authoritative(self) -> set[str]:
        """Авторитетные имена файлов для path_score (расширяют ROOT_AUTHORITATIVE)."""
        ...

    def handlers(self, index: dict, query: str, qtoks: set[str]) -> list:
        """Доменные структурные хендлеры.

        Каждый элемент: (answer, cites, usage) или None.
        """
        ...


_PLUGIN_CACHE: list | None = None


def load_plugins(disabled: bool = False) -> list:
    """Загрузить все доменные плагины.

    disabled=True → пустой список (--no-plugins).
    Результат кэшируется на уровне модуля.
    """
    global _PLUGIN_CACHE
    if disabled:
        return []
    if _PLUGIN_CACHE is not None:
        return _PLUGIN_CACHE
    from greenlock.domain_plugins.doc_idioms import DocIdiomsPlugin
    from greenlock.domain_plugins.helm import HelmPlugin
    _PLUGIN_CACHE = [DocIdiomsPlugin(), HelmPlugin()]
    return _PLUGIN_CACHE
