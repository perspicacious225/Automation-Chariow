import os
import json
import base64
import logging
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from filelock import FileLock
from google.auth import exceptions as gauth_exceptions
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Assurez-vous que ce chemin d'importation est correct pour votre projet
from webhook_app.services.whatsapp import WhatsAppService

logger = logging.getLogger(__name__)

# --- Configuration ---
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

GMAIL_CLIENT_SECRET_PATH = Path(os.getenv("GMAIL_CLIENT_SECRET_PATH", "/etc/secrets/credentials_gmail.json"))
GMAIL_TOKEN_PATH         = Path(os.getenv("GMAIL_TOKEN_PATH", "/opt/data/gmail_token.json"))
GMAIL_TOKEN_JSON         = os.getenv("GMAIL_TOKEN_JSON")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_NAME  = os.getenv("SENDER_NAME", "Digitech Hub")

APP_ENV = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "production")).lower()

# --- Instanciation des services ---
whatsapp_service = WhatsAppService()


class EmailService:
    def __init__(self):
        if not SENDER_EMAIL:
            raise RuntimeError("SENDER_EMAIL manquant (expéditeur).")
        self.creds = None
        self.service = None
        self.available = False          # ← AJOUT : False jusqu'à auth réussie
        try:
            self._authenticate()
            self.available = True
            logger.info("Gmail API initialisée avec succès.")
        except Exception as e:
            logger.error(
                "Gmail API indisponible au démarrage — mode dégradé activé. "
                "Les emails seront mis en attente. Erreur: %s", e
            )
            # Alerte WhatsApp admin
            try:
                admin_phone = os.getenv("ADMIN_PHONE_NUMBER")
                if admin_phone:
                    whatsapp_service.send_message(
                        phone=admin_phone,
                        message=(
                            "⚠️ CHARIOW — Gmail API indisponible au démarrage.\n"
                            "Les emails sont mis en attente.\n"
                            "WhatsApp continue normalement."
                        )
                    )
            except Exception:
                logger.exception("Alerte WhatsApp admin échouée.")

    def is_available(self) -> bool:
        """Teste si Gmail API est opérationnelle (ping léger)."""
        if not self.service:
            return False
        try:
            self.service.users().getProfile(userId="me").execute()
            if not self.available:
                self.available = True
                logger.info("Gmail API revenue disponible.")
            return True
        except Exception:
            self.available = False
            return False

    def _authenticate(self):
        """Charge/rafraîchit le token; en local permet l’obtention interactive."""
        try:
            token_dir = os.path.dirname(GMAIL_TOKEN_PATH) or "."
            os.makedirs(token_dir, exist_ok=True)
        except Exception:
            logger.exception("Création du dossier token échouée.")

        lock = FileLock(str(GMAIL_TOKEN_PATH) + ".lock")
        with lock:
            # 1) Seed initial depuis l'env (utile sur Render au 1er boot)
            try:
                if GMAIL_TOKEN_JSON and not os.path.exists(GMAIL_TOKEN_PATH):
                    json.loads(GMAIL_TOKEN_JSON)  # Sanity-check
                    with open(GMAIL_TOKEN_PATH, "w") as f:
                        f.write(GMAIL_TOKEN_JSON)
                    logger.info("GMAIL_TOKEN_JSON seed -> %s", GMAIL_TOKEN_PATH)
            except Exception:
                logger.exception("Seed du token Gmail échoué (GMAIL_TOKEN_JSON).")

            # 2) Charger le token s’il existe
            if os.path.exists(GMAIL_TOKEN_PATH):
                try:
                    self.creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)
                except Exception:
                    logger.exception("Lecture du token invalide, suppression du fichier et reprise du flux.")
                    try:
                        os.remove(GMAIL_TOKEN_PATH)
                    except Exception:
                        pass
                    self.creds = None

            # 3) Rafraîchir ou créer si le token est invalide/absent
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and getattr(self.creds, "refresh_token", None):
                    # Cas 1 : Token expiré mais rafraîchissable
                    try:
                        self.creds.refresh(Request())
                        with open(GMAIL_TOKEN_PATH, "w") as f:
                            f.write(self.creds.to_json())
                        logger.info("Token Gmail rafraîchi et écrit dans %s", GMAIL_TOKEN_PATH)
                    except gauth_exceptions.RefreshError as e:
                        if "invalid_grant" in str(e).lower():
                            # Cas 2 : Le refresh_token est mort (erreur critique)
                            admin_phone = os.getenv("ADMIN_PHONE_NUMBER")

                            if GMAIL_TOKEN_JSON:
                                # Tentative d'auto-réparation
                                logger.warning("RefreshError invalid_grant → reseed depuis GMAIL_TOKEN_JSON et retry.")
                                with open(GMAIL_TOKEN_PATH, "w") as f:
                                    f.write(GMAIL_TOKEN_JSON)
                                self.creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)
                                self.creds.refresh(Request())
                                with open(GMAIL_TOKEN_PATH, "w") as f:
                                    f.write(self.creds.to_json())
                                logger.info("Reseed+refresh OK → nouveau token écrit dans %s", GMAIL_TOKEN_PATH)

                                # Envoi de la notification de succès de l'auto-réparation
                                if admin_phone:
                                    success_message = (
                                        "✅ INFO AUTOMATISATION CHARIOW ✅\n\n"
                                        "Le token de l'API Gmail a expiré (invalid_grant) mais a été **auto-réparé** avec succès en utilisant le token de secours.\n\n"
                                        "👍 L'envoi d'emails fonctionne à nouveau normalement. Aucune action n'est requise."
                                    )
                                    try:
                                        whatsapp_service.send_message(phone=admin_phone, message=success_message)
                                    except Exception as whatsapp_error:
                                        logger.error(f"Échec de l'envoi de la notification de succès WhatsApp : {whatsapp_error}")

                            else:
                                # Échec de l'auto-réparation, envoi de l'alerte critique
                                if admin_phone:
                                    failure_message = (
                                        "🚨 ALERTE CRITIQUE CHARIOW 🚨\n\n"
                                        "Le token de l'API Gmail est invalide (invalid_grant)\n"
                                        "ET la tentative d'auto-réparation a échoué (pas de token de secours).\n\n"
                                        "🔴 **L'application est ARRÊTÉE** et l'envoi d'emails ne fonctionne plus. 🔴\n\n"
                                        "Action manuelle urgente requise."
                                    )
                                    try:
                                        logger.critical("TOKEN GMAIL MORT ! Envoi de l'alerte critique WhatsApp à l'admin.")
                                        whatsapp_service.send_message(phone=admin_phone, message=failure_message)
                                    except Exception as whatsapp_error:
                                        logger.error(f"Échec de l'envoi de l'alerte critique WhatsApp : {whatsapp_error}")
                                
                                # Le programme s'arrête car il ne peut pas se réparer
                                raise RuntimeError(
                                    "RefreshError invalid_grant et aucun GMAIL_TOKEN_JSON fourni. "
                                    "Régénère un token.json en local et colle-le dans GMAIL_TOKEN_JSON."
                                ) from e
                        else:
                            # Gère les autres erreurs de rafraîchissement
                            raise
                    except Exception:
                        logger.exception("Impossible d'écrire le token rafraîchi.")
                else:
                    # Cas 3 : Pas de token valide, il faut en créer un
                    if APP_ENV in {"prod", "production"}:
                        # En production, on ne peut pas être interactif, on se base sur GMAIL_TOKEN_JSON
                        if not GMAIL_TOKEN_JSON:
                             raise RuntimeError(
                                "Token Gmail absent/invalide en production. "
                                "Fournir GMAIL_TOKEN_JSON ou générer un token.json en local."
                             )
                        # Logique de seed/refresh forcé pour le premier démarrage en production
                        with open(GMAIL_TOKEN_PATH, "w") as f:
                            f.write(GMAIL_TOKEN_JSON)
                        self.creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)
                        if getattr(self.creds, "refresh_token", None):
                            self.creds.refresh(Request())
                            with open(GMAIL_TOKEN_PATH, "w") as f:
                                f.write(self.creds.to_json())
                            logger.info("Seed+refresh en production OK → %s", GMAIL_TOKEN_PATH)
                        else:
                            raise RuntimeError(
                                "Token fourni via GMAIL_TOKEN_JSON sans refresh_token. "
                                "Re-générer en local avec access_type=offline + prompt=consent."
                            )
                    else:
                        # En local, on lance le flux de connexion interactif
                        if not os.path.exists(GMAIL_CLIENT_SECRET_PATH):
                            raise RuntimeError(f"Client secret introuvable: {GMAIL_CLIENT_SECRET_PATH}")
                        flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CLIENT_SECRET_PATH, SCOPES)
                        self.creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
                        with open(GMAIL_TOKEN_PATH, "w") as f:
                            f.write(self.creds.to_json())
                        logger.info("Token Gmail créé et écrit dans %s", GMAIL_TOKEN_PATH)

        # 4) Construire le service API (hors du verrou)
        self.service = build("gmail", "v1", credentials=self.creds, cache_discovery=False)


    def send_email(self, recipient: str, subject: str, html_body: str, plain_fallback: str = "") -> bool:
        """Envoi d'email via l'API Gmail."""
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
            msg["To"] = recipient

            text = plain_fallback or (html_body or "").replace("<br>", "\n").replace("<br/>", "\n")
            msg.set_content(text or "")

            if html_body:
                msg.add_alternative(html_body, subtype="html")

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            self.service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info("Email Gmail API envoyé à %s", recipient)
            return True
        except Exception as e:
            logger.error("Erreur d'envoi Gmail API: %s", e, exc_info=True)
            return False