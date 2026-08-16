import pytest
from src.gui import Dashboard

def test_dashboard_add_and_show(monkeypatch):
    dash = Dashboard()
    dash.add_price_data([100, 102, 105, 103, 108, 110])
    dash.add_signal("買入 AAPL")
    dash.add_account("acc1")
    dash.add_notification("LINE 通知: 策略觸發")

    # 模擬 plt.show() 不要真的開視窗
    monkeypatch.setattr("matplotlib.pyplot.show", lambda: True)

    dash.show()
    assert dash.data[-1] == 110
    assert dash.signals[0][1] == "買入 AAPL"
    assert dash.accounts[0] == "acc1"
    assert "LINE 通知" in dash.notifications[0][1]
