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

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


GMAIL_CLIENT_SECRET_PATH = Path(os.getenv("GMAIL_CLIENT_SECRET_PATH", "/etc/secrets/credentials_gmail.json"))
GMAIL_TOKEN_PATH         = Path(os.getenv("GMAIL_TOKEN_PATH", "/opt/data/gmail_token.json"))
GMAIL_TOKEN_JSON         = os.getenv("GMAIL_TOKEN_JSON")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_NAME  = os.getenv("SENDER_NAME", "Digitech Hub")

APP_ENV = os.getenv("APP_ENV", os.getenv("FLASK_ENV", "production")).lower()


class EmailService:
    def __init__(self):
        if not SENDER_EMAIL:
            raise RuntimeError("SENDER_EMAIL manquant (expéditeur).")
        self.creds = None
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Charge/rafraîchit le token; en local permet l’obtention interactive."""
        # Assurer le dossier du token
        try:
            token_dir = os.path.dirname(GMAIL_TOKEN_PATH) or "."
            os.makedirs(token_dir, exist_ok=True)
        except Exception:
            logger.exception("Création du dossier token échouée.")

        lock = FileLock(str(GMAIL_TOKEN_PATH) + ".lock")
        with lock:
            # 0) Seed initial depuis l'env (utile sur Render au 1er boot)
            try:
                if GMAIL_TOKEN_JSON and not os.path.exists(GMAIL_TOKEN_PATH):
                    json.loads(GMAIL_TOKEN_JSON)  # sanity-check
                    with open(GMAIL_TOKEN_PATH, "w") as f:
                        f.write(GMAIL_TOKEN_JSON)
                    logger.info("GMAIL_TOKEN_JSON seed -> %s", GMAIL_TOKEN_PATH)
            except Exception:
                logger.exception("Seed du token Gmail échoué (GMAIL_TOKEN_JSON).")

            # 1) Charger le token s’il existe
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

            # 2) Rafraîchir ou créer
            if not self.creds or not self.creds.valid:
                if self.creds and self.creds.expired and getattr(self.creds, "refresh_token", None):
                    # Token expiré mais rafraîchissable
                    try:
                        self.creds.refresh(Request())
                        with open(GMAIL_TOKEN_PATH, "w") as f:
                            f.write(self.creds.to_json())
                        logger.info("Token Gmail rafraîchi et écrit dans %s", GMAIL_TOKEN_PATH)
                    except gauth_exceptions.RefreshError as e:
                        # Cas typique: invalid_grant (refresh_token expiré/révoqué)
                        if "invalid_grant" in str(e).lower():
                            if GMAIL_TOKEN_JSON:
                                logger.warning("RefreshError invalid_grant → reseed depuis GMAIL_TOKEN_JSON et retry.")
                                with open(GMAIL_TOKEN_PATH, "w") as f:
                                    f.write(GMAIL_TOKEN_JSON)
                                # recharge et refresh pour obtenir un access_token frais
                                self.creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)
                                self.creds.refresh(Request())
                                with open(GMAIL_TOKEN_PATH, "w") as f:
                                    f.write(self.creds.to_json())
                                logger.info("Reseed+refresh OK → nouveau token écrit dans %s", GMAIL_TOKEN_PATH)
                            else:
                                raise RuntimeError(
                                    "RefreshError invalid_grant et aucun GMAIL_TOKEN_JSON fourni. "
                                    "Régénère un token.json en local (access_type=offline, prompt=consent) "
                                    "et colle-le dans GMAIL_TOKEN_JSON."
                                ) from e
                        else:
                            raise
                    except Exception:
                        logger.exception("Impossible d'écrire le token rafraîchi.")
                else:
                    # Pas de creds valides
                    if APP_ENV in {"prod", "production"}:
                        # En PROD: pas d’interactif → tenter un seed forcé si dispo, sinon erreur claire
                        if GMAIL_TOKEN_JSON:
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
                                    "Rebootstrap en local avec access_type=offline + prompt=consent."
                                )
                        else:
                            raise RuntimeError(
                                "Token Gmail absent/invalid en production. "
                                "Fournis GMAIL_TOKEN_JSON ou génère un token.json en local."
                            )
                    else:
                        # En LOCAL: création via flux OAuth
                        if not os.path.exists(GMAIL_CLIENT_SECRET_PATH):
                            raise RuntimeError(f"Client secret introuvable: {GMAIL_CLIENT_SECRET_PATH}")

                        flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CLIENT_SECRET_PATH, SCOPES)
                        try:
                            self.creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
                        except Exception:
                            # Fallback sans navigateur
                            auth_url, _ = flow.authorization_url(
                                prompt="consent",
                                access_type="offline",
                                include_granted_scopes="true",
                            )
                            print("\nOuvrez ce lien dans votre navigateur, autorisez l’accès puis copiez le code :")
                            print(auth_url)
                            code = input("Code d'autorisation: ").strip()
                            flow.fetch_token(code=code)
                            self.creds = flow.credentials

                        with open(GMAIL_TOKEN_PATH, "w") as f:
                            f.write(self.creds.to_json())
                        logger.info("Token Gmail créé et écrit dans %s", GMAIL_TOKEN_PATH)

        # 3) Construire le service (hors verrou)
        self.service = build("gmail", "v1", credentials=self.creds, cache_discovery=False)


    def send_email(self, recipient: str, subject: str, html_body: str, plain_fallback: str = "") -> bool:
        """Envoi d'email via Gmail API."""
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
            msg["To"] = recipient

            # version texte
            text = plain_fallback or (html_body or "").replace("<br>", "\n").replace("<br/>", "\n")
            msg.set_content(text or "")

            # version HTML
            if html_body:
                msg.add_alternative(html_body, subtype="html")

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
            self.service.users().messages().send(userId="me", body={"raw": raw}).execute()
            logger.info("Email Gmail API envoyé à %s", recipient)
            return True
        except Exception as e:
            logger.error("Erreur d'envoi Gmail API: %s", e, exc_info=True)
            return False
