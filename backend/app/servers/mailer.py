import os
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_USER


class MailerNotConfigured(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(SMTP_USER and SMTP_PASSWORD)


def send_email(to: str, subject: str, body: str) -> None:
    if not is_configured():
        raise MailerNotConfigured(
            "Email sending isn't set up yet — add SMTP_USER and SMTP_PASSWORD "
            "(a Gmail app password works) to the backend .env."
        )
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
