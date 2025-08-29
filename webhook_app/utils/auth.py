# webhook_app/utils/auth.py
import sqlite3, time, os
from dataclasses import dataclass
from typing import Optional
from werkzeug.security import generate_password_hash, check_password_hash  # type: ignore
from flask_login import UserMixin # type: ignore
from webhook_app.config import Config


# ---------- DB ----------

def _conn():
    # S'assure que le dossier existe et est inscriptible
    db_path = Config.DB_PATH
    db_dir = os.path.dirname(db_path or "")
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    # Ouvre la connexion (ne la réutilise pas entre threads)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)

    try:
        # Evite les "database is locked"
        conn.execute("PRAGMA busy_timeout=5000;")

        # - PROD/Render : SQLITE_JOURNAL_MODE=DELETE

        mode = os.getenv("SQLITE_JOURNAL_MODE", "DELETE").upper()
        if mode == "WAL":
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=DELETE;")
        else:
            conn.execute("PRAGMA journal_mode=DELETE;")

        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA temp_store=MEMORY;")
    except Exception:
        pass

    return conn

def ensure_users_schema():
    conn = _conn()
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          is_admin INTEGER NOT NULL DEFAULT 0,
          created_at DATETIME NOT NULL DEFAULT (datetime('now'))
        )
        """)
        conn.commit()
    finally:
        conn.close()

def users_count() -> int:
    conn = _conn()
    try:
        cur = conn.execute("SELECT COUNT(*) FROM users")
        row = cur.fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()

# ---------- Modèle ----------
@dataclass
class _UserRow:
    id: int
    email: str
    password_hash: str
    is_admin: int

class User(UserMixin):
    def __init__(self, row: _UserRow):
        self.id = str(row.id)                
        self.email = row.email
        self.password_hash = row.password_hash
        self.is_admin = bool(row.is_admin)

def _row_to_user(row) -> Optional[User]:
    if not row: return None
    return User(_UserRow(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        is_admin=row["is_admin"]
    ))

# ---------- CRUD ----------
def get_user_by_id(user_id: str) -> Optional[User]:
    conn = _conn(); conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()

def get_user_by_email(email: str) -> Optional[User]:
    email_norm = (email or "").strip().lower()
    conn = _conn(); conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (email_norm,)).fetchone()
        return _row_to_user(row)
    finally:
        conn.close()

def create_user(email: str, password: str, *, is_admin: bool=False) -> User:
    email_norm = email.strip().lower()
    pwd_hash = generate_password_hash(password)

    if users_count() == 0:
        is_admin = True

    for attempt in range(5): 
        conn = _conn()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO users(email, password_hash, is_admin) VALUES (?,?,?)",
                    (email_norm, pwd_hash, 1 if is_admin else 0)
                )
            # relire l’utilisateur inséré
            return get_user_by_email(email_norm)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                time.sleep(0.2 * (2 ** attempt))
                continue
            raise
        finally:
            conn.close()

    
    raise RuntimeError("Base de données occupée, réessayez dans un instant.")

def verify_password(user: User, password: str) -> bool:
    return check_password_hash(user.password_hash, password)
