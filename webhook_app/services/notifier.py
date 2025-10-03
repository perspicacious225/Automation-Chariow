import logging, os, re, unicodedata, hashlib, datetime
from webhook_app.models.sale import Sale
from webhook_app.templates import messages
from webhook_app.templates.messages import render_email_with_brand
from .mailer import  to_plain
from .gmail_api_sender import EmailService

from .whatsapp import WhatsAppService
from webhook_app.config import Config, RELANCE_DELAYS, RELANCE_DELAYS_A, RELANCE_DELAYS_B, WHATSAPP_AND_EMAIL_ALWAYS
from webhook_app.database_pg import (
    Database, enqueue_notification, has_active_cadence_for,
    refresh_cadence_payload, get_template,
    has_recent_contact_product_notification, cancel_cadence_for,
    has_confirmation_for_contact_product, latest_relance_step
)


from webhook_app.utils.formatting import format_money

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"\s+")
def norm_email(s: str | None) -> str | None:
    return EMAIL_RE.sub("", s.lower()) if s else None

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s

class _SafeDict(dict):
    def __missing__(self, key): return ""

def _render(tpl: str, vars_dict: dict) -> str:
    return tpl.format_map(_SafeDict(vars_dict))


class Notifier:
    def __init__(self):
        self.email_service = EmailService()
        self.whatsapp_service = WhatsAppService()
        self.db = Database()
        try:
            self.window_days = int(os.getenv("NOTIFY_DEDUPE_WINDOW_DAYS", "0") or "0")
        except ValueError:
            self.window_days = 0

    def _contact_key(self, sale: Sale) -> str:
        email = (sale.customer_email or "").strip().lower()
        if email:
            return email
        try:
            return self.whatsapp_service.normalize_for_dedupe(sale.customer_phone)
        except Exception:
            return (sale.customer_phone or "").strip()

    def _product_key(self, sale: Sale) -> str:
        if getattr(sale, "product_id", None):
            return sale.product_id
        name = sale.product_name or ""
        store = sale.store_name or ""
        return f"{_slug(name)}|{_slug(store)}" or "unknown-product"
    
    def _extract_prices(self, sale):
        """
        Utilise directement les champs de Sale :
        - price_current = sale.amount          (prix courant du webhook)
        - price_after   = sale.product_value   (prix catalogue)
        - currency      = sale.currency or 'XOF'
        """
        pc = None
        pa = None
        cur = getattr(sale, "currency", None) or "XOF"

        # prix courant
        try:
            pc = float(sale.amount) if sale.amount is not None else None
        except Exception:
            pc = None

        # prix barré
        try:
            pa = float(sale.product_value) if sale.product_value is not None else None
        except Exception:
            pa = None

        # fallback s’il manque le prix barré : garde un différentiel lisible
        if (pa is None or pa == 0) and pc:
            pa = round(pc * 3.5)

        return pc, pa, cur


    def _prepare_template_vars(self, sale: Sale) -> dict:
        sale_id = getattr(sale, "id", None) or getattr(sale, "sale_id", None) or ""
        pc, pa, cur = self._extract_prices(sale)
        return {
            "sale_id": sale_id,
            "customer_first_name": sale.customer_first_name or "cher client",
            "product_name": sale.product_name,
            "customer_email": sale.customer_email or "",
            "checkout_url": sale.checkout_url or "",
            "amount": sale.amount or "",
            "product_value": getattr(sale, "product_value", "") or "",
            "store_name": sale.store_name or "",
            "store_url": sale.store_url or "",
            "current_year": getattr(sale, "current_year", "") or "",

            "Message_commun": messages.Message_commun_html,
            "Message_commun_wa": messages.Message_commun_wa,

            "price_current": pc,
            "price_after": pa,
            "currency": cur,
            "price_current_fmt": format_money(pc, cur),
            "price_after_fmt": format_money(pa, cur),
        }

    # ========= A/B arm déterministe par contact+produit =========
    def _ab_arm_for(self, contact_key: str, product_key: str) -> str:
        seed = f"{contact_key}|{product_key}"
        h = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % 100
        return "B" if h < Config.AB_TEST_SPLIT_PERCENT else "A"

    # ========= Dédup logique =========
    def _already_sent(self, sale: Sale, channel: str, template_type: str, recipient_norm: str | None) -> bool:
        t = (template_type or "").lower()
        if t.startswith("relance_"):
            return self.db.has_notified_recipient(recipient_norm or "", channel, t, self.window_days)
        
        return self.db.has_notified(sale.id, channel, t)


    # ========= Envoi =========
    def _send_notification(self, sale: Sale, template_type: str, *, ab_arm: str | None = None) -> bool:
        tvars = self._prepare_template_vars(sale)
        contact_key = self._contact_key(sale)
        product_key = self._product_key(sale)

        # Annule toute relance si une confirmation a déjà été envoyée pour ce contact+produit
        if template_type.startswith("relance_"):
            if has_confirmation_for_contact_product(contact_key, product_key):
                logger.info("[SKIP] relance annulée: confirmation déjà envoyée (contact=%s, produit=%s, tpl=%s)",
                            contact_key, product_key, template_type)
                return True

        # Blocs communs rendus une fois
        if hasattr(messages, "Message_commun"):
            tvars["Message_commun"] = _render(messages.Message_commun_html, tvars)
        if hasattr(messages, "Message_commun_wa"):
            tvars["Message_commun_wa"] = _render(messages.Message_commun_wa, tvars)

        # -------- EMAIL --------
        email_raw  = sale.customer_email
        email_norm = norm_email(email_raw)
        email_key  = f"{email_norm}|{product_key}" if email_norm else None

        email_previously_sent = self._already_sent(sale, "email", template_type, email_key)
        if not email_previously_sent and email_raw:
            # Cherche un template DB spécifique au produit
            dbtpl = get_template(sale.product_id, template_type, "email")
            if dbtpl:
                # Sujet
                subject_tpl = (dbtpl.get("subject") or "").strip()
                subject = subject_tpl.format_map(_SafeDict(tvars)) if subject_tpl else _render(
                    f"{messages.EMAIL_SUBJECTS.get(template_type,'')} {sale.store_name}".strip(), tvars
                )
                #mode HTML complet ou fragment
                body_tpl = dbtpl.get("body") or ""
                if int(dbtpl.get("is_full_html") or 0) == 1:
                    html = body_tpl.format_map(_SafeDict(tvars))
                else:
                    fragment = body_tpl.format_map(_SafeDict(tvars))
                    html = render_email_with_brand(fragment, tvars)
            else:
                # Fallback sur messages.py
                tpl = messages.EMAIL_TEMPLATES.get(template_type)
                sub = messages.EMAIL_SUBJECTS.get(template_type, "")
                if not tpl:
                    logger.info("[SKIP][email] template manquant pour '%s'", template_type)
                    tpl = ""
                html = _render(tpl, tvars)
                subject = _render(f"{sub} {sale.store_name}".strip(), tvars)

            ok_email = self.email_service.send_email(
                recipient=email_raw,
                subject=subject,
                html_body=html,
                plain_fallback=to_plain(html)
            )
            if ok_email:
                self.db.mark_notified(
                    sale.id, "email", template_type, email_key,
                    recipient_email=email_norm, contact_key=contact_key, product_id=product_key, ab_arm=ab_arm
                )
                logger.info("[SENT][email] sale=%s template=%s to=%s", sale.id, template_type, email_key)
        else:
            if email_previously_sent:
                logger.info("[SKIP][email] déjà envoyé: template=%s recipient=%s", template_type, email_key)
            else:
                logger.info("[SKIP][email] pas d'email pour sale=%s", sale.id)

        # -------- WHATSAPP --------
        send_whatsapp_allowed = WHATSAPP_AND_EMAIL_ALWAYS or (not email_previously_sent) or (email_norm is None)
        phone_raw = sale.customer_phone
        phone_key = self.whatsapp_service.normalize_for_dedupe(phone_raw) if phone_raw else None
        wa_key    = f"{phone_key}|{product_key}" if phone_key else None

        if send_whatsapp_allowed and phone_raw and phone_key:
            # 1 DB d’abord
            wa_tpl_row = get_template(sale.product_id, template_type, "whatsapp")
            if wa_tpl_row and (wa_tpl_row.get("body") or "").strip():
                wa_tpl = wa_tpl_row["body"]
            else:
                # 2 fallback messages.py
                wa_tpl = messages.TEMPLATES_WHATSAPP.get(template_type)

            if wa_tpl:
                if self._already_sent(sale, "whatsapp", template_type, wa_key):
                    logger.info("[SKIP][whatsapp] déjà envoyé: template=%s recipient=%s", template_type, wa_key)
                else:
                    wa_msg = _render(wa_tpl, tvars)
                    ok_wa = self.whatsapp_service.send_message(phone_raw, wa_msg)
                    if ok_wa:
                        self.db.mark_notified(
                            sale.id, "whatsapp", template_type, wa_key,
                            recipient_phone=phone_key, contact_key=contact_key, product_id=product_key, ab_arm=ab_arm
                        )
                        logger.info("[SENT][whatsapp] sale=%s template=%s to=%s", sale.id, template_type, wa_key)
            else:
                logger.info("[SKIP][whatsapp] template manquant pour '%s'", template_type)
        else:
            if not send_whatsapp_allowed:
                logger.info("[SKIP][whatsapp] porte fermée par stratégie (email déjà reçu)")
            elif not phone_raw or not phone_key:
                logger.info("[SKIP][whatsapp] téléphone non disponible/normalisable")

        return True


    # ========= Entrées publiques =========
    def handle_abandoned(self, sale: Sale):
        return self.schedule_relances(sale)

    def handle_failed(self, sale: Sale):
        return self.schedule_relances(sale)

    def handle_success(self, sale: Sale):
        """
        Envoie la confirmation adaptée au parcours, basée sur la DERNIERE relance
        réellement envoyée pour CE contact+produit (et pas sur sale_id).
        Mapping:
        None   -> confirm_3_1
        t30    -> confirm_3_2
        t6h    -> confirm_3_3
        t23h   -> confirm_3_4
        t47h   -> confirm_3_5
        """
        contact_key = self._contact_key(sale)
        product_key = self._product_key(sale)

        # 1) Stopper toute cadence à venir pour ce couple
        try:
            n = cancel_cadence_for(contact_key, product_key)
            logger.info("[CANCEL] cadence supprimée (%s jobs) contact=%s produit=%s", n, contact_key, product_key)
        except Exception:
            logger.exception("Cancel cadence failed")

        # 2) Chercher la dernière relance envoyée pour ce contact+produit
        last_step = latest_relance_step(contact_key, product_key)  # 't47h' | 't23h' | 't6h' | 't30' | None
        step_map = {
            None:  "3_1",
            "t30": "3_2",
            "t6h": "3_3",
            "t23h":"3_4",
            "t47h":"3_5",
        }
        confirm_key = f"confirm_{step_map.get(last_step, '3_1')}"
        logger.info("[CONFIRM] last_relance=%s -> %s (contact=%s, prod=%s)", last_step, confirm_key, contact_key, product_key)

        return self._send_notification(sale, confirm_key)

    def schedule_relances(self, sale: Sale):
        contact_key = self._contact_key(sale)
        product_key = self._product_key(sale)

        # A/B arm déterminé une fois pour ce couple
        ab_arm = self._ab_arm_for(contact_key, product_key)
        delays = RELANCE_DELAYS_B if ab_arm == "B" else RELANCE_DELAYS_A

        # Cadence déjà active ?
        if has_active_cadence_for(contact_key, product_key):
            if Config.CADENCE_REFRESH_POLICY == 'refresh':
                sale_snapshot = self._sale_snapshot(sale)
                refresh_cadence_payload(contact_key, product_key, {"sale": sale_snapshot})
                logger.info("Cadence rafraîchie pour contact=%s product=%s", contact_key, product_key)
            else:
                logger.info("Cadence déjà active (policy=ignore) contact=%s product=%s", contact_key, product_key)
            return True

        # Anti-spam fenêtre
        email_norm = norm_email(sale.customer_email)
        try:
            phone_norm = self.whatsapp_service.normalize_for_dedupe(sale.customer_phone)
        except Exception:
            phone_norm = sale.customer_phone
        if has_recent_contact_product_notification(email_norm, phone_norm, product_key, Config.NOTIFY_DEDUPE_WINDOW_MINUTES):
            logger.info("Skip planif (dedupe fenetre) contact=%s prod=%s", contact_key, product_key)
            return True

        # planification
        base_epoch = int(datetime.datetime.utcnow().timestamp())
        sale_snapshot = self._sale_snapshot(sale)
        for key in ["t30","t6h","t23h","t47h"]:
            minutes = delays[key]
            due_epoch = base_epoch + minutes*60
            template_key = f"relance_{key}"
            enqueue_notification(
                sale.id, template_key, due_epoch, {"sale": sale_snapshot},
                contact_key=contact_key, product_id=product_key, ab_arm=ab_arm
            )
        return True

    def _sale_snapshot(self, sale: Sale) -> dict:
        cur = getattr(sale, "currency", None) or "XOF"
        return dict(
            id=sale.id, status=sale.status, product_name=sale.product_name, product_id=sale.product_id,
            customer_first_name=sale.customer_first_name, customer_phone=sale.customer_phone, customer_country=sale.customer_country,
            customer_email=sale.customer_email, store_name=sale.store_name, store_url=sale.store_url,
            checkout_url=sale.checkout_url, amount=sale.amount, created_at=sale.created_at,abandoned_at=sale.abandoned_at, failed_at=sale.failed_at, completed_at=sale.completed_at,
            product_value=sale.product_value, current_year=sale.current_year, currency=cur
        )
