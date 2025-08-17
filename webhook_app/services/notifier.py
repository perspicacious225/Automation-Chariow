# services/notifier.py
import logging
import os
import re
from webhook_app.models.sale import Sale
from webhook_app.templates import messages
from .mailer import EmailService, to_plain
from .whatsapp import WhatsAppService
from webhook_app.utils.database import Database

import unicodedata
import re

logger = logging.getLogger(__name__)

# -------- Normalisation email (clé de dédup) --------
EMAIL_RE = re.compile(r"\s+")
def norm_email(s: str | None) -> str | None:
    return EMAIL_RE.sub("", s.lower()) if s else None

def _slug(s: str) -> str:
    """
    Convertit une chaîne en *slug* ASCII, stable et lisible, adapté aux clés/identifiants.

    Règles :
      - Normalise Unicode (NFKD) puis supprime les accents et caractères non ASCII.
      - Remplace toute séquence non alphanumérique par un tiret « - ».
      - Met en minuscules et enlève les tirets en début/fin.

    Usage typique :
      - Clé de déduplication « par produit » quand `product_id` est absent,
        pour éviter les variations dues aux accents, espaces, ponctuation, etc.
    """
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s



# -------- Rendu "safe" des templates (évite KeyError sur {placeholders}) --------
class _SafeDict(dict):
    def __missing__(self, key):
        return ""

def _render(tpl: str, vars_dict: dict) -> str:
    return tpl.format_map(_SafeDict(vars_dict))


class Notifier:
    """
    - Relances (abandon/failure) :
        dédup PAR PRODUIT ET DESTINATAIRE (email normalisé / téléphone normalisé),
        avec fenêtre (NOTIFY_DEDUPE_WINDOW_DAYS ; 0 = jamais renvoyer).
      WhatsApp UNIQUEMENT si aucun email de relance (pour CE PRODUIT) n'a encore été envoyé
      à ce destinataire, ou si le client n'a pas d'email.
    - Confirmation (success) : dédup PAR VENTE (sale.id).
    """

    def __init__(self):
        self.email_service = EmailService()
        self.whatsapp_service = WhatsAppService()
        self.db = Database()
        try:
            self.window_days = int(os.getenv("NOTIFY_DEDUPE_WINDOW_DAYS", "0") or "0")
        except ValueError:
            self.window_days = 0

    # ------------------------------
    # Préparation variables template
    # ------------------------------
    def _prepare_template_vars(self, sale: Sale) -> dict:
        sale_id = getattr(sale, "id", None) or getattr(sale, "sale_id", None) or ""
        return {
            "sale_id": sale_id,
            "name": sale.customer_name or "cher client",
            "product_name": sale.product_name,
            "customer_email": sale.customer_email or "",
            "checkout_url": sale.checkout_url or "",
            "amount": sale.amount or "",
            "product_value": getattr(sale, "product_value", "") or "",
            "store_name": sale.store_name or "",
            "store_url": sale.store_url or "",
            "current_year": getattr(sale, "current_year", "") or "",
        }

    # ------------------------------
    # Clés "par produit" pour la dédup des relances
    # ------------------------------
    def _product_key(self, sale: Sale) -> str:
        """
        Clé *stable* pour la dédup par produit.
        1) utilise l'ID produit fourni par le webhook (sale.product_id)
        2) fallback: nom + boutique sluggés si jamais product_id est vide
        ⚠️ Jamais le prix.
        """
        if getattr(sale, "product_id", None):
            return _slug(sale.product_id)

        # fallback
        name  = sale.product_name or ""
        store = sale.store_name or ""
        return f"{_slug(name)}|{_slug(store)}"

    def _email_recipient_key(self, sale: Sale, email_norm: str | None) -> str | None:
        prod = self._product_key(sale)
        return f"{email_norm}|{prod}" if email_norm else None

    def _wa_recipient_key(self, sale: Sale, phone_key: str | None) -> str | None:
        prod = self._product_key(sale)
        return f"{phone_key}|{prod}" if phone_key else None

    # ------------------------------
    # Politique de dédup globale
    # ------------------------------
    def _already_sent(self, sale: Sale, channel: str, template_type: str, recipient_norm: str | None) -> bool:
        """
        - abandon/failure: on attend ici une *clé par produit* (email_key / wa_key), et on
          délègue à has_notified_recipient(recipient, channel, template, window_days)
        - success: dédup par sale.id
        """
        t = (template_type or "").lower()
        if t in ("abandon", "failure"):
            return self.db.has_notified_recipient(recipient_norm or "", channel, t, self.window_days)
        return self.db.has_notified(sale.id, channel, t)

    # ------------------------------
    # Envoi principal
    # ------------------------------
    def _send_notification(self, sale: Sale, template_type: str) -> bool:
        tvars = self._prepare_template_vars(sale)

        # ---------- EMAIL ----------
        email_raw  = sale.customer_email
        email_norm = norm_email(email_raw)
        email_key  = self._email_recipient_key(sale, email_norm)  # <- clé PAR PRODUIT

        # état AVANT envoi (sert à la porte WhatsApp)
        email_previously_sent = self._already_sent(sale, "email", template_type, email_key)

        if email_previously_sent:
            logger.info("[SKIP][email] déjà envoyé: template=%s recipient=%s", template_type, email_key)
        else:
            if email_raw:
                tpl = messages.EMAIL_TEMPLATES[template_type]
                sub = messages.EMAIL_SUBJECTS[template_type]
                html = _render(tpl, tvars)
                subject = _render(f"{sub} {sale.store_name}", tvars)
                ok_email = self.email_service.send_email(
                    recipient=email_raw,
                    subject=subject,
                    html_body=html,
                    plain_fallback=to_plain(html)
                )
                if ok_email:
                    self.db.mark_notified(sale.id, "email", template_type, email_key)
                    logger.info("[SENT][email] sale=%s template=%s to=%s", sale.id, template_type, email_key)
            else:
                logger.info("[SKIP][email] pas d'adresse email pour sale=%s", sale.id)

        # ---------- WHATSAPP ----------
        # Porte basée sur l'état AVANT email pour CE PRODUIT
        send_whatsapp_allowed = (not email_previously_sent) or (email_norm is None)

        phone_raw = sale.customer_phone
        phone_key = WhatsAppService.normalize_for_dedupe(phone_raw) if phone_raw else None  # chiffres normalisés (sans @c.us)
        wa_key    = self._wa_recipient_key(sale, phone_key)  # <- clé PAR PRODUIT
        wa_tpl    = messages.TEMPLATES_WHATSAPP.get(template_type)

        if not send_whatsapp_allowed:
            logger.info("[SKIP][whatsapp] email déjà reçu (par produit) pour template=%s", template_type)
        else:
            if self._already_sent(sale, "whatsapp", template_type, wa_key):
                logger.info("[SKIP][whatsapp] déjà envoyé: template=%s recipient=%s", template_type, wa_key)
            else:
                if phone_raw and phone_key and wa_tpl:
                    wa_msg = _render(wa_tpl, tvars)
                    ok_wa = self.whatsapp_service.send_message(phone_raw, wa_msg)
                    if ok_wa:
                        self.db.mark_notified(sale.id, "whatsapp", template_type, wa_key)
                        logger.info("[SENT][whatsapp] sale=%s template=%s to=%s", sale.id, template_type, wa_key)
                else:
                    if not wa_tpl:
                        logger.info("[SKIP][whatsapp] template WhatsApp manquant pour '%s'", template_type)
                    else:
                        logger.info("[SKIP][whatsapp] téléphone non disponible/normalisable (raw=%s, key=%s)", phone_raw, phone_key)

        return True  # OK global (même si un canal est skip)

    # ------------------------------
    # Entrées publiques
    # ------------------------------
    def handle_abandoned(self, sale: Sale):
        return self._send_notification(sale, "abandon")

    def handle_failed(self, sale: Sale):
        return self._send_notification(sale, "failure")

    def handle_success(self, sale: Sale):
        return self._send_notification(sale, "success")
