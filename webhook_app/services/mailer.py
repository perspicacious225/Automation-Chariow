import os
import re
import time
import logging
import smtplib
import ssl
import imaplib
from email.message import EmailMessage
from email.utils import formataddr

logger = logging.getLogger(__name__)

# ======================
# SMTP (on garde tes noms)
# ======================
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.hostinger.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")                 
SMTP_PASS = os.getenv("SMTP_PASS")                 
SENDER_EMAIL = os.getenv("SENDER_EMAIL", SMTP_USER)
SENDER_NAME = os.getenv("SENDER_NAME", "Digitech Hub")


SMTP_SSL = os.getenv("SMTP_SSL", "false").lower() == "true"

# ===============
# IMAP (pour Sent)
# ===============
IMAP_HOST = os.getenv("IMAP_HOST", "imap.hostinger.com")  
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
IMAP_USER = os.getenv("IMAP_USER", SMTP_USER)
IMAP_PASS = os.getenv("IMAP_PASS", SMTP_PASS)

IMAP_SENT_CANDIDATES = [
    "[Gmail]/Sent Mail",
    "Sent",
    "INBOX.Sent",
    "Envoyés",
    "Sent Items",
]

def to_plain(text_html: str) -> str:
    """Conversion simple HTML -> texte (fallback)."""
    if not text_html:
        return ""
    # retire scripts/styles
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text_html)
    # <br>/<p> -> retours ligne
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</\s*p\s*>", "\n\n", text)
    # retire le reste des balises
    text = re.sub(r"(?s)<.*?>", "", text)
    # entités simples
    text = (text.replace("&nbsp;", " ")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">"))
    # espaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()

def _build_from_header() -> str:
    """Construit 'From' = 'Nom <email@domaine>'"""
    if not (SENDER_EMAIL or SMTP_USER):
        return ""
    return formataddr((SENDER_NAME, SENDER_EMAIL or SMTP_USER)) # type: ignore

def _parse_imap_list_line(line: bytes):
    """
    Extrait (flags, name) depuis une ligne LIST IMAP.
    Exemples:
      b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Sent Mail"'
      b'(\\HasNoChildren \\UnMarked \\Sent) "." INBOX.Sent'
    Retourne: ({"\\hasnochildren","\\sent"}, "INBOX.Sent" / "[Gmail]/Sent Mail")
    """
    s = line.decode("utf-8", "ignore")
    flags = set()

    # Séparer flags vs le reste: "(FLAGS) <rest>"
    if ") " in s:
        flags_part, rest = s.split(")", 1)
        flags = {f.lower() for f in flags_part.strip("() ").split()}
        rest = rest.strip()
    else:
        # format inattendu
        return flags, s.strip().strip('"')

    # rest est du style:  "." INBOX.Sent     ou     "/" "[Gmail]/Sent Mail"
    # On sépare sur le 1er espace: <delimiter> <mailbox-name>
    parts = rest.split(" ", 1)
    if len(parts) == 1:
        name = parts[0].strip().strip('"')
    else:
        # delimiter = parts[0].strip().strip('"')  # si jamais tu veux l'utiliser
        name = parts[1].strip()
        # Retirer des guillemets autour du nom si présents
        if name.startswith('"') and name.endswith('"') and len(name) >= 2:
            name = name[1:-1]

    return flags, name

def _find_sent_mailbox(imap):
    """Trouve le dossier Envoyés en priorité par flag \\Sent, sinon par noms connus, sinon crée 'Sent'."""
    typ, data = imap.list()
    if typ != "OK":
        return None

    boxes = []
    for line in data or []:
        try:
            flags, name = _parse_imap_list_line(line)
            boxes.append((name, flags))
        except Exception:
            continue

    # 1) Chercher un dossier avec le flag \Sent
    for name, flags in boxes:
        if "\\sent" in flags:
            return name

    # 2) Sinon, tenter les noms usuels
    for cand in IMAP_SENT_CANDIDATES:
        for name, _ in boxes:
            if name.lower() == cand.lower():
                return name

    # 3) Dernier recours: créer 'Sent'
    imap.create("Sent")
    return "Sent"

def _append_to_sent(raw_bytes: bytes):
    """Copie le message dans 'Envoyés' via IMAP en ciblant le bon dossier."""
    if not (IMAP_HOST and IMAP_USER and IMAP_PASS):
        logger.warning("IMAP non configuré — copie 'Envoyés' sautée.")
        return
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(IMAP_USER, IMAP_PASS)
            target = _find_sent_mailbox(imap)
            imap.append(target, "\\Seen", imaplib.Time2Internaldate(time.time()), raw_bytes) # type: ignore
            imap.logout()
            logger.info(f"Copie IMAP dans '{target}' OK.")
    except Exception as e:
        logger.error("Echec copie IMAP 'Envoyés' : %s", e, exc_info=True)

class EmailService:
    """
    Envoi d'email via SMTP (Hostinger/Gmail) + copie dans 'Envoyés' via IMAP.
    Compatible avec l'API existante:
        send_email(recipient, subject, html_body, plain_fallback="")
    """

    def __init__(self):
        if not (SMTP_USER and SMTP_PASS and SENDER_EMAIL):
            raise RuntimeError("SMTP credentials missing. Set SMTP_USER, SMTP_PASS, SENDER_EMAIL")

    def send_email(self, recipient: str, subject: str, html_body: str, plain_fallback: str = "") -> bool:
        try:
            if not (html_body or plain_fallback):
                raise ValueError("Fournis 'html_body' ou 'plain_fallback'.")

            # construit le message MIME moderne
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = _build_from_header()
            msg["To"] = recipient

            # version texte
            text = plain_fallback or to_plain(html_body or "")
            msg.set_content(text or "")

            # version HTML
            if html_body:
                msg.add_alternative(html_body, subtype="html")

            # envoi SMTP
            context = ssl.create_default_context()
            if SMTP_SSL:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=30) as server:
                    server.login(SMTP_USER, SMTP_PASS) # type: ignore
                    server.send_message(msg, to_addrs=[recipient])
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(SMTP_USER, SMTP_PASS) # type: ignore
                    server.send_message(msg, to_addrs=[recipient])

            logger.info("Email envoyé à %s", recipient)

            # copie dans "Envoyés"
            _append_to_sent(msg.as_bytes())
            return True

        except smtplib.SMTPAuthenticationError as e:
            logger.error("Auth SMTP échouée (vérifie App Password/2FA) : %s", e, exc_info=True)
        except Exception as e:
            logger.error("Erreur SMTP/IMAP: %s", e, exc_info=True)
        return False
