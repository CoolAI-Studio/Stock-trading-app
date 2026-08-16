import smtplib
from email.message import EmailMessage

from app.services.notification.base import SendResult


class EmailSender:
    def send(self, config: dict, message: str) -> SendResult:
        host = config.get("host")
        from_addr = config.get("from_addr")
        to_addr = config.get("to_addr")
        if not host or not from_addr or not to_addr:
            return SendResult(ok=False, error="missing host/from_addr/to_addr")

        port = config.get("port", 587)
        username = config.get("username")
        password = config.get("password")
        use_tls = config.get("use_tls", True)

        email_message = EmailMessage()
        email_message["Subject"] = "Trading App Notification"
        email_message["From"] = from_addr
        email_message["To"] = to_addr
        email_message.set_content(message)

        try:
            with smtplib.SMTP(host, port, timeout=10) as smtp:
                if use_tls:
                    smtp.starttls()
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(email_message)
            return SendResult(ok=True)
        except (smtplib.SMTPException, OSError) as exc:
            return SendResult(ok=False, error=str(exc))
