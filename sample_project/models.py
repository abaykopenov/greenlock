"""Модель задачи."""
from dataclasses import dataclass, asdict


@dataclass
class Task:
    id: int
    title: str
    done: bool = False
    priority: str = "normal"

    def to_dict(self) -> dict:
        return asdict(self)
