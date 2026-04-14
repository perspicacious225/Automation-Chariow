import os
from pathlib import Path
from dotenv import load_dotenv # type: ignore
load_dotenv()

BASE_DIR = os.path.dirname(__file__)

def _local_db():   return os.path.join(BASE_DIR, "data", "database.sqlite")
def _local_dump(): return os.path.join(BASE_DIR, "templates", "last_webhook.json")

# Détection Render (fallback si non writable)
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))

default_db   = "/opt/data/database.sqlite" if IS_RENDER else _local_db()
default_dump = "/opt/data/last_webhook.json" if IS_RENDER else _local_dump()

DB_PATH = os.getenv("DATABASE_URL", default_db)
WEBHOOK_DUMP_PATH = os.getenv("WEBHOOK_DUMP_PATH", default_dump)

def _ensure_writable(path, fallback):
    d = os.path.dirname(path) or "."
    try:
        os.makedirs(d, exist_ok=True)
        test = os.path.join(d, ".writetest")
        with open(test, "w") as f:
            f.write("x")
        os.remove(test)
        return path
    except Exception:
        d2 = os.path.dirname(fallback) or "."
        os.makedirs(d2, exist_ok=True)
        return fallback

DB_PATH = _ensure_writable(DB_PATH, _local_db())
WEBHOOK_DUMP_PATH = _ensure_writable(WEBHOOK_DUMP_PATH, _local_dump())

class Config:
    DB_PATH = DB_PATH
    WEBHOOK_DUMP_PATH = WEBHOOK_DUMP_PATH
    SECRET_KEY = os.getenv("SECRET_KEY")
    # WhatsApp
    INSTANCE_ID = os.getenv("WHATSAPP_INSTANCE_ID")
    TOKEN       = os.getenv("WHATSAPP_TOKEN")
    API_URL     = (
        f"https://api.green-api.com/waInstance{INSTANCE_ID}/sendMessage/{TOKEN}"
        if INSTANCE_ID and TOKEN else None
    )

    # Google Sheets
    CREDS_PATH  = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    SHEET_ID    = os.getenv("GOOGLE_SHEET_ID")

    # Sécurité Webhook / Email
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
    SENDER_EMAIL   = os.getenv("SENDER_EMAIL")
    PRICE_FALLBACK_MULTIPLIER = float(os.getenv("PRICE_FALLBACK_MULTIPLIER", "0"))

    # ==== Anti-spam (client + produit) ====
    # Fenêtre anti-redémarrage d'une nouvelle cadence pour le même contact+produit 
    NOTIFY_DEDUPE_WINDOW_MINUTES = int(os.getenv("NOTIFY_DEDUPE_WINDOW_MINUTES", "0") or "0")
    if NOTIFY_DEDUPE_WINDOW_MINUTES == 0:
        # rétro-compat : jours -> minutes
        NOTIFY_DEDUPE_WINDOW_MINUTES = int(os.getenv("NOTIFY_DEDUPE_WINDOW_DAYS", "0") or "0") * 1440

    # Politique quand un nouveau sale_id arrive alors qu'une cadence est active :
    # - "refresh": met à jour les jobs non envoyés avec le dernier sale_id/checkout_url
    # - "ignore" : ne rien faire (la cadence actuelle continue telle quelle)
    CADENCE_REFRESH_POLICY = os.getenv("CADENCE_REFRESH_POLICY", "refresh").strip().lower()

    # --- Sécurité Dashboard ---
    DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN")

    # --- A/B split des timings (utilisé par Notifier) ---
    AB_TEST_SPLIT_PERCENT = int(os.getenv("AB_TEST_SPLIT_PERCENT", "50")) 

    # --- Seuils d'alertes (utilisés par services/alerts.py) ---
    ALERT_EMAILS = [
        e.strip()
        for e in (os.getenv("ALERT_EMAILS", os.getenv("SENDER_EMAIL", "")) or "").split(",")
        if e.strip()
    ]
    ALERT_ERROR_THRESHOLD_24H = int(os.getenv("ALERT_ERROR_THRESHOLD_24H", "10"))
    ALERT_ABANDON_SPIKE_FACTOR = float(os.getenv("ALERT_ABANDON_SPIKE_FACTOR", "2.0"))
    ALERT_CONV_DROP_FACTOR = float(os.getenv("ALERT_CONV_DROP_FACTOR", "0.5"))


# ===== Strategy parameters =====
# RELANCE_DELAYS = {
#     "t30": 1,
#     "t6h": 3,
#     "t23h": 5,
#     "t47h": 6
# }
# Valeurs PROD recommandées :
RELANCE_DELAYS = {
    "t30": 5,
    "t6h": 6*60,
    "t23h": 23*60,
    "t47h": 47*60
}

# ===== A/B test des timings =====
RELANCE_DELAYS_A = RELANCE_DELAYS  # bras contrôle

# RELANCE_DELAYS_B = {
#     "t30": 1,
#     "t6h": 3,
#     "t23h": 5,
#     "t47h": 6
# }


RELANCE_DELAYS_B = {
    "t30": 5,       # minutes
    "t6h": 6*60,
    "t23h": 20*60,
    "t47h": 40*60,
}

PRICE_CURRENT = int(os.getenv("PRICE_CURRENT", "5000"))
PRICE_AFTER   = int(os.getenv("PRICE_AFTER",   "15000"))

# Envoi des deux canaux à chaque étape
WHATSAPP_AND_EMAIL_ALWAYS = True
