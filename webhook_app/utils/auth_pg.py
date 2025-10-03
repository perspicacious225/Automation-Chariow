import time
from dataclasses import dataclass
from typing import Optional
from werkzeug.security import generate_password_hash, check_password_hash  # type: ignore
from flask_login import UserMixin  # type: ignore

from webhook_app.database_pg import get_connection, execute_with_retry


def ensure_users_schema():
    with get_connection() as conn:
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS users (
          id SERIAL PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          is_admin BOOLEAN NOT NULL DEFAULT FALSE,
          created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
        """)


def users_count() -> int:
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn, "SELECT COUNT(*) AS c FROM users", fetch="one")
        return int(row["c"] if row else 0)


@dataclass
class _UserRow:
    id: int
    email: str
    password_hash: str
    is_admin: bool


class User(UserMixin):
    def __init__(self, row: _UserRow):
        self.id = str(row.id)
        self.email = row.email
        self.password_hash = row.password_hash
        self.is_admin = bool(row.is_admin)


def _row_to_user(row) -> Optional[User]:
    if not row:
        return None
    return User(_UserRow(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        is_admin=row["is_admin"],
    ))


def get_user_by_id(user_id: str) -> Optional[User]:
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn, "SELECT * FROM users WHERE id=%s", (user_id,), fetch="one")
        return _row_to_user(row)


def get_user_by_email(email: str) -> Optional[User]:
    email_norm = (email or "").strip().lower()
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn, "SELECT * FROM users WHERE email=%s", (email_norm,), fetch="one")
        return _row_to_user(row)


def create_user(email: str, password: str, *, is_admin: bool = False) -> User:
    email_norm = (email or "").strip().lower()
    pwd_hash = generate_password_hash(password)

    if users_count() == 0:
        is_admin = True

    for attempt in range(5):
        try:
            with get_connection() as conn:
                execute_with_retry(conn,
                    "INSERT INTO users(email, password_hash, is_admin) VALUES (%s,%s,%s)",
                    (email_norm, pwd_hash, bool(is_admin))
                )
            return get_user_by_email(email_norm)  # type: ignore
        except Exception:
            time.sleep(0.2 * (2 ** attempt))
            continue
    raise RuntimeError("Base de données occupée, réessayez plus tard.")


def verify_password(user: User, password: str) -> bool:
    return check_password_hash(user.password_hash, password)


