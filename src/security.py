import hashlib
import random

class SecuritySystem:
    """安全系統，支援登入加密與雙重驗證 (模擬版)"""

    def __init__(self):
        self.users = {}
        self.otp_codes = {}

    def register_user(self, username: str, password: str) -> bool:
        if username in self.users:
            return False
        hashed_pw = hashlib.sha256(password.encode()).hexdigest()
        self.users[username] = hashed_pw
        return True

    def authenticate(self, username: str, password: str) -> bool:
        hashed_pw = hashlib.sha256(password.encode()).hexdigest()
        return self.users.get(username) == hashed_pw

    def generate_otp(self, username: str) -> str:
        """產生一次性驗證碼 (模擬)"""
        otp = str(random.randint(100000, 999999))
        self.otp_codes[username] = otp
        return otp

    def verify_otp(self, username: str, otp: str) -> bool:
        """驗證一次性驗證碼"""
        return self.otp_codes.get(username) == otp
