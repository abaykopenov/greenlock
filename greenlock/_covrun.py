"""greenlock._covrun — subprocess-обёртка: pytest под трассировкой покрытия.

Вызов:  python -m greenlock._covrun <cov_out.json> <targets.json> <pytest args...>

Пишет в <cov_out.json> {реальный_путь_файла: [исполненные_строки]} и выходит с
кодом pytest. Отдельный процесс нужен, чтобы in-process pytest не пачкал гейт.
"""
import json
import sys

from greenlock.coverage import run_pytest_traced


def main() -> int:
    cov_out = sys.argv[1]
    targets = json.loads(sys.argv[2])
    pytest_args = sys.argv[3:]
    code, executed = run_pytest_traced(pytest_args, targets)
    try:
        with open(cov_out, "w", encoding="utf-8") as f:
            json.dump(executed, f)
    except OSError:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
