# services/email.py  (version SMTP)
import os
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import re
logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")                
SMTP_PASS = os.getenv("SMTP_PASS")                 
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)

def to_plain(text_html: str) -> str:
    """Supprime les balises HTML pour obtenir une version texte brut"""
    return re.sub(r"<[^>]+>", "", text_html).strip()
class EmailService:
    """
    Envoi d'email via SMTP (Gmail + App Password).

    """

    def __init__(self):
        if not (SMTP_USER and SMTP_PASS and SENDER_EMAIL):
            raise RuntimeError(
                "SMTP credentials missing. Set SMTP_USER, SMTP_PASS, SENDER_EMAIL"
            )

    def send_email(self, recipient: str, subject: str, html_body: str, plain_fallback: str = "") -> bool:
        try:
            # Construire le message multipart (texte brut + HTML)
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = SENDER_EMAIL
            msg["To"] = recipient

            if plain_fallback:
                msg.attach(MIMEText(plain_fallback, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            # Connexion SMTP sécurisée (STARTTLS)
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SENDER_EMAIL, [recipient], msg.as_string())

            logger.info(f"Email envoyé à {recipient}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error("Auth SMTP échouée (vérifie App Password/2FA): %s", e)
        except Exception as e:
            logger.error("Erreur SMTP: %s", e, exc_info=True)
        return False
