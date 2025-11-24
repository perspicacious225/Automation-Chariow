# webhook_app/app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import json, os, logging
from webhook_app.admin.dashboard import dashboard_bp
from webhook_app.database_pg import (
    Database,
    init_pool, close_pool, ensure_all_schemas,
    save_webhook_raw, upsert_fact_from_webhook, rfm_recompute,_check_pool_age
)
from webhook_app.drive_service import grant_access_for_sale

from flask_login import LoginManager
from webhook_app.utils.auth_pg import ensure_users_schema, get_user_by_id


from celery import Celery, chain
from celery.signals import (
    worker_process_init,
    worker_process_shutdown,
    task_prerun,
    task_postrun
)


from webhook_app.config import Config
from webhook_app.services.notifier import Notifier  
from webhook_app.services.scheduler import start_scheduler
from webhook_app.services.mailer import EmailService
from webhook_app.models.sale import Sale
from webhook_app.services.campaign_worker import start_campaign_worker

logger = logging.getLogger(__name__)
def make_celery(app: Flask) -> Celery:
    """Crée et configure l'instance Celery avec gestion des connexions DB."""
    
    broker_url = app.config.get('CELERY_BROKER_URL', 'redis://172.23.232.56:6379/0')
    result_backend_url = app.config.get('CELERY_RESULT_BACKEND', 'redis://172.23.232.56:6379/0')

    celery = Celery(
        app.import_name,
        backend=result_backend_url,
        broker=broker_url
    )
    
    
    # ✅ Configuration additionnelle pour la stabilité
    celery.conf.update(
        # Préfetch : nombre de tâches qu'un worker peut prendre à l'avance
        worker_prefetch_multiplier=1,  
        
        # Timeout des tâches
        task_soft_time_limit=600,     
        task_time_limit=660,           
        
        # Reconnexion automatique
        broker_connection_retry_on_startup=True,
        broker_connection_retry=True,
        broker_connection_max_retries=10,
        
        # Serialization
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        
        # Acks après traitement (plus sûr)
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        
        # Pool de workers
        worker_pool='prefork',          # ou 'gevent' si vous utilisez gevent
        worker_max_tasks_per_child=1000, # Recycle worker après 1000 tâches
    )
    
    # ✅ Task de base avec contexte Flask
    class ContextTask(celery.Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    
    # ✅ IMPORTANT : Configuration des hooks de cycle de vie pour PostgreSQL
    @worker_process_init.connect
    def init_worker_db_pool(**kwargs):
        """
        Initialise un pool de connexions PostgreSQL dédié pour chaque worker process.
        Exécuté une fois au démarrage de chaque worker.
        """
        logger.info(
            "🚀 Initialisation du worker process %s - Création du pool PostgreSQL", 
            os.getpid()
        )
        with app.app_context():
            init_pool()
    
    @worker_process_shutdown.connect
    def shutdown_worker_db_pool(**kwargs):
        """
        Ferme proprement le pool de connexions lors de l'arrêt du worker.
        """
        logger.info(
            "🛑 Arrêt du worker process %s - Fermeture du pool PostgreSQL", 
            os.getpid()
        )
        with app.app_context():
            close_pool()
    
    @task_prerun.connect
    def before_task_check_pool(**kwargs):
        """
        Avant chaque tâche, vérifie l'âge du pool et le recycle si nécessaire.
        Équivalent de pool_recycle de SQLAlchemy.
        """
        task = kwargs.get('task')
        task_id = kwargs.get('task_id')
        
        logger.debug(
            "📋 Début tâche %s [%s] - PID %s", 
            task.name if task else 'unknown',
            task_id[:8] if task_id else 'unknown',
            os.getpid()
        )
        
        # Vérification et recyclage du pool si trop ancien
        _check_pool_age()
    
    @task_postrun.connect
    def after_task_cleanup(**kwargs):
        """
        Après chaque tâche, log et nettoyage optionnel.
        """
        task = kwargs.get('task')
        task_id = kwargs.get('task_id')
        state = kwargs.get('state', 'UNKNOWN')
        
        logger.debug(
            "✅ Fin tâche %s [%s] - État: %s - PID %s", 
            task.name if task else 'unknown',
            task_id[:8] if task_id else 'unknown',
            state,
            os.getpid()
        )
        
        # Si vous voulez forcer un cleanup de connexions après chaque tâche :
        # (Optionnel, peut réduire les performances mais augmente la stabilité)
        # with app.app_context():
        #     _cleanup_stale_connections()
    
    logger.info(
        "✅ Celery configuré : Broker=%s, Backend=%s",
        broker_url,
        result_backend_url
    )
    
    return celery


def create_app():
    """Crée et configure l'application Flask."""
    
    # Logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - PID:%(process)d - %(message)s'
    )
    logger = logging.getLogger(__name__)

    # Flask app
    app = Flask(__name__)
    CORS(app, resources={r"/webhook": {"origins": "*"}})
    app.config.from_object(Config)
    
    # Vérification SECRET_KEY
    assert app.config.get("SECRET_KEY"), "SECRET_KEY requis pour les sessions !"

    # ✅ Configuration Celery + Redis
    redis_url = os.getenv('REDIS_URL', 'redis://172.23.232.56:6379/0')
    
    app.config.from_mapping(
        broker_url=redis_url,
        result_backend=redis_url,
        
        # ✅ Configuration additionnelle pour Redis
        broker_transport_options={
            'visibility_timeout': 3600,  # 1 heure
            'fanout_prefix': True,
            'fanout_patterns': True,
        },
        result_backend_transport_options={
            'retry_policy': {
                'timeout': 5.0
            }
        },
    )
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


    # ----- Scheduler (un seul démarrage) -----
    def _send(sale, template_type):
        # envoie email+whatsapp selon la stratégie
        return notifier._send_notification(sale, template_type)
    
    if not getattr(app, "_workers_started", False):
        start_scheduler(_send)
        start_campaign_worker()
        app._workers_started = True



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

    # @app.get("/debug-imap")
    # def debug_imap():
    #     try:
    #         host = os.getenv("IMAP_HOST")
    #         port = int(os.getenv("IMAP_PORT", "993"))
    #         user = os.getenv("IMAP_USER") or os.getenv("SMTP_USER")
    #         pw   = os.getenv("IMAP_PASS") or os.getenv("SMTP_PASS")

    #         with imaplib.IMAP4_SSL(host, port) as imap:
    #             imap.login(user, pw)
    #             typ, data = imap.list()
    #             rows = []
    #             if typ == "OK":
    #                 for line in data or []:
    #                     rows.append(line.decode("utf-8", "ignore"))
    #             imap.logout()
    #         return jsonify({"ok": True, "folders": rows})
    #     except Exception as e:
    #         return jsonify({"ok": False, "error": str(e)}), 500
        


    from webhook_app.utils.tasks import generate_video_script, generate_audio_task

    @app.route('/generate_script/<int:brief_id>') 
    def generate_vid_script(brief_id):
       
        task_chain_object = chain(generate_video_script.s(brief_id), generate_audio_task.s()) # type: ignore
        
        # On LANCE la chaîne UNE SEULE FOIS et on récupère le résultat
        task_result = task_chain_object.apply_async()
        

        # task_result est un objet AsyncResult qui contient l'ID de la tâche
        if task_result:
            
            logger.info(f"Tâche envoyée avec l'ID : {task_result.id}")
            
            task_result_id = task_result.id
        return jsonify({
        "message": "Flux de génération vidéo lancé !",
        "group_task_id": task_result_id, # C'est l'ID du groupe de tâches (la chaîne)
        "brief_id": brief_id
    })
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
celery = make_celery(app)
__all__ = ['app', 'celery']


# from webhook_app.utils.tasks import generate_script_task
# @app.route('/trigger-task')
# def trigger():
#     generate_script_task.delay()
#     return jsonify({
#         "task": "Task triggered!",
#         "statut_code": 200
#                      })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)

