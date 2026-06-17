"""Напоминания о задачах по email через SMTP."""
import smtplib
from email.message import EmailMessage

from models import Task

SMTP_HOST = "localhost"
SMTP_PORT = 25


def send_reminder(task: Task, to_addr: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Напоминание: {task.title}"
    msg["To"] = to_addr
    msg.set_content(f"Задача #{task.id} ещё не выполнена.")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.send_message(msg)
