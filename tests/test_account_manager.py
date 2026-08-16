import pytest
from src.account_manager import Account, AccountManager

def test_add_and_get_account():
    manager = AccountManager()
    acc = Account("acc1", "broker")
    assert manager.add_account(acc) is True
    retrieved = manager.get_account("acc1")
    assert retrieved.account_id == "acc1"
    assert retrieved.account_type == "broker"

def test_duplicate_account():
    manager = AccountManager()
    acc1 = Account("acc1", "broker")
    acc2 = Account("acc1", "messenger")
    assert manager.add_account(acc1) is True
    assert manager.add_account(acc2) is False  # duplicate ID not allowed

def test_remove_account():
    manager = AccountManager()
    acc = Account("acc1", "broker")
    manager.add_account(acc)
    assert manager.remove_account("acc1") is True
    assert manager.get_account("acc1") is None

def test_permissions():
    acc = Account("acc1", "broker")
    assert acc.grant_permission("trade") is True
    assert acc.grant_permission("trade") is False  # duplicate
    assert "trade" in acc.permissions
    assert acc.revoke_permission("trade") is True
    assert acc.revoke_permission("trade") is False  # already removed
