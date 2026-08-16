import sqlite3, datetime

class LoggerDB:
    def __init__(self, db_name="logs.db"):
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("CREATE TABLE IF NOT EXISTS logs (timestamp TEXT, category TEXT, message TEXT)")

    def log_event(self, category, message):
        ts = datetime.datetime.now().isoformat()
        self.conn.execute("INSERT INTO logs VALUES (?, ?, ?)", (ts, category, message))
        self.conn.commit()
        return True

    def get_logs(self, category=None):
        if category:
            return self.conn.execute("SELECT * FROM logs WHERE category=?", (category,)).fetchall()
        return self.conn.execute("SELECT * FROM logs").fetchall()
