import os
import base64
import logging
from email.message import EmailMessage
from email.utils import formataddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Chemins/identité configurables via env (avec valeurs par défaut Render-friendly)
GMAIL_CLIENT_SECRET_PATH = os.getenv("GMAIL_CLIENT_SECRET_PATH", "/opt/data/client_secret.json")
GMAIL_TOKEN_PATH         = os.getenv("GMAIL_TOKEN_PATH", "/opt/data/gmail_token.json")

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
        # 1) Charger le token s’il existe
        if os.path.exists(GMAIL_TOKEN_PATH):
            self.creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, SCOPES)

        # 2) Rafraîchir ou créer
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # Token expiré mais rafraîchissable
                self.creds.refresh(Request())
            else:
                # En PROD: pas d’interactif → il faut un token déjà présent
                if APP_ENV in {"prod", "production"}:
                    raise RuntimeError(
                        "Token Gmail absent/invalid en production. "
                        "Crée-le en local puis place-le dans GMAIL_TOKEN_PATH."
                    )

                # En LOCAL: créer un nouveau token
                if not os.path.exists(GMAIL_CLIENT_SECRET_PATH):
                    raise RuntimeError(f"Client secret introuvable: {GMAIL_CLIENT_SECRET_PATH}")

                flow = InstalledAppFlow.from_client_secrets_file(GMAIL_CLIENT_SECRET_PATH, SCOPES)

                # Essai 1: serveur local (ouvre le navigateur)
                try:
                    self.creds = flow.run_local_server(
                    port=0,
                    access_type="offline",
                    prompt="consent"
                )
                except Exception:
                    # Essai 2: méthode manuelle (console)
                    auth_url, _ = flow.authorization_url(
                        prompt='consent',
                        access_type='offline',
                        include_granted_scopes='true'
                    )
                    print("\nOuvrez ce lien dans votre navigateur, autorisez l’accès puis copiez le code donné :")
                    print(auth_url)
                    code = input("Collez ici le code d'autorisation puis Entrée: ").strip()
                    flow.fetch_token(code=code)
                    self.creds = flow.credentials

                # Sauvegarder le token pour réutilisation
                with open(GMAIL_TOKEN_PATH, "w") as f:
                    f.write(self.creds.to_json())

        # 3) Construire le service
        self.service = build("gmail", "v1", credentials=self.creds, cache_discovery=False)
    def send_email(self, recipient: str, subject: str, html_body: str, plain_fallback: str = "") -> bool:
        """Envoi d'email via Gmail API."""
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = formataddr((SENDER_NAME, SENDER_EMAIL))
            msg["To"] = recipient

            # version texte 
            text = plain_fallback or (html_body or "").replace("<br>", "\n").replace("<br/>","\n")
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
