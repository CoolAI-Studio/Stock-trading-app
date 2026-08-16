class Account:
    """單一帳號物件，包含券商或通訊帳號資訊"""

    def __init__(self, account_id: str, account_type: str, permissions: list = None):
        self.account_id = account_id
        self.account_type = account_type  # e.g. "broker", "messenger"
        self.permissions = permissions if permissions else []

    def grant_permission(self, permission: str):
        if permission not in self.permissions:
            self.permissions.append(permission)
            return True
        return False

    def revoke_permission(self, permission: str):
        if permission in self.permissions:
            self.permissions.remove(permission)
            return True
        return False


class AccountManager:
    """多帳號管理器，支援新增、刪除、查詢帳號"""

    def __init__(self):
        self.accounts = {}

    def add_account(self, account: Account) -> bool:
        if account.account_id in self.accounts:
            return False
        self.accounts[account.account_id] = account
        return True

    def remove_account(self, account_id: str) -> bool:
        if account_id in self.accounts:
            del self.accounts[account_id]
            return True
        return False

    def get_account(self, account_id: str) -> Account:
        return self.accounts.get(account_id, None)

    def list_accounts(self) -> list:
        return list(self.accounts.values())
