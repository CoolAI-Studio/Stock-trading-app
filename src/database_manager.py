import sqlite3

class DatabaseManager:
    def __init__(self, db_name="trading_app.db"):
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, category TEXT, message TEXT)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS backtest_results (strategy TEXT, profit REAL)")

    def insert_log(self, timestamp, category, message):
        self.conn.execute("INSERT INTO logs VALUES (?, ?, ?)", (timestamp, category, message))
        self.conn.commit()
        return True

    def get_logs(self, category=None):
        if category:
            return self.conn.execute("SELECT * FROM logs WHERE category=?", (category,)).fetchall()
        return self.conn.execute("SELECT * FROM logs").fetchall()

    def insert_backtest_result(self, strategy, profit):
        self.conn.execute("INSERT INTO backtest_results VALUES (?, ?)", (strategy, profit))
        self.conn.commit()
        return True

    def get_backtest_results(self, strategy=None):
        if strategy:
            return self.conn.execute("SELECT * FROM backtest_results WHERE strategy=?", (strategy,)).fetchall()
        return self.conn.execute("SELECT * FROM backtest_results").fetchall()
