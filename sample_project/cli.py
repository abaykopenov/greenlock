"""CLI для задач: add / list / done."""
import argparse

from storage import TaskStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="tasks")
    sub = parser.add_subparsers(dest="cmd")

    add_p = sub.add_parser("add")
    add_p.add_argument("title")
    sub.add_parser("list")
    done_p = sub.add_parser("done")
    done_p.add_argument("id", type=int)

    args = parser.parse_args()
    store = TaskStore()

    if args.cmd == "add":
        task = store.add_task(args.title)
        print(f"added #{task.id}")
    elif args.cmd == "list":
        for task in store.all_tasks():
            mark = "x" if task.done else " "
            print(f"[{mark}] #{task.id} {task.title}")
    elif args.cmd == "done":
        store.mark_done(args.id)
        print("ok")


if __name__ == "__main__":
    main()
