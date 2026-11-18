"""Promote an existing user to admin.

Usage:
    python scripts/make_admin.py someone@example.com

Deliberately does NOT create an account or accept a password: the person
must already have signed up with their own password via POST /auth/signup.
This script only flips the `is_admin` flag, and only for whoever can run it
(shell/DB access to this machine) - so there is no admin credential anywhere
in the codebase, an env var, or a default password to leak. This is how you
make the first admin; after that, an admin can promote others the same way.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/make_admin.py <email>")
        raise SystemExit(1)
    email = sys.argv[1].strip().lower()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one_or_none()
        if user is None:
            print(f"No user with email {email!r}. They must sign up first via POST /auth/signup.")
            raise SystemExit(1)
        if user.is_admin:
            print(f"{email} is already an admin.")
            return
        user.is_admin = True
        db.commit()
        print(f"{email} is now an admin.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
