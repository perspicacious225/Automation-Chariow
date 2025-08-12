from flask import Flask, request, jsonify
import json, os

from webhook_app.utils.database import Database, sqlite3, ensure_schema_for_webhooks, save_webhook_raw
from webhook_app.config import Config
from webhook_app.services.notifier import Notifier

from webhook_app.models.sale import Sale
# from .services.email import EmailService

import logging

from flask_cors import CORS 

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/webhook": {"origins": "*"}})
    notifier = Notifier()
    # Configuration
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # google_sheets = GoogleSheetsService()
    db = Database()
    ensure_schema_for_webhooks()

    @app.route("/", methods=["GET"])
    def home():
        return jsonify({"status": "running", "message": "Webhook handler is operational"}), 200

    @app.route("/webhook", methods=["GET", "POST", "OPTIONS"])  # Ajout OPTIONS pour CORS
    def handle_webhook():
        # Debug complet
        # app.logger.info(f"\n=== Headers ===\n{request.headers}")
        # app.logger.info(f"\n=== Raw Data ===\n{request.get_data(as_text=True)}")

        app.logger.info(f"DB_PATH = {Config.DB_PATH}")
        app.logger.info(f"WEBHOOK_DUMP_PATH = {Config.WEBHOOK_DUMP_PATH}")
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        
        if request.method == "GET":
            return jsonify({"error": "Use POST for webhooks"}), 405
            
        try:
            # Validation basique
            if not request.is_json:
                app.logger.error("Content-Type must be application/json")
                return jsonify({"error": "Content-Type must be application/json"}), 400
                
            payload = request.get_json(force=True)
            # app.logger.info(f"\n=== Parsed JSON ===\n{payload}")
            sale = Sale.from_webhook(payload)
            

            # 1) ARCHIVE en base (historique complet, idempotent)
            try:
                event_pk = save_webhook_raw(payload, source="green_api")
                app.logger.info(f"Webhook archivé (id={event_pk})")
            except Exception as e:
                app.logger.error(f"Archivage webhook échoué: {e}", exc_info=True)

            # 2) DUMP du DERNIER webhook 
            pretty = json.dumps(payload, indent=2, ensure_ascii=False)
            dump_path = Config.WEBHOOK_DUMP_PATH  # configuré dans config.py
            try:
                os.makedirs(os.path.dirname(dump_path) or ".", exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(pretty)
                app.logger.info(f"💾 Payload sauvegardé dans {dump_path}")
            except Exception as e:
                app.logger.error(f"❌ Impossible de sauvegarder payload : {e}")

            
            if db.has_processed(sale.id):  # Utilise l'instance globale
                app.logger.info(f"Already Processed: {sale.id}:")
                return jsonify({"status": "already_processed"}), 200
            
            # Google Sheet
            # google_sheets.append_sale(sale)
            # Gestion des différents statuts
            if sale.status == "abandoned":
                notifier.handle_abandoned(sale)
            elif sale.status == "failed":
                notifier.handle_failed(sale)
            elif sale.status == "completed":
                notifier.handle_success(sale)
            
            db.mark_processed(sale.id, "success")  # Statut explicite
            return jsonify({"status": "success"}), 200
            
        except sqlite3.Error as e:
            logger.error(f"Erreur base de données: {str(e)}", exc_info=True)
            return jsonify({"error": "Database error"}), 500
        except json.JSONDecodeError as e:
            app.logger.error(f"Invalid JSON: {str(e)}")
            return jsonify({"error": "Invalid JSON format"}), 400
        except Exception as e:
            app.logger.error(f"Server error: {str(e)}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    # def _authenticate(request):
    #     return request.headers.get("X-Secret-Token") == Config.WEBHOOK_SECRET

    return app

# --- Point d'entrée pour Render et exécution locale ---
app = create_app()

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
