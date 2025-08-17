import sqlite3
from contextlib import contextmanager
from pathlib import Path
from webhook_app.config import Config
import logging
import json

logger = logging.getLogger(__name__)

# -------- Webhook raw events (déjà présent chez toi, je le garde) --------
SCHEMA_SQL_WEBHOOKS = """
CREATE TABLE IF NOT EXISTS webhook_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key    TEXT UNIQUE,                -- clé stable pour éviter les doublons
  event_name   TEXT,
  received_at  DATETIME NOT NULL DEFAULT (datetime('now')),
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_we_received_at ON webhook_events(received_at);
"""

def _event_key_from_payload(payload: dict) -> str:
    event = str(payload.get("event") or "evt")
    sale_id = str(payload.get("sale", {}).get("id") or "unknown")
    created_at = str(payload.get("sale", {}).get("created_at") or "")
    return f"{event}:{sale_id}:{created_at}"

def ensure_schema_for_webhooks():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL_WEBHOOKS)
        conn.commit()
    finally:
        conn.close()

def save_webhook_raw(payload: dict, source: str = "webhook") -> int:
    ek = _event_key_from_payload(payload)
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT OR IGNORE INTO webhook_events(event_key, event_name, payload_json) VALUES (?,?,?)",
            (ek, str(payload.get("event") or ""), json.dumps(payload))
        )
        conn.commit()
        cur = conn.execute("SELECT id FROM webhook_events WHERE event_key = ?", (ek,))
        row = cur.fetchone()
        return int(row["id"]) if row else 0
    except Exception:
        logger.exception("save_webhook_raw failed")
        raise
    finally:
        conn.close()

# -------- Processed sales (déjà présent chez toi, je le garde) --------
SCHEMA_SQL_PROCESSED = """
CREATE TABLE IF NOT EXISTS processed_sales (
    sale_id TEXT PRIMARY KEY,
    status TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# -------- Notification log (NOUVEAU) --------
SCHEMA_SQL_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notification_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id       TEXT NOT NULL,
  channel       TEXT NOT NULL,              -- 'email' | 'whatsapp'
  template_type TEXT NOT NULL,              -- 'abandon' | 'failure' | 'success'
  recipient     TEXT,                       -- email normalisé ou téléphone normalisé
  sent_at       DATETIME NOT NULL DEFAULT (datetime('now')),
  UNIQUE (sale_id, channel, template_type)
);
CREATE INDEX IF NOT EXISTS idx_notification_log_recipient
  ON notification_log(recipient);
"""

def ensure_schema_for_notifications():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL_NOTIFICATIONS)
        conn.commit()
    finally:
        conn.close()

class Database:
    def __init__(self):
        self._ensure_db_exists()
        # on ne force pas ensure_schema_for_notifications ici pour te laisser la main via app.py

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(Config.DB_PATH)
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_db_exists(self):
        with self._get_connection() as conn:
            conn.executescript(SCHEMA_SQL_PROCESSED)
            conn.commit()
            logger.info("Base de données initialisée")

    # ---- processed_sales ----
    def has_processed(self, sale_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM processed_sales WHERE sale_id = ? LIMIT 1", (sale_id,))
            return cur.fetchone() is not None

    def mark_processed(self, sale_id: str, status: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO processed_sales (sale_id, status) VALUES (?, ?)", (sale_id, status))
            conn.commit()
            logger.debug("Vente %s marquée %s", sale_id, status)

    # ---- notification_log (NOUVEAU) ----
    def has_notified(self, sale_id: str, channel: str, template_type: str) -> bool:
        """Dédup par vente (pour success)."""
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT 1 FROM notification_log
                WHERE sale_id = ? AND channel = ? AND template_type = ?
                LIMIT 1
            """, (sale_id, channel, template_type))
            return cur.fetchone() is not None

    def has_notified_recipient(self, recipient: str, channel: str, template_type: str, window_days: int | None = None) -> bool:
        """Dédup par destinataire (pour abandon/failure), fenêtre optionnelle."""
        if not recipient:
            return False
        with self._get_connection() as conn:
            if window_days and window_days > 0:
                cur = conn.execute("""
                    SELECT 1 FROM notification_log
                    WHERE recipient = ? AND channel = ? AND template_type = ?
                      AND sent_at >= datetime('now', ?)
                    LIMIT 1
                """, (recipient, channel, template_type, f"-{int(window_days)} days"))
            else:
                cur = conn.execute("""
                    SELECT 1 FROM notification_log
                    WHERE recipient = ? AND channel = ? AND template_type = ?
                    LIMIT 1
                """, (recipient, channel, template_type))
            return cur.fetchone() is not None

    def mark_notified(self, sale_id: str, channel: str, template_type: str, recipient: str | None = None):
        """Trace un envoi (idempotent grâce à UNIQUE)."""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO notification_log (sale_id, channel, template_type, recipient)
                VALUES (?, ?, ?, ?)
            """, (sale_id, channel, template_type, recipient))
            conn.commit()
