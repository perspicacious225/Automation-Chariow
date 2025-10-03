# webhook_app/app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, logging
from webhook_app.admin.dashboard import dashboard_bp
from webhook_app.database_pg import (
    Database,
    init_pool, close_pool, ensure_all_schemas,
    save_webhook_raw, upsert_fact_from_webhook, rfm_recompute,
)
from webhook_app.drive_service import grant_access_for_sale

from flask_login import LoginManager
from webhook_app.utils.auth_pg import ensure_users_schema, get_user_by_id


from webhook_app.config import Config
from webhook_app.services.notifier import Notifier  
from webhook_app.services.scheduler import start_scheduler
from webhook_app.services.mailer import EmailService
from webhook_app.models.sale import Sale

def create_app():
    # Logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # Flask app
    app = Flask(__name__)
    CORS(app, resources={r"/webhook": {"origins": "*"}})
    app.config.from_object(Config)
    assert app.config.get("SECRET_KEY"), "SECRET_KEY requis pour les sessions !"

    ensure_users_schema()
     # Flask-Login
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"      # redirige si non connecté
    login_manager.session_protection = "strong"
    login_manager.init_app(app)
    @login_manager.user_loader
    def load_user(user_id: str):
        return get_user_by_id(user_id)

    from webhook_app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    # Services
    notifier = Notifier()
    email_service = EmailService()
    # Pool PG + schémas
    init_pool()
    import atexit
    atexit.register(close_pool)
    ensure_all_schemas()
    db = Database()

    # ----- Schemas déjà assurés via ensure_all_schemas() -----


    # ----- Scheduler (un seul démarrage) -----
    def _send(sale, template_type):
        # envoie email+whatsapp selon la stratégie
        return notifier._send_notification(sale, template_type)
    
    if not getattr(app, "_scheduler_started", False):
        start_scheduler(_send)
        app._scheduler_started = True



    # ----- Routes -----
    @app.route("/", methods=["GET"])
    def home():
        return jsonify({"status": "running", "message": "Webhook handler is operational"}), 200

    @app.get("/test-email")
    def test_email():
        to = request.args.get("to") or os.getenv("TEST_EMAIL_TO") or os.getenv("SENDER_EMAIL") or os.getenv("SMTP_USER")
        if not to:
            return jsonify({"ok": False, "error": "Spécifie ?to=... ou définis TEST_EMAIL_TO"}), 400

        subject = "Test SMTP + IMAP (copie Envoyés) ✅"
        html = "<h3>Bonjour 👋</h3><p>Test d’envoi via SMTP + copie IMAP dans <b>Envoyés</b>.</p>"
        text = "Bonjour, test d’envoi via SMTP + copie IMAP dans Envoyés."
        ok = email_service.send_email(recipient=to, subject=subject, html_body=html, plain_fallback=text)
        return jsonify({"ok": ok, "to": to})

    import imaplib

    @app.get("/debug-imap")
    def debug_imap():
        try:
            host = os.getenv("IMAP_HOST")
            port = int(os.getenv("IMAP_PORT", "993"))
            user = os.getenv("IMAP_USER") or os.getenv("SMTP_USER")
            pw   = os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS")

            with imaplib.IMAP4_SSL(host, port) as imap:
                imap.login(user, pw)
                typ, data = imap.list()
                rows = []
                if typ == "OK":
                    for line in data or []:
                        rows.append(line.decode("utf-8", "ignore"))
                imap.logout()
            return jsonify({"ok": True, "folders": rows})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/webhook", methods=["GET", "POST", "OPTIONS"])
    def handle_webhook():
        app.logger.info(f"DB_PATH = {Config.DB_PATH}")
        app.logger.info(f"WEBHOOK_DUMP_PATH = {Config.WEBHOOK_DUMP_PATH}")

        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        if request.method == "GET":
            return jsonify({"error": "Use POST for webhooks"}), 405
        

        try:
            if not request.is_json:
                app.logger.error("Content-Type must be application/json")
                return jsonify({"error": "Content-Type must be application/json"}), 400

            payload = request.get_json(force=True)
            sale = Sale.from_webhook(payload)

            # 1) Archive du webhook (idempotent)
            try:
                event_pk = save_webhook_raw(payload, source="green_api")
                app.logger.info(f"Webhook archivé (id={event_pk})")
            except Exception as e:
                app.logger.error(f"Archivage webhook échoué: {e}", exc_info=True)
            try:
                upsert_fact_from_webhook(payload)   # alimente fact_sales + dims
            except Exception as e:
                app.logger.error(f"ETL fact_sales failed: {e}", exc_info=True)

            # 2) Dump last webhook pour debug
            pretty = json.dumps(payload, indent=2, ensure_ascii=False)
            dump_path = Config.WEBHOOK_DUMP_PATH
            try:
                os.makedirs(os.path.dirname(dump_path) or ".", exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(pretty)
                app.logger.info(f"💾 Payload sauvegardé dans {dump_path}")
            except Exception as e:
                app.logger.error(f"❌ Impossible de sauvegarder payload : {e}")

            # 3) Idempotence par sale_id
            if db.has_processed(sale.id):
                app.logger.info(f"Already Processed: {sale.id}")
                return jsonify({"status": "already_processed"}), 200

            # 4) Router selon statut
            if sale.status == "abandoned":
                notifier.handle_abandoned(sale)
            elif sale.status == "failed":
                notifier.handle_failed(sale)
            elif sale.status == "completed":

                try:
                    app.logger.info(f"Vente réussie pour {sale.product_id}. Tentative de partage sur Drive.")
                    grant_access_for_sale(sale) 
                except Exception as e:
                    # On log l'erreur mais on ne bloque pas le reste du processus
                    app.logger.error(f"Une erreur est survenue lors du partage sur Drive: {e}", exc_info=True)

                notifier.handle_success(sale)



            try:
                rfm_recompute()
            except Exception as e:
                app.logger.warning(f"RFM recompute skipped: {e}")

            db.mark_processed(sale.id, "success")
            return jsonify({"status": "success"}), 200

        except Exception as e:
            logger.error(f"Erreur base de données: {str(e)}", exc_info=True)
            return jsonify({"error": "Database error"}), 500
        except json.JSONDecodeError as e:
            app.logger.error(f"Invalid JSON: {str(e)}")
            return jsonify({"error": "Invalid JSON format"}), 400
        except Exception as e:
            app.logger.error(f"Server error: {str(e)}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500
        

    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")

    return app


# Point d'entrée
app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), use_reloader=False)


