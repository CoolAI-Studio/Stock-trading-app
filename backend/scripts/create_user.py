"""Bootstrap the first (or an additional) user without opening
POST /api/auth/register to the public. Usage:

    python scripts/create_user.py trader@example.com

You'll be prompted for a password (input hidden via getpass).
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_user.py <email>", file=sys.stderr)
        raise SystemExit(1)

    email = sys.argv[1]
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        raise SystemExit(1)
    if len(password.encode("utf-8")) > 72:
        print("Password must be at most 72 bytes.", file=sys.stderr)
        raise SystemExit(1)

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first() is not None:
            print(f"A user with email {email!r} already exists.", file=sys.stderr)
            raise SystemExit(1)

        user = User(email=email, hashed_password=hash_password(password))
        db.add(user)
        db.commit()
        print(f"Created user {email!r} (id={user.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
