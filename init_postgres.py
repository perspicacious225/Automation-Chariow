import os
import logging
from webhook_app.database_pg import init_pool, close_pool, ensure_all_schemas

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    try:
        init_pool()
        ensure_all_schemas()
        print("Schemas PostgreSQL créés/assurés avec succès.")
    finally:
        close_pool()
