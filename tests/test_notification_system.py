import pytest
from src.notification import NotificationSystem

def test_line_notification():
    notifier = NotificationSystem()
    result = notifier.send_line("測試 LINE 通知")
    assert result is True
    assert ("LINE", "測試 LINE 通知") in notifier.sent_messages

def test_telegram_notification():
    notifier = NotificationSystem()
    result = notifier.send_telegram("測試 Telegram 通知")
    assert result is True
    assert ("Telegram", "測試 Telegram 通知") in notifier.sent_messages

def test_email_notification():
    notifier = NotificationSystem()
    result = notifier.send_email("user@example.com", "測試主題", "測試內容")
    assert result is True
    assert any("測試主題" in msg for channel, msg in notifier.sent_messages if channel == "Email")
