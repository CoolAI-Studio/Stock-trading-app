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
            return SendResult(ok=False, error=self._describe(exc))

    @staticmethod
    def _describe(exc: Exception) -> str:
        """錯誤字串要讓 retry._is_permanent 判得出來。

        這裡原本是 `str(exc)`，而 smtplib 給的是「(535, b'5.7.8 Username and Password
        not accepted')」——不以「HTTP <code>」開頭，所以**應用程式密碼失效永遠不會被判
        成永久失敗**。那件事很常發生（改密碼、關掉兩步驟驗證都會讓 Gmail 的應用程式密
        碼失效），而症狀是管道永遠寫著「啟用中」、每一則提醒都消失。

        用「SMTP <code>」而不是硬套成 HTTP：那不是 HTTP，寫成 HTTP 會讓下一個讀的人以
        為這裡有一個網頁請求。retry.py 那份清單同時認得兩種前綴。
        """
        code = getattr(exc, "smtp_code", None)
        if isinstance(code, int):
            return f"SMTP {code}"
        # 連不上、TLS 交涉失敗、逾時——都是暫時的，要繼續重試。
        return f"{type(exc).__name__}"
