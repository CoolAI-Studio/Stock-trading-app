import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import datetime
import random
import threading
import concurrent.futures
import time
import urllib.request
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import yfinance as yf

# ------------------ 解決 Matplotlib 中文亂碼 ------------------
matplotlib.rcParams['font.family'] = ['Microsoft JhengHei', 'PingFang TC', 'Heiti TC', 'SimHei', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False

# 引入核心模組
from src.risk_control import RiskControl
from src.strategy_loader import StrategyLoader
from src.framework import FutuAPI, TradingViewAPI
from src.notification import NotificationSystem
from src.account_manager import AccountManager, Account
from src.monitor import Monitor
from src.order import OrderSystem
from src.database_manager import DatabaseManager
from src.security import SecuritySystem
from run_daily_backtest import run_daily_backtest

class TradingSystemDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("多平台多指標高容量實時交易控制台 (符合 IEEE 12207 規範)")
        
        # 設定視窗大小與自適應最小限額
        self.root.geometry("1180x820")
        self.root.minsize(1100, 750)
        self.root.resizable(True, True)

        # 初始化核心業務邏輯模組
        self.rc = RiskControl(max_position=50)
        self.loader = StrategyLoader()
        self.futu_api = FutuAPI()
        self.tv_api = TradingViewAPI()
        self.notifier = NotificationSystem()
        self.account_manager = AccountManager()
        self.monitor = Monitor()
        self.order_system = OrderSystem()
        self.db = DatabaseManager("trading_app.db")
        self.security = SecuritySystem()

        # 狀態變數與執行緒鎖
        self.monitoring_active = False
        self.monitor_lock = threading.Lock()
        
        # 多標的歷史價格存儲 (Symbol -> List of Prices)
        self.monitored_prices = {}
        
        # 自訂腳本活動調度器
        self.active_strategies = {}

        # 建立 UI 分頁
        self.notebook = ttk.Notebook(root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self.setup_tab_monitoring()
        self.setup_tab_trading_risk()
        self.setup_tab_notifications_security()
        self.setup_tab_logs()

        # 寫入系統啟動日誌至資料庫
        self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "SYSTEM", "交易控制面板 GUI 順利啟動。")

    # ==================== 分頁 1: 策略與監控 ====================
    def setup_tab_monitoring(self):
        tab1 = ttk.Frame(self.notebook)
        self.notebook.add(tab1, text="策略載入與即時監控")

        # 左側面板: 策略載入器 (設定 width 限制寬度)
        left_frame = ttk.LabelFrame(tab1, text="策略腳本動態載入", padding=10, width=330)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=10, pady=10)
        left_frame.pack_propagate(False)  # 禁止子元件擠壓 LabelFrame 的固定寬度

        # 載入按鈕
        ttk.Button(left_frame, text="載入自訂交易策略腳本 (.py)", command=self.load_local_script, width=30).pack(pady=5)
        ttk.Button(left_frame, text="載入富途 API 策略訊號", command=self.load_futu_strategy, width=30).pack(pady=5)

        ttk.Label(left_frame, text="TradingView Webhook Payload (JSON):").pack(anchor=tk.W, pady=2)
        self.tv_payload_entry = ttk.Entry(left_frame, width=35)
        self.tv_payload_entry.insert(0, '{"symbol": "BTCUSD", "action": "SELL"}')
        self.tv_payload_entry.pack(pady=2)
        ttk.Button(left_frame, text="載入 TradingView Webhook 訊號", command=self.load_tv_webhook, width=30).pack(pady=5)

        ttk.Label(left_frame, text="已激活執行中策略列表:").pack(anchor=tk.W, pady=5)
        self.strategy_listbox = tk.Listbox(left_frame, height=12)
        self.strategy_listbox.pack(fill=tk.BOTH, expand=True)

        # 右側面板: 即時監控與折線圖 (設定 expand=True 使其隨視窗無限放大)
        right_frame = ttk.LabelFrame(tab1, text="多標的定時監控與行情均線 (K線走勢模擬)", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 控制列 1
        ctrl_frame = ttk.Frame(right_frame)
        ctrl_frame.pack(fill=tk.X, pady=2)

        ttk.Label(ctrl_frame, text="代號:").grid(row=0, column=0, padx=2)
        self.stock_entry = ttk.Entry(ctrl_frame, width=12)
        self.stock_entry.insert(0, "AAPL, TSLA, 2330, 2454")
        self.stock_entry.grid(row=0, column=1, padx=2)

        ttk.Label(ctrl_frame, text="頻度(秒):").grid(row=0, column=2, padx=2)
        self.monitor_interval_entry = ttk.Entry(ctrl_frame, width=3)
        self.monitor_interval_entry.insert(0, "5")
        self.monitor_interval_entry.grid(row=0, column=3, padx=2)

        self.btn_monitor_toggle = ttk.Button(ctrl_frame, text="開始監控", command=self.toggle_monitor)
        self.btn_monitor_toggle.grid(row=0, column=4, padx=5)

        self.chart_filter_combobox = ttk.Combobox(ctrl_frame, values=["顯示前5檔 (預設)"], width=13, state="readonly")
        self.chart_filter_combobox.set("顯示前5檔 (預設)")
        self.chart_filter_combobox.grid(row=0, column=5, padx=2)

        self.api_source_combobox = ttk.Combobox(ctrl_frame, values=["富途 OpenD API", "TradingView Webhook"], width=13, state="readonly")
        self.api_source_combobox.set("富途 OpenD API")
        self.api_source_combobox.grid(row=0, column=6, padx=2)

        self.data_mode_combobox = ttk.Combobox(ctrl_frame, values=["模擬隨機行情", "真實網路行情 (Binance)", "真實網路行情 (Yahoo-免費)"], width=21, state="readonly")
        self.data_mode_combobox.set("真實網路行情 (Yahoo-免費)")
        self.data_mode_combobox.grid(row=0, column=7, padx=2)

        # 一鍵清除與系統狀態重置紅色按鈕
        style = ttk.Style()
        style.configure("Red.TButton", foreground="red")
        self.btn_clear = ttk.Button(ctrl_frame, text="一鍵清除重置", style="Red.TButton", command=self.clear_all_data)
        self.btn_clear.grid(row=0, column=8, padx=10)

        # 控制列 2: 技術指標選擇
        ind_frame = ttk.Frame(right_frame)
        ind_frame.pack(fill=tk.X, pady=5)

        ttk.Label(ind_frame, text="疊加技術指標:").pack(side=tk.LEFT, padx=5)
        
        self.show_ma5_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ind_frame, text="MA5 均線", variable=self.show_ma5_var, command=self.plot_live_chart).pack(side=tk.LEFT, padx=5)

        self.show_ma20_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ind_frame, text="MA20 均線", variable=self.show_ma20_var, command=self.plot_live_chart).pack(side=tk.LEFT, padx=5)

        self.show_bb_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ind_frame, text="布林通道 (BB)", variable=self.show_bb_var, command=self.plot_live_chart).pack(side=tk.LEFT, padx=5)

        self.show_rsi_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ind_frame, text="強弱指標 (RSI)", variable=self.show_rsi_var, command=self.plot_live_chart).pack(side=tk.LEFT, padx=5)

        # 嵌入 Matplotlib 圖形
        self.chart_frame = ttk.Frame(right_frame)
        self.chart_frame.pack(fill=tk.BOTH, expand=True, pady=10)
        
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        self.plot_live_chart()

    # ==================== 分頁 2: 風險控制與交易下單 ====================
    def setup_tab_trading_risk(self):
        tab2 = ttk.Frame(self.notebook)
        self.notebook.add(tab2, text="風險控制與交易下單")

        # 左側面板: 即時風控與部位上限
        left_frame = ttk.LabelFrame(tab2, text="即時風險控制系統", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 風控參數
        ttk.Label(left_frame, text="主要買入成本價 ($):").pack(anchor=tk.W, pady=2)
        self.entry_price_var = tk.StringVar(value="100.0")
        ttk.Entry(left_frame, textvariable=self.entry_price_var).pack(fill=tk.X, pady=2)

        ttk.Label(left_frame, text="停損百分比 (%):").pack(anchor=tk.W, pady=2)
        self.sl_pct_var = tk.StringVar(value="10")
        ttk.Entry(left_frame, textvariable=self.sl_pct_var).pack(fill=tk.X, pady=2)

        ttk.Label(left_frame, text="停利百分比 (%):").pack(anchor=tk.W, pady=2)
        self.tp_var = tk.StringVar(value="15")
        ttk.Entry(left_frame, textvariable=self.tp_var).pack(fill=tk.X, pady=2)

        ttk.Label(left_frame, text="最大部位上限 (筆/股):").pack(anchor=tk.W, pady=2)
        self.max_pos_var = tk.StringVar(value="50")
        ttk.Entry(left_frame, textvariable=self.max_pos_var, state='disabled').pack(fill=tk.X, pady=2)

        # 狀態反饋
        status_frame = ttk.LabelFrame(left_frame, text="即時風控檢測狀態 (主標的)", padding=10)
        status_frame.pack(fill=tk.BOTH, expand=True, pady=15)

        self.lbl_live_price = ttk.Label(status_frame, text="當前監控價: --", font=("Arial", 12))
        self.lbl_live_price.pack(anchor=tk.W, pady=5)

        self.lbl_sl_status = ttk.Label(status_frame, text="停損觸發：否", font=("Arial", 11), foreground="green")
        self.lbl_sl_status.pack(anchor=tk.W, pady=5)

        self.lbl_tp_status = ttk.Label(status_frame, text="停利觸發：否", font=("Arial", 11), foreground="green")
        self.lbl_tp_status.pack(anchor=tk.W, pady=5)

        # 右側面板: 通訊軟體指令下單與多帳號管理
        right_frame = ttk.LabelFrame(tab2, text="多帳號管理與 LINE/Telegram 指令模擬下單", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # 帳號綁定區
        acc_frame = ttk.Frame(right_frame)
        acc_frame.pack(fill=tk.X, pady=5)
        ttk.Label(acc_frame, text="綁定新帳號:").pack(anchor=tk.W)
        self.acc_id_entry = ttk.Entry(acc_frame, width=15)
        self.acc_id_entry.pack(side=tk.LEFT, padx=2)
        self.acc_type_combobox = ttk.Combobox(acc_frame, values=["broker", "messenger"], width=10)
        self.acc_type_combobox.set("broker")
        self.acc_type_combobox.pack(side=tk.LEFT, padx=2)
        ttk.Button(acc_frame, text="綁定", command=self.add_acc).pack(side=tk.LEFT, padx=5)

        self.acc_listbox = tk.Listbox(right_frame, height=5)
        self.acc_listbox.pack(fill=tk.X, pady=5)

        # 下單模擬區
        order_frame = ttk.LabelFrame(right_frame, text="通訊管道指令下單模擬", padding=10)
        order_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        grid_f = ttk.Frame(order_frame)
        grid_f.pack(fill=tk.X)
        ttk.Label(grid_f, text="通訊軟體:").grid(row=0, column=0, padx=2, pady=2)
        self.msg_channel_combobox = ttk.Combobox(grid_f, values=["LINE", "Telegram"], width=10)
        self.msg_channel_combobox.set("LINE")
        self.msg_channel_combobox.grid(row=0, column=1, padx=2, pady=2)

        ttk.Label(grid_f, text="方向:").grid(row=0, column=2, padx=2, pady=2)
        self.msg_type_combobox = ttk.Combobox(grid_f, values=["BUY", "SELL"], width=10)
        self.msg_type_combobox.set("BUY")
        self.msg_type_combobox.grid(row=0, column=3, padx=2, pady=2)

        ttk.Label(grid_f, text="數量:").grid(row=0, column=4, padx=2, pady=2)
        self.msg_qty_entry = ttk.Entry(grid_f, width=8)
        self.msg_qty_entry.insert(0, "10")
        self.msg_qty_entry.grid(row=0, column=5, padx=2, pady=2)

        ttk.Button(order_frame, text="模擬傳送下單指令並執行", command=self.trigger_messenger_order).pack(pady=5)

        ttk.Label(order_frame, text="通訊管道委託紀錄:").pack(anchor=tk.W)
        self.order_history_listbox = tk.Listbox(order_frame, height=5)
        self.order_history_listbox.pack(fill=tk.BOTH, expand=True)

    # ==================== 分頁 3: 連線設定與安全認證 (完全重構，支援對稱縮放) ====================
    def setup_tab_notifications_security(self):
        tab3 = ttk.Frame(self.notebook)
        self.notebook.add(tab3, text="連線設定與安全認證")

        # 配置權重，使左右兩大塊在拉伸時保持對稱等比例放大
        tab3.columnconfigure(0, weight=1)
        tab3.columnconfigure(1, weight=1)
        tab3.rowconfigure(0, weight=4) # 設定框架高度權重
        tab3.rowconfigure(1, weight=0) # 按鈕區不放大
        tab3.rowconfigure(2, weight=1) # 安全認證區微幅放大

        # 1. 交易平台 API 連線設定 (左側)
        platform_frame = ttk.LabelFrame(tab3, text="交易平台與行情網關設定 (如 Futu OpenD)", padding=10)
        platform_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        ttk.Label(platform_frame, text="富途 OpenD 主機 (IP):").pack(anchor=tk.W, pady=2)
        self.futu_host_entry = ttk.Entry(platform_frame)
        self.futu_host_entry.insert(0, "127.0.0.1")
        self.futu_host_entry.pack(fill=tk.X, pady=2)

        ttk.Label(platform_frame, text="富途 OpenD 埠號 (Port):").pack(anchor=tk.W, pady=2)
        self.futu_port_entry = ttk.Entry(platform_frame)
        self.futu_port_entry.insert(0, "11111")
        self.futu_port_entry.pack(fill=tk.X, pady=2)

        ttk.Label(platform_frame, text="富途交易環境 (Environment):").pack(anchor=tk.W, pady=2)
        self.futu_env_combobox = ttk.Combobox(platform_frame, values=["SIMULATE", "REAL"], state="readonly")
        self.futu_env_combobox.set("SIMULATE")
        self.futu_env_combobox.pack(fill=tk.X, pady=2)

        ttk.Separator(platform_frame, orient="horizontal").pack(fill=tk.X, pady=10)

        ttk.Label(platform_frame, text="TradingView Webhook 接收 URL:").pack(anchor=tk.W, pady=2)
        self.tv_url_entry = ttk.Entry(platform_frame)
        self.tv_url_entry.insert(0, "http://127.0.0.1:5000/webhook")
        self.tv_url_entry.pack(fill=tk.X, pady=2)

        ttk.Label(platform_frame, text="TradingView Webhook 認證 Token:").pack(anchor=tk.W, pady=2)
        self.tv_secret_entry = ttk.Entry(platform_frame, show="*")
        self.tv_secret_entry.insert(0, "MyTvSecret_98765")
        self.tv_secret_entry.pack(fill=tk.X, pady=2)

        # 2. 外部通訊通知與 SMTP 設定 (右側)
        notify_frame = ttk.LabelFrame(tab3, text="外部通訊與通知密鑰設定", padding=10)
        notify_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)

        ttk.Label(notify_frame, text="LINE Notify 權杖 (Token):").pack(anchor=tk.W, pady=2)
        self.line_token = ttk.Entry(notify_frame)
        self.line_token.insert(0, "Dummy_LINE_Token_12345")
        self.line_token.pack(fill=tk.X, pady=2)

        ttk.Label(notify_frame, text="Telegram Bot Token:").pack(anchor=tk.W, pady=2)
        self.tg_token = ttk.Entry(notify_frame)
        self.tg_token.insert(0, "Dummy_TG_Token_abcde")
        self.tg_token.pack(fill=tk.X, pady=2)

        ttk.Label(notify_frame, text="Telegram Chat ID:").pack(anchor=tk.W, pady=2)
        self.tg_chat_id = ttk.Entry(notify_frame)
        self.tg_chat_id.insert(0, "123456789")
        self.tg_chat_id.pack(fill=tk.X, pady=2)

        ttk.Separator(notify_frame, orient="horizontal").pack(fill=tk.X, pady=10)

        # SMTP Email 信箱設定網格
        email_grp = ttk.Frame(notify_frame)
        email_grp.pack(fill=tk.X, pady=2)
        
        ttk.Label(email_grp, text="SMTP 伺服器:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.smtp_server_entry = ttk.Entry(email_grp, width=15)
        self.smtp_server_entry.insert(0, "smtp.gmail.com")
        self.smtp_server_entry.grid(row=0, column=1, padx=2, pady=2)

        ttk.Label(email_grp, text="埠號:").grid(row=0, column=2, sticky=tk.W, pady=2)
        self.smtp_port_entry = ttk.Entry(email_grp, width=5)
        self.smtp_port_entry.insert(0, "465")
        self.smtp_port_entry.grid(row=0, column=3, padx=2, pady=2)

        ttk.Label(notify_frame, text="寄件人信箱:").pack(anchor=tk.W, pady=2)
        self.email_sender = ttk.Entry(notify_frame)
        self.email_sender.insert(0, "sender@example.com")
        self.email_sender.pack(fill=tk.X, pady=2)

        ttk.Label(notify_frame, text="寄件人密碼 (或授權密鑰):").pack(anchor=tk.W, pady=2)
        self.email_password = ttk.Entry(notify_frame, show="*")
        self.email_password.insert(0, "mypassword123")
        self.email_password.pack(fill=tk.X, pady=2)

        ttk.Label(notify_frame, text="收件人信箱:").pack(anchor=tk.W, pady=2)
        self.email_recipient = ttk.Entry(notify_frame)
        self.email_recipient.insert(0, "receiver@example.com")
        self.email_recipient.pack(fill=tk.X, pady=2)

        # 實測按鈕區
        test_btn_frame = ttk.LabelFrame(tab3, text="即時發送測試 (若非預設虛擬 Token 則會自動發送真實訊息)", padding=10)
        test_btn_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=5)
        
        ttk.Button(test_btn_frame, text="發送 LINE Notify 實測", command=self.test_line_push).pack(side=tk.LEFT, padx=10, expand=True)
        ttk.Button(test_btn_frame, text="發送 Telegram Bot 實測", command=self.test_tg_push).pack(side=tk.LEFT, padx=10, expand=True)
        ttk.Button(test_btn_frame, text="發送 Email 警報實測", command=self.test_email_push).pack(side=tk.LEFT, padx=10, expand=True)

        # 3. OTP 安全認證 (置於最下方)
        security_frame = ttk.LabelFrame(tab3, text="安全帳戶登入與下單雙因素 (OTP) 認證", padding=10)
        security_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=10)

        # OTP 網格佈局
        sec_f = ttk.Frame(security_frame)
        sec_f.pack(fill=tk.X)
        
        ttk.Label(sec_f, text="使用者:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.sec_user_entry = ttk.Entry(sec_f, width=15)
        self.sec_user_entry.insert(0, "alice")
        self.sec_user_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(sec_f, text="密碼:").grid(row=0, column=2, sticky=tk.W, padx=5, pady=2)
        self.sec_pwd_entry = ttk.Entry(sec_f, show="*", width=15)
        self.sec_pwd_entry.insert(0, "password123")
        self.sec_pwd_entry.grid(row=0, column=3, padx=5, pady=2)

        ttk.Button(sec_f, text="註冊", command=self.reg_user).grid(row=0, column=4, padx=5, pady=2)
        ttk.Button(sec_f, text="密碼認證並發送 OTP", command=self.auth_user).grid(row=0, column=5, padx=5, pady=2)

        self.sec_otp_display = ttk.Label(security_frame, text="OTP 驗證碼 (模擬發送至手機): --", font=("Arial", 9, "bold"), foreground="blue")
        self.sec_otp_display.pack(anchor=tk.W, pady=5)

        verify_f = ttk.Frame(security_frame)
        verify_f.pack(fill=tk.X)
        ttk.Label(verify_f, text="輸入 OTP 驗證碼:").pack(side=tk.LEFT, padx=5)
        self.sec_otp_entry = ttk.Entry(verify_f, width=10)
        self.sec_otp_entry.pack(side=tk.LEFT, padx=5)
        ttk.Button(verify_f, text="驗證 OTP 啟用系統權限", command=self.verify_otp_code).pack(side=tk.LEFT, padx=5)

    # ==================== 分頁 4: 日誌與歷史回測 ====================
    def setup_tab_logs(self):
        tab4 = ttk.Frame(self.notebook)
        self.notebook.add(tab4, text="資料庫日誌與每日回測")

        # 左側: 資料庫 Log 讀取器
        left_frame = ttk.LabelFrame(tab4, text="交易日誌與行情歷史紀錄 (SQL 查詢結果)", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        ctrl_f = ttk.Frame(left_frame)
        ctrl_f.pack(fill=tk.X, pady=5)
        ttk.Button(ctrl_f, text="手動整理/刷新資料庫日誌", command=self.refresh_logs).pack(side=tk.LEFT)

        # 日誌列表 (Treeview)
        cols = ("Time", "Category", "Message")
        self.log_tree = ttk.Treeview(left_frame, columns=cols, show="headings")
        for col in cols:
            self.log_tree.heading(col, text=col)
            self.log_tree.column(col, width=150 if col != "Message" else 250)
        self.log_tree.pack(fill=tk.BOTH, expand=True)

        # 右側: 每日回測
        right_frame = ttk.LabelFrame(tab4, text="每日回測策略與報告生成", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        ttk.Button(right_frame, text="點擊執行每日回測 (Daily_MA_Cross)", command=self.execute_backtest, width=35).pack(pady=10)
        
        self.backtest_result_text = tk.Text(right_frame, height=15)
        self.backtest_result_text.pack(fill=tk.BOTH, expand=True)

    # ==================== 策略載入與即時監控邏輯 ====================
    def load_local_script(self):
        file_path = filedialog.askopenfilename(title="選擇本地自訂策略腳本", filetypes=[("Python 檔案", "*.py")])
        if file_path:
            name = "Strategy_" + datetime.datetime.now().strftime("%Y%m%d%M%S")
            try:
                self.loader.load_pipe_script(file_path, name)
                
                module = self.loader.loaded_strategies[name]
                if hasattr(module, "Strategy"):
                    strategy_instance = module.Strategy()
                    
                    strat_name = getattr(strategy_instance, "name", "未命名策略")
                    strat_symbol = getattr(strategy_instance, "symbol", "AAPL").upper()
                    
                    # 註冊至活動調度器
                    self.active_strategies[name] = strategy_instance
                    
                    # 顯示於 GUI 策略清單中
                    self.strategy_listbox.insert(tk.END, f"[腳本] {strat_name} | 標的: {strat_symbol}")
                    
                    # 同步更新圖表顯示下拉選單中的標的值
                    self.update_chart_combobox()
                    
                    messagebox.showinfo("成功", f"成功載入並激活交易策略：{strat_name}\n綁定監控標的：{strat_symbol}")
                    self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Strategy", f"動態加載並啟用策略：{strat_name} ({strat_symbol})")
                else:
                    messagebox.showwarning("警告", "載入成功，但該腳本未包含『class Strategy』標準規格類別！")
            except Exception as e:
                messagebox.showerror("錯誤", f"載入策略失敗: {str(e)}")

    def load_futu_strategy(self):
        # 動態載入富途連線設定參數
        host = self.futu_host_entry.get()
        port = self.futu_port_entry.get()
        env = self.futu_env_combobox.get()
        symbol = "AAPL"
        
        sig = self.loader.load_from_futu(symbol)
        self.strategy_listbox.insert(tk.END, f"[富途訊號] {symbol} -> {sig['action']}")
        messagebox.showinfo("富途連接成功", f"從富途載入策略訊號成功！\n主機: {host}:{port}\n環境: {env}\n訊號: {json.dumps(sig, indent=2)}")
        self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Strategy", f"從富途網關 {host}:{port} ({env}) 獲取 {symbol} 行情訊號。")

    def load_tv_webhook(self):
        url = self.tv_url_entry.get()
        secret = self.tv_secret_entry.get()
        payload = self.tv_payload_entry.get()
        try:
            sig = self.loader.load_from_tradingview(payload)
            self.strategy_listbox.insert(tk.END, f"[TradingView Webhook] {sig['symbol']} -> {sig['action']}")
            messagebox.showinfo("TV 連接成功", f"TradingView 訊號解析成功！\n目標Webhook: {url}\n訊號: {json.dumps(sig, indent=2)}")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Strategy", f"TradingView Webhook (SecToken: {secret}) 訊號載入: {sig['symbol']}")
        except Exception as e:
            messagebox.showerror("錯誤", f"JSON 格式解析失敗: {str(e)}")

    def update_chart_combobox(self):
        # 獲取當前所有已知標的並寫入下拉選單
        raw_symbols = self.stock_entry.get().strip()
        symbols = set()
        if raw_symbols:
            symbols.update(s.strip().upper() for s in raw_symbols.split(",") if s.strip())
        symbols.update(getattr(strat, "symbol", "").upper() for strat in self.active_strategies.values() if getattr(strat, "symbol", ""))
        
        # 對台股代碼進行防呆自動轉換 (如 2330 轉換為 2330.TW)
        clean_symbols = set()
        for s in symbols:
            if s.isdigit() and len(s) == 4:
                clean_symbols.add(s + ".TW")
            else:
                clean_symbols.add(s)

        vals = ["顯示前5檔 (預設)"] + sorted(list(clean_symbols))
        self.chart_filter_combobox.config(values=vals)

    def toggle_monitor(self):
        if self.monitoring_active:
            self.monitoring_active = False
            self.btn_monitor_toggle.config(text="開始監控")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "MONITOR", "使用者停止高容量監控。")
        else:
            self.monitoring_active = True
            self.btn_monitor_toggle.config(text="停止監控")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "MONITOR", "啟動高容量並行監控執行緒。")
            
            # 更新下拉選單
            self.update_chart_combobox()
            
            # 將拉取行情流程，從主執行緒剝離至獨立的背景守護執行緒中
            threading.Thread(target=self.background_monitor_loop, daemon=True).start()

    # ==================== 一鍵清除與系統状态重置紅色按鈕邏輯 ====================
    def clear_all_data(self):
        # 1. 停止監控線程
        if self.monitoring_active:
            self.toggle_monitor()
            
        # 2. 清空歷史價格字典
        with self.monitor_lock:
            self.monitored_prices.clear()
            
        # 3. 清除所有已啟用策略與清單
        self.active_strategies.clear()
        self.strategy_listbox.delete(0, tk.END)
        
        # 4. 清除下單委託與帳號清單
        self.acc_listbox.delete(0, tk.END)
        self.order_history_listbox.delete(0, tk.END)
        self.order_system.orders.clear() # 清空底層訂單資料
        
        # 5. 更新選單過濾器
        self.update_chart_combobox()
        
        # 6. 強制銷毀並重新初始化 Matplotlib 圖形，清除任何殘留子圖
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title("系統已成功重置 — 請重新載入策略、配置標的並啟動監控")
        self.ax.set_xlabel("時間刻度 (Tick)")
        self.ax.set_ylabel("價格 ($)")
        self.fig.tight_layout()
        self.canvas.draw()
        
        # 7. 寫入重置日誌
        self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "SYSTEM", "使用者觸發『一鍵清除與系統重置』操作。")
        messagebox.showinfo("系統重置", "所有歷史價格快取、加載策略、成交紀錄已成功清空還原！")

    # ==================== 高併發行情定時監控多執行緒核心 ====================
    def background_monitor_loop(self):
        while self.monitoring_active:
            data_mode = self.data_mode_combobox.get()
            
            # 1. 取得需要監控的所有標的
            raw_symbols = self.stock_entry.get().strip()
            if raw_symbols:
                symbols = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()]
            else:
                # 智慧壓力測試：若無代碼也無策略，自動激活 200 檔股票進行高容量監控測試 (前 7 檔為真實股票，後 193 檔為虛擬)
                symbols = ["AAPL", "TSLA", "MSFT", "NVDA", "AMD", "2330", "2454"] + [f"STK{i:03d}" for i in range(1, 194)]
                self.root.after(0, lambda: self.db.insert_log(
                    datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
                    "SYSTEM", 
                    "「200檔智慧壓力測試啟用」：前 7 檔為真實美台股，後 193 檔為自動補足之虛擬股票！"
                ))
            
            # 將台股代碼（如 2330）自動補上 .TW 格式
            clean_symbols = []
            for s in symbols:
                if s.isdigit() and len(s) == 4:
                    clean_symbols.append(s + ".TW")
                else:
                    clean_symbols.append(s)

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # 2. 判斷數據獲取模式
            if data_mode == "真實網路行情 (Yahoo-免費)":
                # ================= 終極免費 200 檔解決方案：Yahoo Finance 批量打包下載 =================
                tickers_str = " ".join(clean_symbols)
                try:
                    # 發送「單一一次」請求獲取 200 檔，完美規避 Yahoo 429 頻率限制！
                    data = yf.download(tickers=tickers_str, period="1d", interval="1m", group_by="ticker", progress=False)
                    
                    for symbol in clean_symbols:
                        try:
                            # 提取價格
                            if len(clean_symbols) == 1:
                                price = float(data['Close'].iloc[-1])
                            else:
                                price = float(data[symbol]['Close'].iloc[-1])
                                
                            if np.isnan(price):
                                if "STK" in symbol:
                                    raise Exception("STK fallback")
                                continue
                                
                            with self.monitor_lock:
                                if symbol not in self.monitored_prices:
                                    self.monitored_prices[symbol] = []
                                self.monitored_prices[symbol].append(price)
                                if len(self.monitored_prices[symbol]) > 30:
                                    self.monitored_prices[symbol].pop(0)

                            self.root.after(0, lambda s=symbol, p=price: self.db.insert_log(now_str, "MarketData", f"(Yahoo) 標的 {s} 實時價: {p}"))
                            self.root.after(0, lambda s=symbol, p=price: self.evaluate_strategies_and_risk(s, p, now_str))
                        except:
                            # 虛擬標的自動備援降級
                            if "STK" in symbol:
                                price = self.get_simulated_price(symbol, "Futu")
                                with self.monitor_lock:
                                    if symbol not in self.monitored_prices:
                                        self.monitored_prices[symbol] = [round(100.0 + random.uniform(-5,5), 2) for _ in range(5)]
                                    self.monitored_prices[symbol].append(price)
                                    if len(self.monitored_prices[symbol]) > 30:
                                        self.monitored_prices[symbol].pop(0)
                                self.root.after(0, lambda s=symbol, p=price: self.db.insert_log(now_str, "MarketData", f"(模擬壓測) 標的 {s} 實時價: {p}"))
                                self.root.after(0, lambda s=symbol, p=price: self.evaluate_strategies_and_risk(s, p, now_str))
                except Exception as e:
                    self.root.after(0, lambda err=str(e): self.db.insert_log(now_str, "SYSTEM", f"[Error] Yahoo 行情下載失敗: {err}，暫時轉為模擬備援"))
                    # 網絡出錯時，全量跑模擬
                    for symbol in clean_symbols:
                        self.fetch_and_evaluate_single_asset(symbol, now_str)
            else:
                # 幣安或純模擬：使用多執行緒池並發拉取
                with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                    futures = [executor.submit(self.fetch_and_evaluate_single_asset, symbol, now_str) for symbol in clean_symbols]
                    concurrent.futures.wait(futures)

            # 3. 委託主 GUI 執行緒刷新圖表 (線程安全)
            self.root.after(0, self.plot_live_chart)

            # 讀取定時器頻度
            try:
                interval = float(self.monitor_interval_entry.get())
            except:
                interval = 2.0
            
            time.sleep(interval)

    # 獲取網路真實即時價格 (Binance 公開免 Key 接口)
    def fetch_binance_live_price(self, symbol):
        try:
            sym = symbol.upper().strip()
            # 轉換為幣安可接受對 U 交易對 (例如 BTC -> BTCUSDT)
            if sym in ["BTC", "BTCUSD"]:
                sym = "BTCUSDT"
            elif sym in ["ETH", "ETHUSD"]:
                sym = "ETHUSDT"
            elif "USDT" not in sym:
                sym = sym + "USDT"

            url = f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                return float(data["price"])
        except:
            return None # 失敗則自動進入降級備援

    def fetch_and_evaluate_single_asset(self, symbol, now_str):
        data_mode = self.data_mode_combobox.get()
        api_source = self.api_source_combobox.get()

        # 智慧切換真實或模擬數據源
        if data_mode == "真實網路行情 (Binance)":
            price = self.fetch_binance_live_price(symbol)
            if price is None:
                # 網路離線或輸入台股/美股時自動優雅降級回模擬
                price = self.get_simulated_price(symbol, api_source)
        else:
            price = self.get_simulated_price(symbol, api_source)

        # 線程安全寫入價格存儲
        with self.monitor_lock:
            if symbol not in self.monitored_prices:
                base_p = 150.0 if "AAPL" in symbol else 180.0
                if "STK" in symbol:
                    try:
                        base_p = 50.0 + (int(symbol[3:]) * 1.5)
                    except:
                        pass
                self.monitored_prices[symbol] = [round(base_p + random.uniform(-6, 6), 2) for _ in range(5)]
            
            self.monitored_prices[symbol].append(price)
            if len(self.monitored_prices[symbol]) > 30:
                self.monitored_prices[symbol].pop(0)

        # 委託主執行緒進行 SQLite 日誌寫入
        self.root.after(0, lambda s=symbol, p=price: self.db.insert_log(now_str, "MarketData", f"({data_mode}) 標的 {s} 行情價格: {p}"))

        # 委託主執行緒執行策略與風控評估
        self.root.after(0, lambda s=symbol, p=price: self.evaluate_strategies_and_risk(s, p, now_str))

    def get_simulated_price(self, symbol, api_source):
        if api_source == "TradingView Webhook":
            raw_price = self.tv_api.get_price(symbol) # TradingViewAPI 預設基價為 200.0
            base_price = raw_price
        else:
            raw_price = self.futu_api.get_price(symbol) # FutuAPI 預設基價為 100.0
            base_price = raw_price
        
        # 依照標的自適應基準價波動
        if "TSLA" in symbol:
            base_price += 50.0
        elif "STK" in symbol:
            try:
                base_price += (int(symbol[3:]) * 1.5)
            except:
                pass
                
        return round(base_price + random.uniform(-6.0, 6.0), 2)

    def evaluate_strategies_and_risk(self, symbol, price, now_str):
        # 手動風控主標的面板顯示狀態更新
        self.check_risk_auto_for_symbol(symbol, price)

        # 策略事件行情分派
        for strat_key, strat_instance in list(self.active_strategies.items()):
            strat_symbol = getattr(strat_instance, "symbol", "").upper()
            strat_name = getattr(strat_instance, "name", "未命名策略")
            
            if strat_symbol == symbol:
                try:
                    signal = strat_instance.on_tick(price)
                    if signal in ["BUY", "SELL"]:
                        self.db.insert_log(now_str, "StrategySignal", f"策略 [{strat_name}] 在 {symbol} 現價 {price} 發出 {signal} 決策")
                        
                        # 風控持倉檢查
                        qty = 10
                        if not self.rc.check_position_limit(qty):
                            self.db.insert_log(now_str, "RiskAlert", f"下單攔截：策略 [{strat_name}] ({symbol}) 部位超出上限！")
                            continue
                            
                        # 執行下單
                        self.order_system.place_order_via_line(symbol, qty, signal)
                        self.rc.update_position(qty)
                        self.refresh_order_history()
                        
                        # LINE 警報通知 (動態實測與安全過濾)
                        token = self.line_token.get()
                        if token:
                            msg = (
                                f"🔔【多標的自動交易警報】\n"
                                f"行情來源: {self.data_mode_combobox.get()}\n"
                                f"觸發策略: {strat_name}\n"
                                f"監控標的: {symbol}\n"
                                f"決策方向: {signal}\n"
                                f"當前價格: {price} USD\n"
                                f"成交時間: {now_str}"
                            )
                            if token != "Dummy_LINE_Token_12345":
                                # 動態發送真實推播！
                                self.notifier.send_real_line(token, msg)
                            else:
                                self.notifier.send_line(msg)
                except Exception as e:
                    self.db.insert_log(now_str, "StrategyError", f"策略 [{strat_name}] 執行錯誤: {str(e)}")

    def plot_live_chart(self):
        self.fig.clear()
        
        # 1. 決定子圖佈局：若勾選了 RSI，則切分為上下兩個子圖 (主圖 3 : RSI圖 1 的 TradingView 風格比例)
        if self.show_rsi_var.get():
            axes = self.fig.subplots(2, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1]})
            ax1 = axes[0]
            ax2 = axes[1]
        else:
            ax1 = self.fig.add_subplot(111)
            ax2 = None
        
        # 讀取下拉過濾選項，避免 200 檔股票同時在單一畫面上渲染造成 CPU 崩潰
        filter_val = self.chart_filter_combobox.get()
        
        with self.monitor_lock:
            all_items = list(self.monitored_prices.items())
            
        if not all_items:
            ax1.set_title("無監控行情數據")
            self.canvas.draw()
            return

        # 智慧過濾降噪邏輯
        if filter_val == "顯示前5檔 (預設)" or not filter_val:
            plot_items = all_items[:5]
            title_suffix = f"(僅顯示前 5 檔 / 後台正定時監控共 {len(all_items)} 檔股票)"
        else:
            plot_items = [item for item in all_items if item[0] == filter_val]
            title_suffix = f"(圖表過濾單一標的: {filter_val} / 總監控共 {len(all_items)} 檔)"

        # 2. 繪製選定標的與各經典技術指標 (使用 pandas 計算)
        for symbol, prices in plot_items:
            if prices:
                # 繪製主價格收盤價曲線
                line = ax1.plot(prices, label=f"{symbol} 實時收盤價", linewidth=1.5)
                color = line[0].get_color()
                
                df = pd.DataFrame(prices, columns=["close"])
                
                # 指標 1: MA5 均線
                if self.show_ma5_var.get() and len(prices) >= 5:
                    df["ma5"] = df["close"].rolling(5).mean()
                    ax1.plot(df["ma5"], label=f"{symbol} MA5", linestyle="--", color=color, alpha=0.8)
                
                # 指標 2: MA20 均線
                if self.show_ma20_var.get() and len(prices) >= 20:
                    df["ma20"] = df["close"].rolling(20).mean()
                    ax1.plot(df["ma20"], label=f"{symbol} MA20", linestyle=":", color=color, alpha=0.9, linewidth=2)

                # 指標 3: 布林通道 Bollinger Bands (BB)
                if self.show_bb_var.get() and len(prices) >= 20:
                    df["ma20"] = df["close"].rolling(20).mean()
                    df["std20"] = df["close"].rolling(20).std()
                    df["bb_upper"] = df["ma20"] + 2 * df["std20"]
                    df["bb_lower"] = df["ma20"] - 2 * df["std20"]
                    ax1.plot(df["bb_upper"], linestyle="-.", color="red", alpha=0.35, label=f"{symbol} BB上軌")
                    ax1.plot(df["bb_lower"], linestyle="-.", color="red", alpha=0.35, label=f"{symbol} BB下軌")
                    ax1.fill_between(range(len(prices)), df["bb_lower"], df["bb_upper"], color="red", alpha=0.04)

                # 指標 4: 強弱指標 RSI 14 (單獨渲染於下方子圖)
                if ax2 is not None and len(prices) >= 14:
                    delta = df["close"].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                    rs = gain / loss
                    df["rsi"] = 100 - (100 / (1 + rs))
                    df["rsi"] = df["rsi"].fillna(50)
                    
                    ax2.plot(df["rsi"], color=color, label=f"{symbol} RSI(14)")
                    ax2.axhline(70, color="red", linestyle=":", alpha=0.5)
                    ax2.axhline(30, color="green", linestyle=":", alpha=0.5)
                    ax2.set_ylim(10, 90)
                    ax2.set_ylabel("RSI 指標值", fontsize=8)
                    ax2.legend(loc="upper left", prop={'size': 7})

        ax1.legend(loc="upper left", prop={'size': 7})
        ax1.set_title(f"多標的實時高負載監控走勢 {title_suffix}", fontsize=9)
        ax1.set_ylabel("價格 ($)")
        
        if ax2 is None:
            ax1.set_xlabel("時間刻度 (Tick)")
        else:
            ax2.set_xlabel("時間刻度 (Tick)")

        self.fig.tight_layout()
        self.canvas.draw()

    # ==================== 風險控制與交易邏輯 ====================
    def check_risk_auto_for_symbol(self, symbol, current_price):
        raw_symbols = self.stock_entry.get().strip()
        primary_symbol = "AAPL"
        if raw_symbols:
            primary_symbol = [s.strip().upper() for s in raw_symbols.split(",") if s.strip()][0]
            
        if symbol == primary_symbol:
            try:
                entry_price = float(self.entry_price_var.get())
                sl_pct = float(self.sl_pct_var.get()) / 100.0
                tp_pct = float(self.tp_var.get()) / 100.0
            except:
                return

            is_sl = self.rc.check_stop_loss(entry_price, current_price, stop_loss_pct=sl_pct)
            is_tp = self.rc.check_take_profit(entry_price, current_price, take_profit_pct=tp_pct)

            self.lbl_live_price.config(text=f"當前主監控價 ({symbol}): {current_price}")

            if is_sl:
                self.lbl_sl_status.config(text=f"停損觸發：是 ({symbol})", foreground="red")
                self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "RiskAlert", f"[停損警報] 主標的 {symbol} 現價: {current_price} 觸發停損")
                token = self.line_token.get()
                if token:
                    msg = f"🚨【停損警報】主標的 {symbol} 已觸發手動設定之停損點！"
                    if token != "Dummy_LINE_Token_12345":
                        self.notifier.send_real_line(token, msg)
                    else:
                        self.notifier.send_line(msg)
            else:
                self.lbl_sl_status.config(text="停損觸發：否", foreground="green")

            if is_tp:
                self.lbl_tp_status.config(text=f"停利觸發：是 ({symbol})", foreground="orange")
                self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "RiskAlert", f"[停利警報] 主標的 {symbol} 現價: {current_price} 觸發停利")
                token = self.line_token.get()
                if token:
                    msg = f"🚨【停利警報】主標的 {symbol} 已觸發手動設定之停利點！"
                    if token != "Dummy_LINE_Token_12345":
                        self.notifier.send_real_line(token, msg)
                    else:
                        self.notifier.send_line(msg)
            else:
                self.lbl_tp_status.config(text="停利觸發：否", foreground="green")

    def add_acc(self):
        acc_id = self.acc_id_entry.get()
        acc_type = self.acc_type_combobox.get()
        if not acc_id:
            messagebox.showwarning("警告", "請輸入帳號 ID")
            return
        
        acc = Account(acc_id, acc_type)
        acc.grant_permission("trade")
        acc.grant_permission("notify")
        
        if self.account_manager.add_account(acc):
            self.acc_listbox.insert(tk.END, f"[{acc_type}] ID: {acc_id} (權限: trade, notify)")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Account", f"綁定帳戶成功: {acc_id} [{acc_type}]")
            messagebox.showinfo("成功", f"成功綁定帳號 {acc_id}")
        else:
            messagebox.showwarning("錯誤", "帳號已重複綁定")

    def trigger_messenger_order(self):
        channel = self.msg_channel_combobox.get()
        symbol = "AAPL"
        try:
            qty = int(self.msg_qty_entry.get())
        except:
            qty = 10
        order_type = self.msg_type_combobox.get()

        if not self.rc.check_position_limit(qty):
            messagebox.showerror("風控攔截", "下單失敗！新增此部位將超出最大部位限制 (50)！")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "RiskAlert", f"下單 {symbol} 攔截：超出持倉限制！")
            return

        if channel == "LINE":
            self.order_system.place_order_via_line(symbol, qty, order_type)
        else:
            self.order_system.place_order_via_telegram(symbol, qty, order_type)

        self.rc.update_position(qty)

        self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Order", f"通訊下單 [{channel}指令]: {order_type} {symbol} 數量: {qty}")
        messagebox.showinfo("通訊下單成功", f"通訊管道委託成功發送！\n管道: {channel}\n委託: {order_type} {symbol} x {qty}")
        self.refresh_order_history()

    # ==================== 通知實測動態串接機制 ====================
    def test_line_push(self):
        token = self.line_token.get()
        msg = f"🔔 [LINE 交易控制台實測] 行情來源: {self.api_source_combobox.get()} 警報測試成功。"
        
        # 實測辨識防呆
        if token and token != "Dummy_LINE_Token_12345":
            success = self.notifier.send_real_line(token, msg)
            if success:
                self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Notification", "成功發送真實 LINE 通知")
                messagebox.showinfo("實測成功", "已成功將『真實即時警報』推送至您的 LINE 聊天室！")
            else:
                messagebox.showerror("實測失敗", "LINE 發送失敗，請確認您的 Token 是否有效、以及網路是否連通。")
        else:
            self.notifier.send_line(msg)
            messagebox.showinfo("模擬通知", f"LINE 模擬推送成功 (暫用預設測試金鑰)\nToken: {token}\n訊息: {msg}")

    def test_tg_push(self):
        token = self.tg_token.get()
        chat_id = self.tg_chat_id.get()
        msg = "[Telegram 交易控制台實測] 跨平台安全通道建立成功。"
        
        if token and token != "Dummy_TG_Token_abcde" and chat_id and chat_id != "123456789":
            success = self.notifier.send_real_telegram(token, chat_id, msg)
            if success:
                self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Notification", "成功發送真實 Telegram 訊息")
                messagebox.showinfo("實測成功", "Telegram 真實訊息已成功發送！")
            else:
                messagebox.showerror("實測失敗", "Telegram 發送失敗，請確認 Token 與 Chat ID。")
        else:
            self.notifier.send_telegram(msg)
            messagebox.showinfo("模擬通知", f"Telegram 模擬推送成功\nToken: {token}")

    def test_email_push(self):
        smtp_srv = self.smtp_server_entry.get()
        try:
            port = int(self.smtp_port_entry.get())
        except:
            port = 465
        sender = self.email_sender.get()
        pwd = self.email_password.get()
        recipient = self.email_recipient.get()
        
        if sender and sender != "sender@example.com" and recipient and recipient != "receiver@example.com":
            success = self.notifier.send_real_email(smtp_srv, port, sender, pwd, recipient, "交易控制台實測郵件", "這是一封來自您的交易主控台的真實發送警報郵件！")
            if success:
                messagebox.showinfo("實測成功", f"Email 實測信件已成功寄發至 {recipient}！")
            else:
                messagebox.showerror("實測失敗", "Email 發送失敗，請確認您的 SMTP 伺服器、埠號、密碼設定是否正確。")
        else:
            self.notifier.send_email(recipient, "系統自動警報", "風控核心正常啟動中。")
            messagebox.showinfo("模擬通知", f"Email 模擬通知發送成功\n收件人: {recipient}")

    def reg_user(self):
        u = self.sec_user_entry.get()
        p = self.sec_pwd_entry.get()
        if not u or not p:
            messagebox.showwarning("警告", "帳號或密碼不得為空！")
            return
        if self.security.register_user(u, p):
            messagebox.showinfo("註冊成功", f"帳戶 {u} 註冊成功。")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Security", f"註冊新使用者 {u}")
        else:
            messagebox.showerror("註冊失敗", "該用戶名稱已被註冊。")

    def auth_user(self):
        u = self.sec_user_entry.get()
        p = self.sec_pwd_entry.get()
        if self.security.authenticate(u, p):
            otp = self.security.generate_otp(u)
            self.sec_otp_display.config(text=f"OTP 驗證碼 (模擬發送手機): {otp}")
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Security", f"密碼登入驗證通過: {u}, 發送驗證碼: {otp}")
            messagebox.showinfo("驗證通過", "密碼認證成功！OTP 一次性認證驗證碼已模擬發送至手機。")
        else:
            messagebox.showerror("驗證失敗", "帳號或密碼有誤。")

    def verify_otp_code(self):
        u = self.sec_user_entry.get()
        code = self.sec_otp_entry.get()
        if self.security.verify_otp(u, code):
            self.db.insert_log(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Security", f"雙因子 OTP 認證成功: {u}")
            messagebox.showinfo("OTP 成功", "雙因素安全性驗證認證成功！交易操作已安全解鎖。")
        else:
            messagebox.showerror("OTP 失敗", "OTP 認證失敗，代碼錯誤。")

    # ==================== 日誌與每日回測邏輯 ====================
    def refresh_logs(self):
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        
        # 逆序（最新時間在最上面）讀取資料庫日誌
        logs = self.db.get_logs()
        for i, l in enumerate(reversed(logs)):
            if i > 100:  # 限制最多顯示 100 筆，防卡頓
                break
            self.log_tree.insert("", "end", values=(l[0], l[1], l[2]))

    def execute_backtest(self):
        self.backtest_result_text.insert(tk.END, f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 開始啟動策略回測程序...\n")
        
        # 調用 run_daily_backtest 執行引擎 (其內部會寫入數據庫 'trading_app.db')
        results = run_daily_backtest("trading_app.db")
        
        for r in results:
            self.backtest_result_text.insert(tk.END, f" - 策略: {r['strategy']} | 單次獲利: {r['profit']} USD\n")
            
        self.backtest_result_text.insert(tk.END, "回測結束，結果數據已寫入 SQL 資料庫中。\n=====================================\n\n")
        self.refresh_logs()

def main():
    root = tk.Tk()
    app = TradingSystemDashboard(root)
    root.mainloop()

if __name__ == "__main__":
    main()
