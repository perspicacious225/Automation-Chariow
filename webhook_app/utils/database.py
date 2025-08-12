import sqlite3
from contextlib import contextmanager
from pathlib import Path
from config import Config
import logging
import json

# Schéma minimal pour archiver 100% des payloads (audit + idempotence simple)
SCHEMA_SQL_WEBHOOKS = """
CREATE TABLE IF NOT EXISTS webhook_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key    TEXT UNIQUE,                -- clé stable pour éviter les doublons
  event_name   TEXT,                       -- ex: successful.sale, failed.sale...
  received_at  DATETIME NOT NULL DEFAULT (datetime('now')),
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_we_received_at ON webhook_events(received_at);
"""

def _event_key_from_payload(payload: dict) -> str:
    # clé stable = event + sale.id + sale.created_at (adapte si besoin)
    event = str(payload.get("event") or "evt")
    sale_id = str(payload.get("sale", {}).get("id") or "unknown")
    created_at = str(payload.get("sale", {}).get("created_at") or "")
    return f"{event}:{sale_id}:{created_at}"

def ensure_schema_for_webhooks():
    """Créer la table webhook_events si absente."""
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL_WEBHOOKS)
        conn.commit()
    finally:
        conn.close()

def save_webhook_raw(payload: dict, source: str = "webhook") -> int:
    """
    Insère le JSON brut dans webhook_events (INSERT OR IGNORE via event_key).
    Retourne la PK (id) de l'event.
    """
    ek = _event_key_from_payload(payload)
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT OR IGNORE INTO webhook_events(event_key, event_name, payload_json) VALUES (?,?,?)",
            (ek, payload.get("event"), json.dumps(payload, ensure_ascii=False))
        )
        row = conn.execute("SELECT id FROM webhook_events WHERE event_key = ?", (ek,)).fetchone()
        conn.commit()
        return row["id"]
    finally:
        conn.close()

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self._ensure_db_exists()

    @contextmanager
    def _get_connection(self):
        """Gestion automatique des connexions avec création de table si nécessaire"""
        conn = sqlite3.connect(Config.DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")  # Meilleure gestion des accès concurrents
        try:
            yield conn
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def _ensure_db_exists(self):
        """Crée la table si elle n'existe pas"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_sales (
                    sale_id TEXT PRIMARY KEY,
                    status TEXT,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            logger.info("Base de données initialisée")

    def has_processed(self, sale_id: str) -> bool:
        """Vérifie si une vente a déjà été traitée"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM processed_sales WHERE sale_id = ?", 
                (sale_id,)
            )
            return cursor.fetchone() is not None

    def mark_processed(self, sale_id: str, status: str):
        """Marque une vente comme traitée"""
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_sales (sale_id, status) VALUES (?, ?)",
                (sale_id, status)
            )
            conn.commit()
            logger.debug(f"Vente {sale_id} marquée comme {status}")