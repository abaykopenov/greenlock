"""Хранилище задач: читает и пишет JSON-файл tasks.json."""
import json
from pathlib import Path

from models import Task

DEFAULT_PATH = Path("tasks.json")


class TaskStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self._tasks: dict[int, Task] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            self._tasks = {int(k): Task(**v) for k, v in raw.items()}

    def _save(self) -> None:
        raw = {str(t.id): t.to_dict() for t in self._tasks.values()}
        self.path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))

    def add_task(self, title: str) -> Task:
        new_id = max(self._tasks, default=0) + 1
        task = Task(id=new_id, title=title, done=False)
        self._tasks[new_id] = task
        self._save()
        return task

    def get_task(self, task_id: int) -> Task | None:
        return self._tasks.get(task_id)

    def mark_done(self, task_id: int) -> None:
        self._tasks[task_id].done = True
        self._save()

    def delete_task(self, task_id: int) -> None:
        self._tasks.pop(task_id, None)
        self._save()

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())
