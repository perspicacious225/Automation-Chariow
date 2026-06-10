# webhook_app/app.py
from flask import Flask, request, jsonify, redirect, url_for
from flask_cors import CORS
import json, os, logging, hmac, hashlib   


from webhook_app.admin.dashboard import dashboard_bp, dashboard_view
from webhook_app.admin.dashboard_v2 import dashboard_v2_bp

from webhook_app.database_pg import (
    Database,
    init_pool, close_pool, ensure_all_schemas,
    save_webhook_raw, upsert_fact_from_webhook, rfm_recompute,
)
from webhook_app.database_conv import ConvDatabase
from webhook_app.drive_service import grant_access_for_sale

from flask_login import LoginManager
from webhook_app.utils.auth_pg import ensure_users_schema, get_user_by_id

from webhook_app.config import Config
from webhook_app.services.notifier import Notifier
from webhook_app.services.scheduler import start_scheduler
from webhook_app.services.mailer import EmailService
from webhook_app.models.sale import Sale

# conversational services
from webhook_app.services.whatsapp_inbound import inbound_bp
from webhook_app.conversation.manager import ConversationManager
from webhook_app.services.whatsapp import WhatsAppService


from webhook_app import config

config.configure_logging(env=Config.APP_ENV)



def create_app():
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, log_level, logging.INFO))
    logger = logging.getLogger(__name__)

    app = Flask(__name__)

    CORS(app, resources={r"/webhook": {"origins": "*"}})
    app.config.from_object(Config)
    assert app.config.get("SECRET_KEY"), "SECRET_KEY requis pour les sessions !"

    ensure_users_schema()
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.session_protection = "strong"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        return get_user_by_id(user_id)

    from webhook_app.auth.routes import auth_bp
    app.register_blueprint(auth_bp)

    notifier = Notifier()
    email_service = EmailService()
    init_pool()
    import atexit
    atexit.register(close_pool)
    ensure_all_schemas()
    db = Database()
    ConvDatabase()

    def _send(sale, template_type):
        return notifier._send_notification(sale, template_type)

    if not getattr(app, "_scheduler_started", False):
        scheduler_thread = start_scheduler(_send, email_service=notifier.email_service)
        app._scheduler_started = True

        # ── Watchdog : surveille le scheduler toutes les 60s ──────────────
        import threading as _threading
        from webhook_app.services.whatsapp import WhatsAppService as _WA

        _wa_admin = _WA()
        _admin_phone = os.getenv("ADMIN_PHONE_NUMBER")

        def _watchdog():
            nonlocal scheduler_thread
            while True:
                _threading.Event().wait(60)  # attend 60s
                if not scheduler_thread.is_alive():
                    logger.critical("[WATCHDOG] Thread scheduler mort — redémarrage.")
                    try:
                        if _admin_phone:
                            _wa_admin.send_message(
                                phone=_admin_phone,
                                message=(
                                    "🚨 CHARIOW — Scheduler de relances arrêté.\n"
                                    "Redémarrage automatique en cours.\n"
                                    "Vérifier les logs si cela se répète."
                                )
                            )
                    except Exception:
                        logger.exception("[WATCHDOG] Alerte WhatsApp admin échouée.")
                    try:
                        scheduler_thread = start_scheduler(
                            _send, email_service=notifier.email_service
                        )
                        logger.info("[WATCHDOG] Scheduler redémarré avec succès.")
                        if _admin_phone:
                            _wa_admin.send_message(
                                phone=_admin_phone,
                                message="✅ CHARIOW — Scheduler redémarré avec succès."
                            )
                    except Exception:
                        logger.exception("[WATCHDOG] Échec redémarrage scheduler.")

        _wt = _threading.Thread(target=_watchdog, name="scheduler-watchdog", daemon=True)
        _wt.start()
        logger.info("[WATCHDOG] Démarré — surveille le scheduler toutes les 60s.")
        # ──────────────────────────────────────────────────────────────────

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.route("/", methods=["GET"])
    def home():
        # return jsonify({"status": "running", "message": "Webhook handler is operational"}), 200
        
        return dashboard_view()


    @app.route("/webhook", methods=["GET", "POST", "OPTIONS"])
    def handle_webhook():

        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        if request.method == "GET":
            return jsonify({"error": "Use POST for webhooks"}), 405

        # ── CORRIGÉ 1 : Vérification HMAC ─────────────────────────────────────
        secret = Config.WEBHOOK_SECRET
        if secret:
            raw_body = request.get_data()
            sig_received = request.headers.get("X-Webhook-Secret", "")
            expected = hmac.new(
                secret.encode(),
                raw_body,
                hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, sig_received):
                app.logger.warning("Webhook rejeté : signature HMAC invalide")
                return jsonify({"error": "unauthorized"}), 401
        # ──────────────────────────────────────────────────────────────────────

        # ── CORRIGÉ 2 : Except handlers dans le bon ordre ─────────────────────
        try:
            if not request.is_json:
                app.logger.error("Content-Type must be application/json")
                return jsonify({"error": "Content-Type must be application/json"}), 400

            payload = request.get_json(force=True)

        except json.JSONDecodeError as e:                          # ← spécifique en premier
            app.logger.error(f"Invalid JSON: {str(e)}")
            return jsonify({"error": "Invalid JSON format"}), 400
        except Exception as e:
            app.logger.error(f"Erreur parsing: {str(e)}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500
        # ──────────────────────────────────────────────────────────────────────

        # ── CORRIGÉ 3 : Transaction atomique ──────────────────────────────────
        try:
            sale = Sale.from_webhook(payload)

            # Archive brute (non critique, hors transaction)
            try:
                event_pk = save_webhook_raw(payload, source="green_api")
                app.logger.info(f"Webhook archivé (id={event_pk})")
            except Exception as e:
                app.logger.error(f"Archivage webhook échoué: {e}", exc_info=True)

            # Dump debug (non critique, hors transaction)
            try:
                pretty = json.dumps(payload, indent=2, ensure_ascii=False)
                dump_path = Config.WEBHOOK_DUMP_PATH
                os.makedirs(os.path.dirname(dump_path) or ".", exist_ok=True)
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(pretty)
            except Exception as e:
                app.logger.error(f"Impossible de sauvegarder payload : {e}")

            # Idempotence
            if db.has_processed(sale.id):
                app.logger.info(f"Already Processed: {sale.id}")
                return jsonify({"status": "already_processed"}), 200

            # ETL fact_sales (hors transaction — acceptable, idempotent via UPSERT)
            try:
                upsert_fact_from_webhook(payload)
            except Exception as e:
                app.logger.error(f"ETL fact_sales failed: {e}", exc_info=True)

            # Routing métier
            if sale.status == "abandoned":
                notifier.handle_abandoned(sale)
            elif sale.status == "failed":
                notifier.handle_failed(sale)
            elif sale.status == "completed":
                try:
                    app.logger.info(f"Vente réussie pour {sale.product_id}. Tentative partage Drive.")
                    grant_access_for_sale(sale)
                except Exception as e:
                    app.logger.error(f"Erreur partage Drive: {e}", exc_info=True)
                notifier.handle_success(sale)

            # ksynchronisation contexte conversationnel ────────
            try:
                _phone_raw = sale.customer_phone or ""
                if _phone_raw:
                    _phone_norm = WhatsAppService.normalize_phone(_phone_raw)
                    if _phone_norm:
                        ConversationManager().on_payment_event(
                            phone=_phone_norm,
                            country=sale.customer_country or "CI",
                            event_type=payload.get("event", ""),
                            sale_id=sale.id,
                            product_id=sale.product_id,
                            contact_key=sale.customer_email or _phone_norm,
                        )
            except Exception as e:
                app.logger.warning(f"Sync contexte conv échouée (non bloquant): {e}")
            # ──────────────────────────────────────────────────────────────

            # RFM (non critique, hors transaction)
            try:
                rfm_recompute()
            except Exception as e:
                app.logger.warning(f"RFM recompute skipped: {e}")

            # ← ATOMIQUE : mark_processed en dernier, si tout a réussi
            db.mark_processed(sale.id, "success")
            return jsonify({"status": "success"}), 200

        except Exception as e:
            logger.error(f"Erreur traitement webhook: {str(e)}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500
        # ──────────────────────────────────────────────────────────────────────

    with app.app_context():
        try:
            from webhook_app.database_v21 import init_default_prompts
            from webhook_app.llm.prompts import (
                BASE_SYSTEM_PROMPT,
               
                BASE_PROMPT_VENDOR_FR,
                
                BASE_PROMPT_SUPPORT_FR,
               
            )


            default_prompts = {
                "base": {
                    "label": "Prompt système de base",
                    "content": BASE_SYSTEM_PROMPT,
                }
            }


            # Prompts adaptatifs compressés
            default_prompts["base_vendor"] = {
                "label": "Prompt vendeur FR (compressé)",
                "content": BASE_PROMPT_VENDOR_FR,
            }
            
            default_prompts["base_support"] = {
                "label": "Prompt support FR (compressé)",
                "content": BASE_PROMPT_SUPPORT_FR,
            }
 

            init_default_prompts(default_prompts)

        except Exception as e:
            logger.warning("Init prompts DB échouée : %s", e)
    with app.app_context():
        try:
            from webhook_app.database_conv import count_chunks_by_product
            from webhook_app.rag.ingestion import ingest_all_from_db

            existing = count_chunks_by_product()
            if not existing:
                logger.info("KB vide — réingestion depuis DB au démarrage")
                ingest_all_from_db()
            else:
                total = sum(r.get("chunk_count", 0) for r in existing)
                logger.info("KB déjà présente — %d chunks sur %d produits, pas de réingestion", 
                            total, len(existing))
        except Exception as e:
            logger.warning("Réingestion KB au démarrage échouée : %s", e)

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(dashboard_v2_bp)
    app.register_blueprint(inbound_bp)

    return app


# Point d'entrée
app = create_app()
app.logger.disabled = True
log = logging.getLogger("werkzeug")
log.disabled = True
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5001)), use_reloader=True)