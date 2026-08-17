"""Delete a user account (and everything that cascades from it -- orders,
positions, strategies, notification channels, broker credentials) without
opening a public DELETE /api/users endpoint that would need its own auth
story. Usage:

    python scripts/delete_user.py trader@example.com

Asks for a typed confirmation before deleting anything.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/delete_user.py <email>", file=sys.stderr)
        raise SystemExit(1)

    email = sys.argv[1]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            print(f"No user with email {email!r} found.", file=sys.stderr)
            raise SystemExit(1)

        confirm = input(
            f"Type the email again to confirm deleting user {email!r} (id={user.id}) "
            "and all their data: "
        )
        if confirm != email:
            print("Confirmation did not match. Aborted.", file=sys.stderr)
            raise SystemExit(1)

        db.delete(user)
        db.commit()
        print(f"Deleted user {email!r} (id={user.id}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
