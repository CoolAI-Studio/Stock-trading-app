import pytest
from src.security import SecuritySystem

def test_register_and_authenticate():
    sec = SecuritySystem()
    assert sec.register_user("alice", "password123") is True
    assert sec.authenticate("alice", "password123") is True
    assert sec.authenticate("alice", "wrongpass") is False

def test_duplicate_register():
    sec = SecuritySystem()
    assert sec.register_user("bob", "pw1") is True
    assert sec.register_user("bob", "pw2") is False  # duplicate username not allowed

def test_otp_generation_and_verification():
    sec = SecuritySystem()
    sec.register_user("charlie", "pw")
    otp = sec.generate_otp("charlie")
    assert sec.verify_otp("charlie", otp) is True
    assert sec.verify_otp("charlie", "000000") is False
