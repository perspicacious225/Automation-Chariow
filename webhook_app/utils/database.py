import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from webhook_app.config import Config
import logging, json, math, datetime

logger = logging.getLogger(__name__)

# -------- Webhook raw events (historique) --------
SCHEMA_SQL_WEBHOOKS = """
CREATE TABLE IF NOT EXISTS webhook_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key    TEXT UNIQUE,
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
            (ek, str(payload.get("event") or ""), json.dumps(payload, ensure_ascii=False))
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

# -------- Processed sales --------
SCHEMA_SQL_PROCESSED = """
CREATE TABLE IF NOT EXISTS processed_sales (
    sale_id TEXT PRIMARY KEY,
    status TEXT,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

# -------- Notification log --------
SCHEMA_SQL_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notification_log (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id         TEXT NOT NULL,
  channel         TEXT NOT NULL,              -- 'email' | 'whatsapp'
  template_type   TEXT NOT NULL,              -- 'relance_t30', ... 'confirm_3_1'
  recipient       TEXT,                       -- clé normalisée (email|prod ou phone|prod)
  sent_at         DATETIME NOT NULL DEFAULT (datetime('now')),
  -- enrichissements
  recipient_email TEXT,
  recipient_phone TEXT,
  contact_key     TEXT,
  product_id      TEXT,
  ab_arm          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_notif ON notification_log(sale_id, channel, template_type);
CREATE INDEX IF NOT EXISTS idx_notification_log_recipient ON notification_log(recipient);
CREATE INDEX IF NOT EXISTS idx_log_contact_product_time ON notification_log(contact_key, product_id, sent_at);
"""


def ensure_schema_for_notifications():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL_NOTIFICATIONS)
        conn.commit()
    finally:
        conn.close()

def ensure_notification_log_columns():
    """
    Migration douce si la table existante n'a pas encore les colonnes enrichies.
    """
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(notification_log)")
        cols = [row[1] for row in cur.fetchall()]

        to_add = []
        if "recipient_email" not in cols:
            to_add.append("ALTER TABLE notification_log ADD COLUMN recipient_email TEXT")
        if "recipient_phone" not in cols:
            to_add.append("ALTER TABLE notification_log ADD COLUMN recipient_phone TEXT")
        if "contact_key" not in cols:
            to_add.append("ALTER TABLE notification_log ADD COLUMN contact_key TEXT")
        if "product_id" not in cols:
            to_add.append("ALTER TABLE notification_log ADD COLUMN product_id TEXT")
        if "ab_arm" not in cols:
            to_add.append("ALTER TABLE notification_log ADD COLUMN ab_arm TEXT")

        for sql in to_add:
            try:
                conn.execute(sql)
            except Exception:
                pass

        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_notif ON notification_log(sale_id, channel, template_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_log_recipient ON notification_log(recipient)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_log_contact_product_time ON notification_log(contact_key, product_id, sent_at)")
        except Exception:
            pass

        conn.commit()
    finally:
        conn.close()

# ---------- Scheduled notifications (relances) ----------
SCHEDULED_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_notifications (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id        TEXT NOT NULL,
  template_type  TEXT NOT NULL,
  due_at         DATETIME NOT NULL,
  payload_json   TEXT NOT NULL,
  created_at     DATETIME NOT NULL DEFAULT (datetime('now')),
  sent_at        DATETIME,
  error          TEXT,
  -- enrichissements
  contact_key    TEXT,
  product_id     TEXT,
  ab_arm         TEXT,
  UNIQUE (sale_id, template_type, due_at)
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_notifications(due_at);
CREATE INDEX IF NOT EXISTS idx_sched_contact_product_sent ON scheduled_notifications(contact_key, product_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_sched_contact_product_due ON scheduled_notifications(contact_key, product_id, due_at);
"""

def ensure_schema_for_scheduled():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(SCHEDULED_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

def ensure_scheduled_contact_columns():
    # migration douce si la table existante n'avait pas ces colonnes
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute("PRAGMA table_info(scheduled_notifications)")
        cols = [row[1] for row in cur.fetchall()]
        to_add = []
        if "contact_key" not in cols:
            to_add.append("ALTER TABLE scheduled_notifications ADD COLUMN contact_key TEXT")
        if "product_id" not in cols:
            to_add.append("ALTER TABLE scheduled_notifications ADD COLUMN product_id TEXT")
        if "ab_arm" not in cols:
            to_add.append("ALTER TABLE scheduled_notifications ADD COLUMN ab_arm TEXT")
        for sql in to_add:
            try: conn.execute(sql)
            except Exception: pass
        conn.commit()
    finally:
        conn.close()

def enqueue_notification(
    sale_id: str,
    template_type: str,
    due_at,                            
    payload: dict,
    *,
    contact_key: str | None = None,
    product_id: str | None = None,
    ab_arm: str | None = None
):
    due_epoch = _to_epoch(due_at)
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO scheduled_notifications
              (sale_id, template_type, due_at, payload_json, contact_key, product_id, ab_arm)
            VALUES (?,?,?,?,?,?,?)
            """,
            (sale_id, template_type, due_epoch, json.dumps(payload, ensure_ascii=False), contact_key, product_id, ab_arm),
        )
        conn.commit()
    finally:
        conn.close()
def fetch_due_scheduled(limit: int = 50):
    now_epoch = int(time.time())
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """
            SELECT *
            FROM scheduled_notifications
            WHERE sent_at IS NULL
              AND due_at <= ?
            ORDER BY due_at ASC, id ASC
            LIMIT ?
            """,
            (now_epoch, limit),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
def mark_scheduled_sent(sched_id: int):
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.execute("UPDATE scheduled_notifications SET sent_at = datetime('now'), error = NULL WHERE id = ?", (sched_id,))
        conn.commit()
    finally:
        conn.close()

def mark_scheduled_error(sched_id: int, error: str):
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.execute("UPDATE scheduled_notifications SET error = ? WHERE id = ?", (error, sched_id))
        conn.commit()
    finally:
        conn.close()

def has_confirmation_for_contact_product(contact_key: str, product_id: str) -> bool:
    if not (contact_key and product_id):
        return False
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "SELECT 1 FROM notification_log "
            "WHERE contact_key=? AND product_id=? AND template_type LIKE 'confirm_%' "
            "LIMIT 1",
            (contact_key, product_id)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def latest_relance_step(contact_key: str, product_id: str) -> str | None:
    """
    Retourne la DERNIÈRE relance envoyée pour ce couple contact+produit,
    en considérant tous les canaux (email + whatsapp).
    Renvoie l'une de: 't47h' | 't23h' | 't6h' | 't30' | None
    """
    if not (contact_key and product_id):
        return None
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "SELECT template_type FROM notification_log "
            "WHERE contact_key=? AND product_id=? "
            "  AND template_type IN ('relance_t30','relance_t6h','relance_t23h','relance_t47h') "
            "ORDER BY sent_at DESC LIMIT 1",
            (contact_key, product_id)
        )
        row = cur.fetchone()
        if not row:
            return None
        tt = str(row[0]).lower()
        if tt.endswith("t47h"): return "t47h"
        if tt.endswith("t23h"): return "t23h"
        if tt.endswith("t6h"):  return "t6h"
        return "t30"
    finally:
        conn.close()


def claim_scheduled_job(job_id: int) -> bool:
    """
    Réservation atomique d’un job (anti-doublon si plusieurs workers).
    Retourne True si on a bien revendiqué le job.
    """
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE scheduled_notifications SET sent_at = datetime('now'), error = NULL WHERE id = ? AND sent_at IS NULL",
            (job_id,)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()

# -------- Processed + Notification log helpers --------
class Database:
    def __init__(self):
        self._ensure_db_exists()

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

    # processed_sales
    def has_processed(self, sale_id: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM processed_sales WHERE sale_id = ? LIMIT 1", (sale_id,))
            return cur.fetchone() is not None

    def mark_processed(self, sale_id: str, status: str):
        with self._get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO processed_sales (sale_id, status) VALUES (?, ?)", (sale_id, status))
            conn.commit()

    # notification_log
    def has_notified(self, sale_id: str, channel: str, template_type: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("""
                SELECT 1 FROM notification_log
                WHERE sale_id = ? AND channel = ? AND template_type = ?
                LIMIT 1
            """, (sale_id, channel, template_type))
            return cur.fetchone() is not None

    def has_notified_recipient(self, recipient: str, channel: str, template_type: str, window_days: int | None = None) -> bool:
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

    def mark_notified(self, sale_id: str, channel: str, template_type: str, recipient: str | None = None, *,
                      recipient_email: str | None = None, recipient_phone: str | None = None,
                      contact_key: str | None = None, product_id: str | None = None, ab_arm: str | None = None):
        sent_epoch = int(time.time())
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row

            # Backfill depuis fact_sales si meta manquante/mal formée
            row = conn.execute(
                "SELECT product_id, contact_key, email, phone FROM fact_sales WHERE sale_id=? LIMIT 1",
                (sale_id,)
            ).fetchone()
            if row:
                fs_pid = (row["product_id"] or "").strip()
                fs_ck  = (row["contact_key"] or "").strip() or None
                # corrige tirets -> underscore quand nécessaire
                if fs_pid and "-" in fs_pid:
                    fs_pid = fs_pid.replace("-", "_")

                if not product_id or product_id != fs_pid:
                    product_id = fs_pid or product_id
                if not contact_key:
                    contact_key = fs_ck
                if not recipient_email:
                    recipient_email = (row["email"] or None)
                if not recipient_phone:
                    recipient_phone = (row["phone"] or None)

            # On écrit sent_at en epoch pour simplifier toutes les requêtes
            conn.execute("""
                INSERT OR IGNORE INTO notification_log
                (sale_id, channel, template_type, recipient, recipient_email, recipient_phone,
                 contact_key, product_id, ab_arm, sent_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (sale_id, channel, template_type, recipient, recipient_email, recipient_phone,
                  contact_key, product_id, ab_arm, sent_epoch))
            conn.commit()


# ---------- FACT & DIM (ETL KPI) ----------
FACT_SALES_SQL = """
CREATE TABLE IF NOT EXISTS fact_sales (
  sale_id TEXT PRIMARY KEY,
  status TEXT,
  amount_value REAL,
  currency TEXT,
  product_id TEXT,
  product_name TEXT,
  store_id TEXT,
  store_name TEXT,
  contact_key TEXT,
  email TEXT,
  phone TEXT,
  country TEXT,
  created_at DATETIME,
  completed_at DATETIME,
  abandoned_at DATETIME,
  failed_at DATETIME,
  time_to_complete_min REAL,
  hour_of_day INTEGER,
  dow INTEGER,
  month TEXT,
  utm_source TEXT,
  utm_medium TEXT,
  utm_campaign TEXT,
  price_tier TEXT
);
CREATE INDEX IF NOT EXISTS idx_fact_created ON fact_sales(created_at);
CREATE INDEX IF NOT EXISTS idx_fact_contact_prod ON fact_sales(contact_key, product_id);
CREATE INDEX IF NOT EXISTS idx_fact_failed ON fact_sales(status, failed_at);
"""

DIM_CUSTOMER_SQL = """
CREATE TABLE IF NOT EXISTS dim_customer (
  contact_key TEXT PRIMARY KEY,
  first_seen DATETIME,
  last_seen DATETIME,
  country TEXT,
  orders_count INTEGER DEFAULT 0,
  gmv_total REAL DEFAULT 0,
  rfm_recency_days REAL,
  rfm_frequency REAL,
  rfm_monetary REAL,
  rfm_segment TEXT
);
"""

DIM_PRODUCT_SQL = """
CREATE TABLE IF NOT EXISTS dim_product (
  product_id TEXT PRIMARY KEY,
  product_name TEXT,
  first_seen DATETIME
);
"""

def ensure_fact_dims_schema():
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript(FACT_SALES_SQL)
        conn.executescript(DIM_CUSTOMER_SQL)
        conn.executescript(DIM_PRODUCT_SQL)
        conn.commit()
    finally:
        conn.close()

def _norm_email(s: str | None) -> str | None:
    return (s or "").replace(" ", "").lower() or None

def _contact_key(email: str | None, phone: str | None) -> str | None:
    e = _norm_email(email)
    return e or (phone or None)

def _price_tier(val: float) -> str:
    if val < 1000: return "<1k"
    if val < 5000: return "1k–5k"
    if val < 10000: return "5k–10k"
    if val < 50000: return "10k–50k"
    return "50k+"

def _get_utm(custom_fields):
    src = med = camp = None
    try:
        for f in custom_fields or []:
            n = str(f.get("name","")).lower()
            v = f.get("value")
            if n in ("utm_source","source"): src = v
            elif n in ("utm_medium","medium"): med = v
            elif n in ("utm_campaign","campaign"): camp = v
    except Exception:
        pass
    return src, med, camp

def _to_epoch(v) -> int | None:
    """
    Convertit v en epoch (int, UTC).
    - v peut être int/float (déjà epoch) ou string ISO ('YYYY-MM-DD HH:MM:SS', '...T...Z', etc.)
    - lève ValueError si non parseable.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    # ISO 8601: accepte 'Z' et espace comme séparateur
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp())
    except Exception:
        pass
    # Dernier recours: conversion SQLite (gère beaucoup de variantes texte)
    with sqlite3.connect(Config.DB_PATH) as c:
        cur = c.execute("SELECT CAST(strftime('%s', ?) AS INTEGER)", (s,))
        row = cur.fetchone()
        if row and row[0] is not None:
            return int(row[0])
    raise ValueError(f"due_at non parseable: {v!r}")


def upsert_fact_from_webhook(payload: dict):
    s    = payload.get("sale", {}) or {}
    prod = payload.get("product", {}) or {}
    cust = payload.get("customer", {}) or {}
    store= payload.get("store", {}) or {}

    sale_id = str(s.get("id") or "")
    status  = (s.get("status") or "").strip().lower()

    amount   = float((s.get("amount") or {}).get("value") or 0.0)
    currency = (s.get("amount") or {}).get("currency") or (prod.get("price") or {}).get("currency")

    # --- timestamps en EPOCH (INTEGER) ---
    created   = _to_epoch(s.get("created_at"))
    completed = _to_epoch(s.get("completed_at"))
    abandoned = _to_epoch(s.get("abandoned_at"))
    failed    = _to_epoch(s.get("failed_at"))

    # fallbacks sains
    if status == "completed" and not completed:
        completed = created
    if status == "failed" and not failed:
        # si la source n'envoie pas failed_at, prends le meilleur proxy
        failed = s.get("failed_at") and _to_epoch(s.get("failed_at")) or abandoned or completed or created

    product_id   = prod.get("id") or None
    product_name = prod.get("name") or None
    store_id     = store.get("id") or None
    store_name   = store.get("name") or None

    email   = cust.get("email") or None
    phone   = cust.get("phone") or None
    country = cust.get("country") or None
    contact = _contact_key(email, phone)

    # dérivés
    ttc_min = round((completed - created)/60.0, 2) if (created and completed) else None
    t_ref = completed or abandoned or failed or created
    if t_ref:
        dt = datetime.datetime.utcfromtimestamp(t_ref)
        hod = dt.hour
        dow = dt.weekday()       # 0 = lundirf
        month = dt.strftime("%Y-%m")
    else:
        hod = dow = month = None

    utm_source, utm_medium, utm_campaign = _get_utm(s.get("custom_fields"))
    price_tier = _price_tier(amount)

    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")

        # FACT upsert (note l’ajout de failed_at + COALESCE pour ne pas écraser)
        conn.execute("""
        INSERT INTO fact_sales (
          sale_id, status, amount_value, currency,
          product_id, product_name, store_id, store_name,
          contact_key, email, phone, country,
          created_at, completed_at, abandoned_at, failed_at,
          time_to_complete_min, hour_of_day, dow, month,
          utm_source, utm_medium, utm_campaign, price_tier
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sale_id) DO UPDATE SET
          status       = excluded.status,
          amount_value = COALESCE(excluded.amount_value, fact_sales.amount_value),
          currency     = COALESCE(excluded.currency,     fact_sales.currency),
          product_id   = COALESCE(excluded.product_id,   fact_sales.product_id),
          product_name = COALESCE(excluded.product_name, fact_sales.product_name),
          store_id     = COALESCE(excluded.store_id,     fact_sales.store_id),
          store_name   = COALESCE(excluded.store_name,   fact_sales.store_name),
          contact_key  = COALESCE(excluded.contact_key,  fact_sales.contact_key),
          email        = COALESCE(excluded.email,        fact_sales.email),
          phone        = COALESCE(excluded.phone,        fact_sales.phone),
          country      = COALESCE(excluded.country,      fact_sales.country),

          created_at   = COALESCE(fact_sales.created_at,   excluded.created_at),
          completed_at = COALESCE(excluded.completed_at,   fact_sales.completed_at),
          abandoned_at = COALESCE(excluded.abandoned_at,   fact_sales.abandoned_at),
          failed_at    = COALESCE(excluded.failed_at,      fact_sales.failed_at),

          time_to_complete_min = COALESCE(excluded.time_to_complete_min, fact_sales.time_to_complete_min),
          hour_of_day  = COALESCE(excluded.hour_of_day,  fact_sales.hour_of_day),
          dow          = COALESCE(excluded.dow,          fact_sales.dow),
          month        = COALESCE(excluded.month,        fact_sales.month),

          utm_source   = COALESCE(excluded.utm_source,   fact_sales.utm_source),
          utm_medium   = COALESCE(excluded.utm_medium,   fact_sales.utm_medium),
          utm_campaign = COALESCE(excluded.utm_campaign, fact_sales.utm_campaign),
          price_tier   = COALESCE(excluded.price_tier,   fact_sales.price_tier)
        """, (
            sale_id, status, amount, currency,
            product_id, product_name, store_id, store_name,
            contact, email, phone, country,
            created, completed, abandoned, failed,
            ttc_min, hod, dow, month,
            utm_source, utm_medium, utm_campaign, price_tier
        ))

        # DIM Product
        if product_id:
            first_seen_iso = (
                datetime.datetime.utcfromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S")
                if created else datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            )
            conn.execute("""
            INSERT INTO dim_product(product_id, product_name, first_seen)
            VALUES (?,?,?)
            ON CONFLICT(product_id) DO UPDATE SET product_name=excluded.product_name
            """, (product_id, product_name, first_seen_iso))

        # DIM Customer
        if contact:
            now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            first_iso = datetime.datetime.utcfromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created else now_iso
            last_iso  = datetime.datetime.utcfromtimestamp(completed or created).strftime("%Y-%m-%d %H:%M:%S") if (completed or created) else now_iso
            conn.execute("""
            INSERT INTO dim_customer(contact_key, first_seen, last_seen, country)
            VALUES (?,?,?,?)
            ON CONFLICT(contact_key) DO NOTHING
            """, (contact, first_iso, last_iso, country))

        conn.commit()
    finally:
        conn.close()

def rfm_recompute():
    """Recalcule R/F/M + segment et met à jour dim_customer (colonnes *_at en epoch)."""
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT contact_key,
                   MIN(COALESCE(created_at, completed_at)) AS first_seen,
                   MAX(COALESCE(completed_at, created_at)) AS last_seen,
                   COUNT(CASE WHEN status='completed' THEN 1 END) AS orders_count,
                   COALESCE(SUM(CASE WHEN status='completed' THEN amount_value END),0) AS gmv_total
            FROM fact_sales
            WHERE contact_key IS NOT NULL
            GROUP BY contact_key
        """).fetchall()

        now = datetime.datetime.utcnow()

        def _as_dt_epoch(val):
            if val is None: return None
            try:
                return datetime.datetime.utcfromtimestamp(int(val))
            except Exception:
                # backstop si base partiellement en texte
                try:
                    return datetime.datetime.fromisoformat(str(val))
                except Exception:
                    return None

        rec_days, freqs, mons, tmp = [], [], [], []
        for r in rows:
            fs_dt = _as_dt_epoch(r["first_seen"]) or now
            ls_dt = _as_dt_epoch(r["last_seen"]) or now
            recency_days = (now - ls_dt).total_seconds()/86400.0
            freq = int(r["orders_count"] or 0)
            mon  = float(r["gmv_total"] or 0)
            rec_days.append(recency_days); freqs.append(freq); mons.append(mon)
            tmp.append((r["contact_key"], recency_days, freq, mon, fs_dt, ls_dt))

        def _quantiles(vals):
            if not vals: return (0,0,0,0)
            vs = sorted(vals)
            def q(p): 
                i = max(0, min(len(vs)-1, int(round(p*(len(vs)-1)))))
                return vs[i]
            return (q(0.2), q(0.4), q(0.6), q(0.8))

        rq, fq, mq = _quantiles(rec_days), _quantiles(freqs), _quantiles(mons)

        def score_recency(d):
            return 5 if d <= rq[0] else 4 if d <= rq[1] else 3 if d <= rq[2] else 2 if d <= rq[3] else 1

        def score_quantile(x, qs):
            return 1 if x <= qs[0] else 2 if x <= qs[1] else 3 if x <= qs[2] else 4 if x <= qs[3] else 5

        def seg(r,f,m):
            if r>=4 and f>=4 and m>=4: return "Champions"
            if r>=4 and f>=3:          return "Fidèles"
            if r>=3 and f>=2 and m>=3: return "Prometteurs"
            if r<=2 and f>=3:          return "À réactiver"
            if r<=2 and f<=2 and m<=2: return "À risque"
            return "Standard"

        for ck, rd, fqv, mv, fs_dt, ls_dt in tmp:
            r = score_recency(rd); f = score_quantile(fqv, fq); m = score_quantile(mv, mq)
            first_iso = fs_dt.strftime("%Y-%m-%d %H:%M:%S")
            last_iso  = ls_dt.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("""
              INSERT INTO dim_customer(contact_key, first_seen, last_seen, country, orders_count, gmv_total,
                                       rfm_recency_days, rfm_frequency, rfm_monetary, rfm_segment)
              VALUES (?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(contact_key) DO UPDATE SET
                first_seen=excluded.first_seen,
                last_seen=excluded.last_seen,
                orders_count=excluded.orders_count,
                gmv_total=excluded.gmv_total,
                rfm_recency_days=excluded.rfm_recency_days,
                rfm_frequency=excluded.rfm_frequency,
                rfm_monetary=excluded.rfm_monetary,
                rfm_segment=excluded.rfm_segment
            """, (ck, first_iso, last_iso, None, int(fqv), float(mv), float(rd), float(f), float(m), seg(r,f,m)))
        conn.commit()
    finally:
        conn.close()

# ---- Anti-spam / cadence utilitaires ----
def has_active_cadence_for(contact_key: str, product_id: str) -> bool:
    if not (contact_key and product_id):
        return False
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "SELECT 1 FROM scheduled_notifications WHERE contact_key=? AND product_id=? AND sent_at IS NULL LIMIT 1",
            (contact_key, product_id)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()

def refresh_cadence_payload(contact_key: str, product_id: str, payload: dict) -> int:
    import json as _json
    if not (contact_key and product_id):
        return 0
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "UPDATE scheduled_notifications SET payload_json=? WHERE contact_key=? AND product_id=? AND sent_at IS NULL",
            (_json.dumps(payload, ensure_ascii=False), contact_key, product_id)
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()

def cancel_cadence_for(contact_key: str, product_id: str) -> int:
    if not (contact_key and product_id):
        return 0
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        cur = conn.execute(
            "DELETE FROM scheduled_notifications WHERE contact_key=? AND product_id=? AND sent_at IS NULL",
            (contact_key, product_id)
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()

def has_recent_contact_product_notification(email: str, phone: str, product_id: str, minutes: int) -> bool:
    if minutes <= 0:
        return False
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        lookback = f"-{minutes} minutes"
        cur = conn.execute(
            "SELECT 1 FROM notification_log WHERE sent_at >= datetime('now', ?) "
            "AND product_id = ? AND template_type LIKE 'relance_%' "
            "AND (recipient_email = ? OR recipient_phone = ?) LIMIT 1",
            (lookback, product_id or "", (email or "").lower().replace(" ",""), phone or "")
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


#--------------------------------------------------------- templates setings

def ensure_templates_schema():
    """
    Crée la table message_templates + index, sans UNIQUE avec expression dans la clause de table
    (SQLite l'interdit), mais avec un UNIQUE INDEX par expression IFNULL(product_id,'').
    """
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS message_templates (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          product_id    TEXT,                 -- NULL => global
          template_type TEXT NOT NULL,        -- ex: relance_t30, relance_t6h, confirm_3_2...
          channel       TEXT NOT NULL,        -- 'email' | 'whatsapp'
          subject       TEXT,                 -- email uniquement
          body          TEXT NOT NULL,        -- html (fragment ou complet) ou texte WhatsApp
          is_full_html  INTEGER NOT NULL DEFAULT 0,
          is_active     INTEGER NOT NULL DEFAULT 1,
          updated_at    DATETIME NOT NULL DEFAULT (datetime('now')),
          created_at    DATETIME NOT NULL DEFAULT (datetime('now'))
        );

        -- Index “normaux”
        CREATE INDEX IF NOT EXISTS idx_msgtpl_product  ON message_templates(product_id);
        CREATE INDEX IF NOT EXISTS idx_msgtpl_key      ON message_templates(template_type, channel);

        -- Contrainte d'unicité logique:
        -- on veut 1 seul template par (product_id||'' , template_type, channel).
        -- Impossible dans UNIQUE de table (expressions interdites), donc on passe par un UNIQUE INDEX.
        CREATE UNIQUE INDEX IF NOT EXISTS ux_msgtpl_norm
          ON message_templates(IFNULL(product_id,''), template_type, channel);
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_template(product_id, template_type, channel, subject, body, is_full_html=False, is_active=True):
    """
    UPSERT “manuel” compatible SQLite :
      - On cherche s'il existe déjà une ligne où IFNULL(product_id,'') = IFNULL(?, '')
        ET (template_type, channel) identiques.
      - Si oui: UPDATE
      - Sinon: INSERT

    Avantage: pas de dépendance à ON CONFLICT sur index d'expression.
    """
    pid_norm = "" if (product_id is None or str(product_id).strip() == "") else str(product_id).strip()
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT id FROM message_templates
            WHERE IFNULL(product_id,'') = ? AND template_type = ? AND channel = ?
            LIMIT 1
        """, (pid_norm, template_type, channel))
        row = cur.fetchone()
        if row:
            conn.execute("""
                UPDATE message_templates
                   SET subject = ?, body = ?, is_full_html = ?, is_active = ?,
                       updated_at = datetime('now')
                 WHERE id = ?
            """, (subject, body, 1 if is_full_html else 0, 1 if is_active else 0, row["id"]))
        else:
            conn.execute("""
                INSERT INTO message_templates (product_id, template_type, channel, subject, body, is_full_html, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (None if pid_norm == "" else pid_norm,
                  template_type, channel, subject, body,
                  1 if is_full_html else 0, 1 if is_active else 0))
        conn.commit()
    finally:
        conn.close()


def get_template(product_id, template_type, channel):
    """
    Résolution par priorité:
      1) Template spécifique produit (product_id exact)
      2) Template global (product_id IS NULL)
    Retourne un dict (ou None).
    """
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 1) spécifique
        if product_id:
            cur = conn.execute("""
              SELECT * FROM message_templates
               WHERE product_id = ? AND template_type = ? AND channel = ? AND is_active = 1
               LIMIT 1
            """, (product_id, template_type, channel))
            row = cur.fetchone()
            if row:
                return dict(row)
        # 2) global
        cur = conn.execute("""
          SELECT * FROM message_templates
            WHERE product_id IS NULL AND template_type = ? AND channel = ? AND is_active = 1
            LIMIT 1
        """, (template_type, channel))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
