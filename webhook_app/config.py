import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(__file__)

def _local_db():   return os.path.join(BASE_DIR, "data", "database.sqlite")
def _local_dump(): return os.path.join(BASE_DIR, "templates", "last_webhook.json")

# Détection Render (mais on Fallback si pas writable)
IS_RENDER = bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"))

default_db   = "/opt/data/database.sqlite" if IS_RENDER else _local_db()
default_dump = "/opt/data/last_webhook.json" if IS_RENDER else _local_dump()

DB_PATH = os.getenv("DB_PATH", default_db)
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
        # bascule automatique sur local
        d2 = os.path.dirname(fallback) or "."
        os.makedirs(d2, exist_ok=True)
        return fallback

DB_PATH = _ensure_writable(DB_PATH, _local_db())
WEBHOOK_DUMP_PATH = _ensure_writable(WEBHOOK_DUMP_PATH, _local_dump())

class Config:
    DB_PATH = DB_PATH
    WEBHOOK_DUMP_PATH = WEBHOOK_DUMP_PATH

    INSTANCE_ID = os.getenv("WHATSAPP_INSTANCE_ID")
    TOKEN       = os.getenv("WHATSAPP_TOKEN")
    API_URL     = f"https://api.green-api.com/waInstance{INSTANCE_ID}/sendMessage/{TOKEN}" if INSTANCE_ID and TOKEN else None

    CREDS_PATH  = os.getenv("GOOGLE_SHEETS_CREDENTIALS")
    SHEET_ID    = os.getenv("GOOGLE_SHEET_ID")
    WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
    SENDER_EMAIL   = os.getenv("SENDER_EMAIL")
