import os, time, logging, datetime, json
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, Json

from webhook_app.config import Config

logger = logging.getLogger(__name__)

_POOL: Optional[pool.SimpleConnectionPool] = None


def init_pool():
    global _POOL
    if _POOL is not None:
        return _POOL
    
    dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or getattr(Config, "POSTGRES_URL", None)
    
    if not dsn:
        # Build from discrete vars if provided 
        host = os.getenv("PGHOST") or "localhost"
        port = int(os.getenv("PGPORT", "5432"))
        db   = os.getenv("PGDATABASE") or "postgres"
        user = os.getenv("PGUSER") or "postgres"
        pw   = os.getenv("PGPASSWORD") or "postgres"
        dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    minconn = int(os.getenv("PG_MINCONN", "1"))
    maxconn = int(os.getenv("PG_MAXCONN", "10"))
    _POOL = pool.SimpleConnectionPool(minconn, maxconn, dsn=dsn, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5)
    logger.info("PostgreSQL pool initialisé (%s..%s)", minconn, maxconn)
    return _POOL


def close_pool():
    global _POOL
    if _POOL is not None:
        _POOL.closeall()
        _POOL = None
        logger.info("PostgreSQL pool fermé")


@contextmanager
def get_connection(readonly: bool = False):
    if _POOL is None:
        init_pool()
    assert _POOL is not None
    conn = _POOL.getconn()
    try:
        # Active l'autocommit (DDL possible immédiatement)
        conn.autocommit = True
        # Évite de marquer la session en READ ONLY pour ne pas brider les DDL
        # Les requêtes read-only n'ont pas besoin d'un flag serveur read-only ici.
        yield conn
    finally:
        _POOL.putconn(conn)


def execute_with_retry(conn, sql: str, params: Iterable[Any] | None = None, *, fetch: str | None = None):
    attempts = int(os.getenv("PG_EXEC_RETRIES", "3"))
    base_delay = float(os.getenv("PG_EXEC_BASE_DELAY_MS", "100")) / 1000.0
    for i in range(attempts):
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params or ())
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return cur.rowcount
        except psycopg2.Error as e:
            msg = str(e).lower()
            # Retry on common transient errors
            if any(x in msg for x in ["deadlock", "timeout", "could not serialize", "connection reset", "connection refused", "terminating connection"]):
                time.sleep(base_delay * (2 ** i))
                continue
            raise
def read_mappings_from_db():
    """Charge les mappings depuis la base de données et les groupe par produit."""
    sql = """
        SELECT product_id, array_agg(drive_folder_id ORDER BY drive_folder_id) as folder_ids
        FROM product_drive_mapping
        GROUP BY product_id
        ORDER BY product_id;
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, fetch="all") or []
        # Convertit la liste de dictionnaires en un dictionnaire simple pour le template
        return {row['product_id']: row['folder_ids'] for row in rows}
    

def add_mapping_to_db(product_id: str, folder_id: str):
    """Ajoute un mapping dans la base de données, ignore les doublons."""
    # Ajout d'une description par défaut pour plus de clarté
    description = f"Mapping pour {product_id}"
    sql = """
        INSERT INTO product_drive_mapping (product_id, drive_folder_id, description)
        VALUES (%s, %s, %s)
        ON CONFLICT (product_id, drive_folder_id) DO NOTHING;
    """
    with get_connection() as conn:
        execute_with_retry(conn, sql, (product_id, folder_id, description))

def delete_mapping_from_db(product_id: str, folder_id: str):
    """Supprime un mapping spécifique de la base de données."""
    sql = """
        DELETE FROM product_drive_mapping
        WHERE product_id = %s AND drive_folder_id = %s;
    """
    with get_connection() as conn:
        execute_with_retry(conn, sql, (product_id, folder_id))

# --------------------------- Schema creation ---------------------------
def ensure_all_schemas():
    with get_connection() as conn:
        # webhook_events
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS webhook_events (
          id           SERIAL PRIMARY KEY,
          event_key    TEXT UNIQUE,
          event_name   TEXT,
          received_at  TIMESTAMP NOT NULL DEFAULT NOW(),
          payload_json JSONB NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_we_received_at ON webhook_events(received_at);
        """)

        # processed_sales
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS processed_sales (
          sale_id TEXT PRIMARY KEY,
          status  TEXT,
          processed_at TIMESTAMP NOT NULL DEFAULT NOW()
        );
        """)

        # notification_log 
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS notification_log (
          id              SERIAL PRIMARY KEY,
          sale_id         TEXT NOT NULL,
          channel         TEXT NOT NULL,
          template_type   TEXT NOT NULL,
          recipient       TEXT,
          sent_at         TIMESTAMP NOT NULL DEFAULT NOW(),
          recipient_email TEXT,
          recipient_phone TEXT,
          contact_key     TEXT,
          product_id      TEXT,
          ab_arm          TEXT,
          UNIQUE (sale_id, channel, template_type)
        );
        CREATE INDEX IF NOT EXISTS idx_notification_log_recipient ON notification_log(recipient);
        CREATE INDEX IF NOT EXISTS idx_log_contact_product_time ON notification_log(contact_key, product_id, sent_at);
        """)

        # scheduled_notifications
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS scheduled_notifications (
          id             SERIAL PRIMARY KEY,
          sale_id        TEXT NOT NULL,
          template_type  TEXT NOT NULL,
          due_at         TIMESTAMP NOT NULL,
          payload_json   JSONB NOT NULL,
          created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
          sent_at        TIMESTAMP,
          error          TEXT,
          contact_key    TEXT,
          product_id     TEXT,
          ab_arm         TEXT,
          UNIQUE (sale_id, template_type, due_at)
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_notifications(due_at);
        CREATE INDEX IF NOT EXISTS idx_sched_contact_product_sent ON scheduled_notifications(contact_key, product_id, sent_at);
        CREATE INDEX IF NOT EXISTS idx_sched_contact_product_due ON scheduled_notifications(contact_key, product_id, due_at);
        CREATE INDEX IF NOT EXISTS idx_sched_due_unsent ON scheduled_notifications(due_at) WHERE sent_at IS NULL;
        """)

        # fact_sales
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS fact_sales (
          sale_id TEXT PRIMARY KEY,
          status TEXT,
          amount_value DOUBLE PRECISION,
          currency TEXT,
          product_id TEXT,
          product_name TEXT,
          store_id TEXT,
          store_name TEXT,
          contact_key TEXT,
          email TEXT,
          phone TEXT,
          country TEXT,
          created_at TIMESTAMP,
          completed_at TIMESTAMP,
          abandoned_at TIMESTAMP,
          failed_at TIMESTAMP,
          time_to_complete_min DOUBLE PRECISION,
          hour_of_day INTEGER,
          dow INTEGER,
          month VARCHAR(7),
          utm_source TEXT,
          utm_medium TEXT,
          utm_campaign TEXT,
          price_tier TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fact_created ON fact_sales(created_at);
        CREATE INDEX IF NOT EXISTS idx_fact_contact_prod ON fact_sales(contact_key, product_id);
        CREATE INDEX IF NOT EXISTS idx_fact_failed ON fact_sales(status, failed_at);
        """)

        # dim_customer
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS dim_customer (
          contact_key TEXT PRIMARY KEY,
          first_seen TIMESTAMP,
          last_seen TIMESTAMP,
          country TEXT,
          orders_count INTEGER DEFAULT 0,
          gmv_total DOUBLE PRECISION DEFAULT 0,
          rfm_recency_days DOUBLE PRECISION,
          rfm_frequency DOUBLE PRECISION,
          rfm_monetary DOUBLE PRECISION,
          rfm_segment TEXT
        );
        """)

        # dim_product
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS dim_product (
          product_id TEXT PRIMARY KEY,
          product_name TEXT,
          first_seen TIMESTAMP
        );
        """)

        # message_templates
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS message_templates (
          id            SERIAL PRIMARY KEY,
          product_id    TEXT,
          template_type TEXT NOT NULL,
          channel       TEXT NOT NULL,
          subject       TEXT,
          body          TEXT NOT NULL,
          is_full_html  BOOLEAN NOT NULL DEFAULT FALSE,
          is_active     BOOLEAN NOT NULL DEFAULT TRUE,
          updated_at    TIMESTAMP NOT NULL DEFAULT NOW(),
          created_at    TIMESTAMP NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_msgtpl_product  ON message_templates(product_id);
        CREATE INDEX IF NOT EXISTS idx_msgtpl_key      ON message_templates(template_type, channel);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_msgtpl_norm
          ON message_templates((COALESCE(product_id,'')), template_type, channel);
        """)

        # product_drive_mapping
        execute_with_retry(conn, """
            CREATE TABLE IF NOT EXISTS product_drive_mapping (
                id SERIAL PRIMARY KEY,
                product_id TEXT NOT NULL,
                drive_folder_id TEXT NOT NULL,
                description TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                
                -- Empêche d'ajouter deux fois le même dossier pour le même produit
                UNIQUE (product_id, drive_folder_id) 
            );
            CREATE INDEX IF NOT EXISTS idx_mapping_product_id ON product_drive_mapping(product_id);
        """)


# --------------------------- Helpers ---------------------------
def _parse_timestamp(value) -> Optional[datetime.datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.datetime.utcfromtimestamp(int(value))
    s = str(value).strip()
    try:
        # Allow Z suffix
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(datetime.timezone.utc).replace(tzinfo=None)
    except Exception:
        return None


# --------------------------- Webhook raw ---------------------------
def _event_key_from_payload(payload: dict) -> str:
    event = str(payload.get("event") or "evt")
    sale_id = str(payload.get("sale", {}).get("id") or "unknown")
    created_at = str(payload.get("sale", {}).get("created_at") or "")
    return f"{event}:{sale_id}:{created_at}"


def save_webhook_raw(payload: dict, source: str = "webhook") -> int:
    ek = _event_key_from_payload(payload)
    with get_connection() as conn:
        execute_with_retry(conn,
            """
            INSERT INTO webhook_events(event_key, event_name, payload_json)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_key) DO NOTHING
            """,
            (ek, str(payload.get("event") or ""), Json(payload))
        )
        row = execute_with_retry(conn, "SELECT id FROM webhook_events WHERE event_key = %s LIMIT 1", (ek,), fetch="one")
        return int(row["id"]) if row else 0


# --------------------------- Scheduled notifications ---------------------------
def enqueue_notification(
    sale_id: str,
    template_type: str,
    due_at,
    payload: dict,
    *,
    contact_key: str | None = None,
    product_id: str | None = None,
    ab_arm: str | None = None,
):
    due_ts = _parse_timestamp(due_at) or datetime.datetime.utcnow()
    with get_connection() as conn:
        execute_with_retry(conn,
            """
            INSERT INTO scheduled_notifications
              (sale_id, template_type, due_at, payload_json, contact_key, product_id, ab_arm)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (sale_id, template_type, due_at) DO NOTHING
            """,
            (sale_id, template_type, due_ts, Json(payload), contact_key, product_id, ab_arm)
        )


def fetch_due_scheduled(limit: int = 50):
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn,
            """
            SELECT id, sale_id, template_type, due_at, payload_json, created_at, sent_at, error, contact_key, product_id, ab_arm
            FROM scheduled_notifications
            WHERE sent_at IS NULL AND due_at <= NOW()
            ORDER BY due_at ASC, id ASC
            LIMIT %s
            """,
            (limit,),
            fetch="all"
        ) or []
        # Convert JSONB to string for scheduler compatibility
        for r in rows:
            if isinstance(r.get("payload_json"), (dict, list)):
                r["payload_json"] = json.dumps(r["payload_json"], ensure_ascii=False)
        return rows


def claim_scheduled_job(job_id: int) -> bool:
    with get_connection() as conn:
        rc = execute_with_retry(conn,
            """
            UPDATE scheduled_notifications
               SET sent_at = NOW(), error = NULL
             WHERE id = %s AND sent_at IS NULL
            """,
            (job_id,)
        )
        return (rc or 0) == 1

# ------- delete relance----- #


def get_pending_relances_by_contact():
    """
    Récupère un résumé des relances en attente, groupé par client (contact_key).
    """
    sql = """
        SELECT
            contact_key,
            COUNT(*) AS relance_count,
            MIN(due_at) AS next_relance_at
        FROM scheduled_notifications
        WHERE sent_at IS NULL AND contact_key IS NOT NULL AND contact_key <> ''
        GROUP BY contact_key
        ORDER BY MIN(due_at) ASC;
    """
    with get_connection(readonly=True) as conn:
        return execute_with_retry(conn, sql, fetch="all") or []


def get_pending_relances(contact_key: str = None):
    """
    Récupère les notifications programmées, avec un filtre optionnel par contact_key.
    """
    params = []
    sql = """
        SELECT id, sale_id, template_type, due_at, contact_key, product_id, error
        FROM scheduled_notifications
        WHERE sent_at IS NULL
    """
    if contact_key:
        sql += " AND contact_key = %s"
        params.append(contact_key)
    
    sql += " ORDER BY due_at ASC;"
    
    with get_connection(readonly=True) as conn:
        return execute_with_retry(conn, sql, params, fetch="all") or []
    
def update_relance_due_at(job_id: int, new_due_at):
    """
    Met à jour la date d'échéance (due_at) d'une notification programmée.
    """
    # On s'assure que new_due_at est un objet datetime si c'est une chaîne
    if isinstance(new_due_at, str):
        from datetime import datetime
        new_due_at = datetime.fromisoformat(new_due_at)

    sql = """
        UPDATE scheduled_notifications
        SET due_at = %s
        WHERE id = %s AND sent_at IS NULL;
    """
    with get_connection() as conn:
        return execute_with_retry(conn, sql, (new_due_at, job_id))

def cancel_relance_by_id(job_id: int):
    """
    Supprime une notification programmée de la file d'attente par son ID.
    """
    sql = "DELETE FROM scheduled_notifications WHERE id = %s AND sent_at IS NULL;"
    with get_connection() as conn:
        return execute_with_retry(conn, sql, (job_id,))



def mark_scheduled_error(sched_id: int, error: str):
    with get_connection() as conn:
        execute_with_retry(conn, "UPDATE scheduled_notifications SET error = %s WHERE id = %s", (error, sched_id))


def get_drive_mappings(product_id: str) -> list[str]:
    """
    Récupère la liste des ID de dossiers Google Drive associés à un product_id.
    """
    if not product_id:
        return []
    
    sql = "SELECT drive_folder_id FROM product_drive_mapping WHERE product_id = %s"
    
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, (product_id,), fetch="all") or []
        # Retourne une liste simple des IDs de dossier
        return [row['drive_folder_id'] for row in rows]
    



# --------------------------- Processed + notifications ---------------------------
class Database:
    def __init__(self):
        ensure_all_schemas()

    def has_processed(self, sale_id: str) -> bool:
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(conn, "SELECT 1 FROM processed_sales WHERE sale_id = %s LIMIT 1", (sale_id,), fetch="one")
            return row is not None

    def mark_processed(self, sale_id: str, status: str):
        with get_connection() as conn:
            execute_with_retry(conn,
                """
                INSERT INTO processed_sales(sale_id, status) VALUES (%s,%s)
                ON CONFLICT (sale_id) DO NOTHING
                """,
                (sale_id, status)
            )

    def has_notified(self, sale_id: str, channel: str, template_type: str) -> bool:
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(conn,
                "SELECT 1 FROM notification_log WHERE sale_id=%s AND channel=%s AND template_type=%s LIMIT 1",
                (sale_id, channel, template_type),
                fetch="one"
            )
            return row is not None

    def has_notified_recipient(self, recipient: str, channel: str, template_type: str, window_days: int | None = None) -> bool:
        if not recipient:
            return False
        with get_connection(readonly=True) as conn:
            if window_days and window_days > 0:
                row = execute_with_retry(conn,
                    """
                    SELECT 1 FROM notification_log
                    WHERE recipient=%s AND channel=%s AND template_type=%s
                      AND sent_at >= NOW() - (%s || ' days')::INTERVAL
                    LIMIT 1
                    """,
                    (recipient, channel, template_type, str(int(window_days))),
                    fetch="one"
                )
            else:
                row = execute_with_retry(conn,
                    "SELECT 1 FROM notification_log WHERE recipient=%s AND channel=%s AND template_type=%s LIMIT 1",
                    (recipient, channel, template_type),
                    fetch="one"
                )
            return row is not None

    def mark_notified(
        self,
        sale_id: str,
        channel: str,
        template_type: str,
        recipient: str | None = None,
        *,
        recipient_email: str | None = None,
        recipient_phone: str | None = None,
        contact_key: str | None = None,
        product_id: str | None = None,
        ab_arm: str | None = None,
    ):
        # Backfill depuis fact_sales si disponible
        with get_connection() as conn:
            row = execute_with_retry(conn,
                "SELECT product_id, contact_key, email, phone FROM fact_sales WHERE sale_id=%s LIMIT 1",
                (sale_id,),
                fetch="one"
            )
            if row:
                fs_pid = (row.get("product_id") or "").strip()
                fs_ck  = (row.get("contact_key") or "").strip() or None
                if not product_id or product_id != fs_pid:
                    product_id = fs_pid or product_id
                if not contact_key:
                    contact_key = fs_ck
                if not recipient_email:
                    recipient_email = (row.get("email") or None)
                if not recipient_phone:
                    recipient_phone = (row.get("phone") or None)

            execute_with_retry(conn,
                """
                INSERT INTO notification_log(
                  sale_id, channel, template_type, recipient, recipient_email, recipient_phone,
                  contact_key, product_id, ab_arm, sent_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (sale_id, channel, template_type) DO NOTHING
                """,
                (sale_id, channel, template_type, recipient, recipient_email, recipient_phone, contact_key, product_id, ab_arm)
            )


# --------------------------- Analytics ETL ---------------------------
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
            name = str(f.get("name",""))
            v = f.get("value")
            if name.lower() in ("utm_source","source"): src = v
            elif name.lower() in ("utm_medium","medium"): med = v
            elif name.lower() in ("utm_campaign","campaign"): camp = v
    except Exception:
        pass
    return src, med, camp


def upsert_fact_from_webhook(payload: dict):
    s    = payload.get("sale", {}) or {}
    prod = payload.get("product", {}) or {}
    cust = payload.get("customer", {}) or {}
    store= payload.get("store", {}) or {}

    sale_id = str(s.get("id") or "")
    status  = (s.get("status") or "").strip().lower()

    amt = s.get("amount")
    if isinstance(amt, dict):
        amount   = float(amt.get("value") or 0.0)
        currency = amt.get("currency")
    else:
        amount   = float(amt or 0.0)
        currency = s.get("currency")
    if not currency:
        pv = prod.get("price")
        if isinstance(pv, dict):
            currency = pv.get("currency")

    created   = _parse_timestamp(s.get("created_at"))
    completed = _parse_timestamp(s.get("completed_at"))
    abandoned = _parse_timestamp(s.get("abandoned_at"))
    failed    = _parse_timestamp(s.get("failed_at"))

    if status == "completed" and not completed:
        completed = created
    if status == "failed" and not failed:
        failed = _parse_timestamp(s.get("failed_at")) or abandoned or completed or created

    product_id   = prod.get("id") or None
    product_name = prod.get("name") or None
    store_id     = store.get("id") or None
    store_name   = store.get("name") or None

    email   = cust.get("email") or None
    phone   = cust.get("phone") or None
    country = cust.get("country") or None
    contact = _contact_key(email, phone)

    ttc_min = round(((completed - created).total_seconds())/60.0, 2) if (created and completed) else None
    t_ref = completed or abandoned or failed or created
    if t_ref:
        dt = t_ref
        hod = dt.hour
        dow = dt.weekday()  # 0 = lundi
        month = dt.strftime("%Y-%m")
    else:
        hod = dow = month = None

    utm_source, utm_medium, utm_campaign = _get_utm(s.get("custom_fields"))
    price_tier = _price_tier(amount)

    with get_connection() as conn:
        execute_with_retry(conn,
            """
            INSERT INTO fact_sales (
              sale_id, status, amount_value, currency,
              product_id, product_name, store_id, store_name,
              contact_key, email, phone, country,
              created_at, completed_at, abandoned_at, failed_at,
              time_to_complete_min, hour_of_day, dow, month,
              utm_source, utm_medium, utm_campaign, price_tier
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (sale_id) DO UPDATE SET
              status = EXCLUDED.status,
              amount_value = COALESCE(EXCLUDED.amount_value, fact_sales.amount_value),
              currency     = COALESCE(EXCLUDED.currency,     fact_sales.currency),
              product_id   = COALESCE(EXCLUDED.product_id,   fact_sales.product_id),
              product_name = COALESCE(EXCLUDED.product_name, fact_sales.product_name),
              store_id     = COALESCE(EXCLUDED.store_id,     fact_sales.store_id),
              store_name   = COALESCE(EXCLUDED.store_name,   fact_sales.store_name),
              contact_key  = COALESCE(EXCLUDED.contact_key,  fact_sales.contact_key),
              email        = COALESCE(EXCLUDED.email,        fact_sales.email),
              phone        = COALESCE(EXCLUDED.phone,        fact_sales.phone),
              country      = COALESCE(EXCLUDED.country,      fact_sales.country),
              created_at   = COALESCE(fact_sales.created_at, EXCLUDED.created_at),
              completed_at = COALESCE(EXCLUDED.completed_at, fact_sales.completed_at),
              abandoned_at = COALESCE(EXCLUDED.abandoned_at, fact_sales.abandoned_at),
              failed_at    = COALESCE(EXCLUDED.failed_at,    fact_sales.failed_at),
              time_to_complete_min = COALESCE(EXCLUDED.time_to_complete_min, fact_sales.time_to_complete_min),
              hour_of_day  = COALESCE(EXCLUDED.hour_of_day,  fact_sales.hour_of_day),
              dow          = COALESCE(EXCLUDED.dow,          fact_sales.dow),
              month        = COALESCE(EXCLUDED.month,        fact_sales.month),
              utm_source   = COALESCE(EXCLUDED.utm_source,   fact_sales.utm_source),
              utm_medium   = COALESCE(EXCLUDED.utm_medium,   fact_sales.utm_medium),
              utm_campaign = COALESCE(EXCLUDED.utm_campaign, fact_sales.utm_campaign),
              price_tier   = COALESCE(EXCLUDED.price_tier,   fact_sales.price_tier)
            """,
            (
                sale_id, status, amount, currency,
                product_id, product_name, store_id, store_name,
                contact, email, phone, country,
                created, completed, abandoned, failed,
                ttc_min, hod, dow, month,
                utm_source, utm_medium, utm_campaign, price_tier
            )
        )

        if product_id:
            first_seen = created or datetime.datetime.utcnow()
            execute_with_retry(conn,
                """
                INSERT INTO dim_product(product_id, product_name, first_seen)
                VALUES (%s,%s,%s)
                ON CONFLICT (product_id) DO UPDATE SET product_name = EXCLUDED.product_name
                """,
                (product_id, product_name, first_seen)
            )

        if contact:
            now_ts = datetime.datetime.utcnow()
            first_ts = created or now_ts
            last_ts  = (completed or created) or now_ts
            execute_with_retry(conn,
                """
                INSERT INTO dim_customer(contact_key, first_seen, last_seen, country)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (contact_key) DO NOTHING
                """,
                (contact, first_ts, last_ts, country)
            )

def rfm_recompute():
    with get_connection() as conn:
        rows = execute_with_retry(conn, """
            SELECT contact_key,
                   MIN(COALESCE(created_at, completed_at)) AS first_seen,
                   MAX(COALESCE(completed_at, created_at)) AS last_seen,
                   COUNT(CASE WHEN status='completed' THEN 1 END) AS orders_count,
                   COALESCE(SUM(CASE WHEN status='completed' THEN amount_value END),0) AS gmv_total
            FROM fact_sales
            WHERE contact_key IS NOT NULL
            GROUP BY contact_key
        """, fetch="all") or []

        now = datetime.datetime.utcnow()
        rec_days, freqs, mons, tmp = [], [], [], []
        for r in rows:
            fs_dt = r.get("first_seen") or now
            ls_dt = r.get("last_seen") or now
            if isinstance(fs_dt, str):
                fs_dt = _parse_timestamp(fs_dt) or now
            if isinstance(ls_dt, str):
                ls_dt = _parse_timestamp(ls_dt) or now
            recency_days = (now - ls_dt).total_seconds()/86400.0
            freq = int(r.get("orders_count") or 0)
            mon  = float(r.get("gmv_total") or 0)
            rec_days.append(recency_days); freqs.append(freq); mons.append(mon)
            tmp.append((r.get("contact_key"), recency_days, freq, mon, fs_dt, ls_dt))

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

        # CORRECTION ICI: Ajouter 'conn' comme premier paramètre

        for ck, rd, fqv, mv, fs_dt, ls_dt in tmp:
            r = score_recency(rd); f = score_quantile(fqv, fq); m = score_quantile(mv, mq)
            execute_with_retry(conn,  
                """
                INSERT INTO dim_customer(contact_key, first_seen, last_seen, country, orders_count, gmv_total,
                                         rfm_recency_days, rfm_frequency, rfm_monetary, rfm_segment)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (contact_key) DO UPDATE SET
                  first_seen=EXCLUDED.first_seen,
                  last_seen=EXCLUDED.last_seen,
                  orders_count=EXCLUDED.orders_count,
                  gmv_total=EXCLUDED.gmv_total,
                  rfm_recency_days=EXCLUDED.rfm_recency_days,
                  rfm_frequency=EXCLUDED.rfm_frequency,
                  rfm_monetary=EXCLUDED.rfm_monetary,
                  rfm_segment=EXCLUDED.rfm_segment
                """,
                (ck, fs_dt, ls_dt, None, int(fqv), float(mv), float(rd), float(f), float(m), seg(r,f,m))
            )

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
            execute_with_retry(conn,
                """
                INSERT INTO dim_customer(contact_key, first_seen, last_seen, country, orders_count, gmv_total,
                                         rfm_recency_days, rfm_frequency, rfm_monetary, rfm_segment)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (contact_key) DO UPDATE SET
                  first_seen=EXCLUDED.first_seen,
                  last_seen=EXCLUDED.last_seen,
                  orders_count=EXCLUDED.orders_count,
                  gmv_total=EXCLUDED.gmv_total,
                  rfm_recency_days=EXCLUDED.rfm_recency_days,
                  rfm_frequency=EXCLUDED.rfm_frequency,
                  rfm_monetary=EXCLUDED.rfm_monetary,
                  rfm_segment=EXCLUDED.rfm_segment
                """,
                (ck, fs_dt, ls_dt, None, int(fqv), float(mv), float(rd), float(f), float(m), seg(r,f,m))
            )


# --------------------------- Cadence / anti-spam ---------------------------
def has_active_cadence_for(contact_key: str, product_id: str) -> bool:
    if not (contact_key and product_id):
        return False
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn,
            "SELECT 1 FROM scheduled_notifications WHERE contact_key=%s AND product_id=%s AND sent_at IS NULL LIMIT 1",
            (contact_key, product_id), fetch="one")
        return row is not None


def refresh_cadence_payload(contact_key: str, product_id: str, payload: dict) -> int:
    if not (contact_key and product_id):
        return 0
    with get_connection() as conn:
        rc = execute_with_retry(conn,
            "UPDATE scheduled_notifications SET payload_json=%s WHERE contact_key=%s AND product_id=%s AND sent_at IS NULL",
            (Json(payload), contact_key, product_id))
        return rc or 0


def cancel_cadence_for(contact_key: str, product_id: str) -> int:
    if not (contact_key and product_id):
        return 0
    with get_connection() as conn:
        rc = execute_with_retry(conn,
            "DELETE FROM scheduled_notifications WHERE contact_key=%s AND product_id=%s AND sent_at IS NULL",
            (contact_key, product_id))
        return rc or 0


def has_confirmation_for_contact_product(contact_key: str, product_id: str) -> bool:
    """
    Vérifie si une notification de confirmation a déjà été envoyée pour une paire contact/produit.
    """
    if not (contact_key and product_id):
        return False
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn,
            """
            SELECT 1 FROM notification_log
            WHERE contact_key=%s AND product_id=%s AND template_type LIKE 'confirm_%%'
            LIMIT 1
            """,
            (contact_key, product_id), fetch="one")
        return row is not None


def latest_relance_step(contact_key: str, product_id: str) -> str | None:
    if not (contact_key and product_id):
        return None
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(conn,
            """
            SELECT template_type
            FROM notification_log
            WHERE contact_key=%s AND product_id=%s
              AND template_type IN ('relance_t30','relance_t6h','relance_t23h','relance_t47h')
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (contact_key, product_id), fetch="one")
        if not row:
            return None
        tt = str(row["template_type"]).lower()
        if tt.endswith("t47h"): return "t47h"
        if tt.endswith("t23h"): return "t23h"
        if tt.endswith("t6h"):  return "t6h"
        return "t30"


def has_recent_contact_product_notification(
    email: str, 
    phone: str, 
    product_id: str, 
    minutes: int
) -> bool:
    """Vérifie s'il y a eu une notification récente pour ce contact/produit."""
    if minutes <= 0:
        return False
    
    # CORRECTION: Utiliser get_connection() pour obtenir conn
    with get_connection(readonly=True) as conn:
        from datetime import timedelta
        threshold = datetime.datetime.now() - timedelta(minutes=minutes)
        
        row = execute_with_retry(conn, """
            SELECT 1 FROM notification_log
            WHERE sent_at >= %s
              AND product_id = %s
              AND template_type LIKE 'relance_%%'
              AND (recipient_email = %s OR recipient_phone = %s)
            LIMIT 1
        """, (threshold, product_id or "", (email or "").lower().replace(" ", ""), phone or ""), fetch="one")
        return row is not None

# --------------------------- Templates ---------------------------
def upsert_template(product_id, template_type, channel, subject, body, is_full_html=False, is_active=True):
    pid_norm = None if (product_id is None or str(product_id).strip() == "") else str(product_id).strip()
    with get_connection() as conn:
        row = execute_with_retry(conn,
            """
            SELECT id FROM message_templates
            WHERE COALESCE(product_id,'') = COALESCE(%s,'') AND template_type=%s AND channel=%s
            LIMIT 1
            """,
            (pid_norm, template_type, channel), fetch="one")
        if row:
            execute_with_retry(conn,
                """
                UPDATE message_templates
                   SET subject=%s, body=%s, is_full_html=%s, is_active=%s, updated_at=NOW()
                 WHERE id=%s
                """,
                (subject, body, bool(is_full_html), bool(is_active), row["id"]))
        else:
            execute_with_retry(conn,
                """
                INSERT INTO message_templates(product_id, template_type, channel, subject, body, is_full_html, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (pid_norm, template_type, channel, subject, body, bool(is_full_html), bool(is_active)))


def get_template(product_id, template_type, channel):
    with get_connection(readonly=True) as conn:
        if product_id:
            row = execute_with_retry(conn,
                """
                SELECT * FROM message_templates
                 WHERE product_id=%s AND template_type=%s AND channel=%s AND is_active = TRUE
                 LIMIT 1
                """,
                (product_id, template_type, channel), fetch="one")
            if row:
                return dict(row)

        row = execute_with_retry(conn,
            """
            SELECT * FROM message_templates
             WHERE product_id IS NULL AND template_type=%s AND channel=%s AND is_active = TRUE
             LIMIT 1
            """,
            (template_type, channel), fetch="one")
        return dict(row) if row else None