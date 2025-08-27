import sqlite3
from webhook_app.config import Config

def ensure_fact_sales_failed_at():
    """
    - Ajoute la colonne fact_sales.failed_at (INTEGER epoch) si manquante
    - Backfill failed_at pour les lignes failed
    - Normalise les colonnes *_at en INTEGER quand elles sont encore TEXT
    - Crée l'index (status, failed_at)
    Idempotent: peut être appelé à chaque démarrage.
    """
    conn = sqlite3.connect(Config.DB_PATH)
    try:
        # Rend SQLite plus tolérant aux locks et crash-safe
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")

        # 1) Colonne manquante ?
        cur = conn.execute("PRAGMA table_info(fact_sales)")
        cols = {row[1] for row in cur.fetchall()}
        if "failed_at" not in cols:
            conn.execute("ALTER TABLE fact_sales ADD COLUMN failed_at INTEGER;")

        # 2) Normalisation: TEXT -> EPOCH (INTEGER) sur les colonnes *_at existantes
        #   
        for col in ("created_at", "completed_at", "abandoned_at", "failed_at"):

            if col != "failed_at" and col not in cols:
                continue
            conn.execute(f"""
                UPDATE fact_sales
                   SET {col} = CAST(strftime('%s', {col}) AS INTEGER)
                 WHERE typeof({col})='text' AND {col} IS NOT NULL AND {col} <> '';
            """)

        # 3) Backfill failed_at pour les lignes status='failed' où failed_at est NULL
        #    On prend la meilleure info dispo: failed_at (si text), sinon abandoned_at, sinon completed_at, sinon created_at
        conn.execute("""
            UPDATE fact_sales
               SET failed_at = COALESCE(
                    failed_at,
                    abandoned_at,
                    completed_at,
                    created_at
               )
             WHERE status='failed' AND failed_at IS NULL;
        """)

        # 4) Index utile pour tes KPI
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_failed ON fact_sales(status, failed_at);")

        conn.commit()
    finally:
        conn.close()
