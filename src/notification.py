class NotificationSystem:
    """通知系統，支援 LINE、Telegram、Email 推送 (模擬版)"""

    def __init__(self):
        self.sent_messages = []

    def send_line(self, message: str) -> bool:
        # 模擬 LINE 推送
        self.sent_messages.append(("LINE", message))
        return True

    def send_telegram(self, message: str) -> bool:
        # 模擬 Telegram 推送
        self.sent_messages.append(("Telegram", message))
        return True

    def send_email(self, recipient: str, subject: str, body: str) -> bool:
        # 模擬 Email 推送
        self.sent_messages.append(("Email", f"To:{recipient} | {subject} | {body}"))
        return True
