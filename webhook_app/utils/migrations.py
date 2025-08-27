# webhook_app/utils/migrations.py
import os, sqlite3
from webhook_app.config import Config

def ensure_fact_sales_failed_at():
    
    # S'assure que le dossier existe
    os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)

    conn = sqlite3.connect(Config.DB_PATH, timeout=10)
    try:
        # PRAGMA en best-effort (ne jamais faire planter le déploiement ici)
        try:
            conn.execute("PRAGMA busy_timeout=5000;")
            # WAL si possible, sinon on ignore tranquillement
            mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
            if (mode or "").lower() != "wal":
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                except sqlite3.OperationalError:
                    pass
        except Exception:
            pass

        # 0) La table existe ?
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_sales' LIMIT 1;"
        ).fetchone()
        if not row:
            # Rien à faire au premier boot (ensure_fact_dims_schema créera la table)
            return

        # 1) Colonne déjà là ?
        cols = {r[1] for r in conn.execute("PRAGMA table_info(fact_sales)").fetchall()}
        if "failed_at" not in cols:
            conn.execute("ALTER TABLE fact_sales ADD COLUMN failed_at INTEGER;")

            # 2) Backfill pour les lignes déjà 'failed'
            #    (garde epoch si déjà INTEGER, convertit si TEXT)
            conn.execute("""
                UPDATE fact_sales
                SET failed_at = CASE
                  WHEN status='failed' AND failed_at IS NULL THEN
                    COALESCE(
                      CASE WHEN typeof(failed_at)='integer'   THEN failed_at
                           WHEN typeof(failed_at)='text'      THEN CAST(strftime('%s',failed_at)    AS INTEGER)
                      END,
                      CASE WHEN typeof(abandoned_at)='integer' THEN abandoned_at
                           WHEN typeof(abandoned_at)='text'    THEN CAST(strftime('%s',abandoned_at) AS INTEGER)
                      END,
                      CASE WHEN typeof(completed_at)='integer' THEN completed_at
                           WHEN typeof(completed_at)='text'    THEN CAST(strftime('%s',completed_at) AS INTEGER)
                      END,
                      CASE WHEN typeof(created_at)='integer'   THEN created_at
                           WHEN typeof(created_at)='text'      THEN CAST(strftime('%s',created_at)   AS INTEGER)
                      END
                    )
                  ELSE failed_at
                END
            """)

        # 3) Index idempotent
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_failed ON fact_sales(status, failed_at);")
        conn.commit()
    finally:
        conn.close()
