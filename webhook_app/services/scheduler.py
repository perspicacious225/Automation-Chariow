# webhook_app/services/scheduler.py
import threading
import time
import json
import logging
from typing import Callable

import psycopg2

from webhook_app.database_pg import (
    fetch_due_scheduled,
    mark_scheduled_error,
    claim_scheduled_job,
    has_confirmation_for_contact_product,
    cancel_cadence_for,
    fetch_pending_emails,
    mark_pending_email_sent,
    cancel_pending_emails_for,
)
from webhook_app.models.sale import Sale

logger = logging.getLogger(__name__)

PENDING_EMAIL_THROTTLE_SECONDS = 5


def _build_sale_from_payload(payload_json: str) -> Sale:
    data = json.loads(payload_json)
    if "id" in data and "status" in data:
        return Sale(**data)
    elif "sale" in data:
        return Sale(**data["sale"])
    return Sale(**data)


def _process_pending_emails(email_service):
    """
    Rattrapage Option B :
    - 1 email max par contact (le plus récent)
    - Vérifie achat entre-temps avant d'envoyer
    - Throttle de 5s entre chaque contact
    """
    if not email_service.is_available():
        return

    jobs = fetch_pending_emails()
    if not jobs:
        return

    logger.info("[PENDING] Gmail revenue — rattrapage de %s contacts", len(jobs))

    for contact_key, contact_jobs in jobs.items():
        try:
            product_key = contact_jobs[0]["product_id"]
            if has_confirmation_for_contact_product(contact_key, product_key):
                logger.info(
                    "[PENDING][SKIP] achat détecté — annule %s emails contact=%s",
                    len(contact_jobs), contact_key,
                )
                cancel_pending_emails_for(contact_key, product_key)
                continue

            contact_jobs.sort(key=lambda j: j["due_at"], reverse=True)
            to_send   = contact_jobs[0]
            to_cancel = contact_jobs[1:]

            for old_job in to_cancel:
                cancel_pending_emails_for(
                    contact_key, product_key, exclude_id=to_send["id"]
                )
                logger.info(
                    "[PENDING][CANCEL] job_id=%s (plus ancien) contact=%s",
                    old_job["id"], contact_key,
                )

            payload = (
                to_send["payload_json"]
                if isinstance(to_send["payload_json"], dict)
                else json.loads(to_send["payload_json"])
            )
            ok = email_service.send_email(
                recipient=payload["email_raw"],
                subject=payload["subject"],
                html_body=payload["html"],
                plain_fallback=payload.get("html", ""),
            )
            if ok:
                mark_pending_email_sent(to_send["id"], payload)
                logger.info(
                    "[PENDING][SENT] job_id=%s contact=%s",
                    to_send["id"], contact_key,
                )
            else:
                logger.warning(
                    "[PENDING][FAIL] job_id=%s — sera retenté", to_send["id"]
                )

        except Exception:
            logger.exception("[PENDING][ERR] contact=%s", contact_key)

        time.sleep(PENDING_EMAIL_THROTTLE_SECONDS)


# ── Mots-clés SSL/connexion récupérables 
_SSL_KEYWORDS = ("ssl syscall", "eof detected", "ssl connection", "connection reset")


def _is_ssl_error(e: Exception) -> bool:
    return any(kw in str(e).lower() for kw in _SSL_KEYWORDS)


def start_scheduler(send_func: Callable[[Sale, str], bool], email_service=None):
    """
    send_func(sale, template_type) -> bool
    email_service : instance EmailService pour le rattrapage email_pending
    """

    def worker():
        while True:
            try:
                # ── Jobs normaux 
                due = fetch_due_scheduled(limit=50)

                for job in due:
                    job_id = job["id"]
                    tpl    = str(job.get("template_type", ""))

                    if tpl.startswith("email_pending::"):
                        continue

                    if not claim_scheduled_job(job_id):
                        continue

                    if tpl.startswith("relance_"):
                        ckey = job.get("contact_key")
                        pkey = job.get("product_id")
                        if ckey and pkey and has_confirmation_for_contact_product(ckey, pkey):
                            logger.info(
                                "[SCHED][SKIP] Relance ignorée job_id=%s tpl=%s "
                                "contact=%s product=%s",
                                job_id, tpl, ckey, pkey,
                            )
                            try:
                                n = cancel_cadence_for(ckey, pkey)
                                logger.info(
                                    "[CANCEL] cadence supprimée (%s jobs) "
                                    "contact=%s produit=%s",
                                    n, ckey, pkey,
                                )
                            except Exception:
                                logger.exception("Cancel cadence failed")
                            continue

                    sale = _build_sale_from_payload(job["payload_json"])
                    try:
                        send_func(sale, tpl)
                        logger.info("[SCHED][SENT] job_id=%s tpl=%s", job_id, tpl)
                    except Exception as e:
                        mark_scheduled_error(job_id, str(e))
                        logger.exception(
                            "[SCHED][ERR] job_id=%s tpl=%s err=%s", job_id, tpl, e
                        )

                # ── Rattrapage email_pending ──────────────────────────────
                if email_service:
                    _process_pending_emails(email_service)

            # ── Connexion SSL coupée par Supabase (idle timeout) ──────────
            # Erreur transiente et récupérable — on log un WARNING simple
            # sans stack trace, le cycle suivant rouvrira une connexion fraîche
            except psycopg2.OperationalError as e:
                if _is_ssl_error(e):
                    logger.warning(
                        "[SCHED] Connexion DB SSL perdue (idle timeout) "
                        "— retry dans 30s : %s", e,
                    )
                else:
                    logger.exception(
                        "[SCHED] Erreur DB OperationalError non récupérable"
                    )

            # ── Toute autre erreur inattendue ─────────────────────────────
            except psycopg2.OperationalError as e:
                logger.warning("[SCHED] Erreur DB transitoire — retry dans 30s : %s", e)
            except Exception:
                logger.exception("[SCHED] Unhandled scheduler loop error")

            time.sleep(30)

    def debounce_worker():
        while True:
            try:
                _process_debounced_conversations()
            except psycopg2.OperationalError as e:
                logger.warning("[DEBOUNCE] Erreur DB transitoire : %s", e)
            except Exception:
                logger.exception("[DEBOUNCE] Unhandled error")
            time.sleep(2)  

    th  = threading.Thread(target=worker, name="relance-scheduler", daemon=True)
    dth = threading.Thread(target=debounce_worker, name="debounce-watcher",  daemon=True)

    th.start()
    dth.start()

   
    return th

def _process_debounced_conversations() -> None:
    """
    Traite les conversations dont le debounce a expiré.
    Agrège tous les messages pending et déclenche le LLM une seule fois.
    """
    from webhook_app.database_conv import (
        fetch_expired_debounce,
        claim_debounce,
        fetch_pending_user_messages,
    )
    from webhook_app.conversation.manager import ConversationManager

    expired = fetch_expired_debounce(limit=10)
    if not expired:
        return

    for conv in expired:
        conv_id  = str(conv["id"])
        phone    = conv["phone"]

        # Claim atomique — évite double traitement (multi-workers)
        if not claim_debounce(conv_id):
            logger.debug("[DEBOUNCE] Claim raté (déjà traité) — conv=%s", conv_id)
            continue

        pending_msgs = fetch_pending_user_messages(conv_id)
        if not pending_msgs:
            continue

        # Agréger les messages dans l'ordre chronologique
        aggregated_text = "\n".join(
            m["content"] for m in pending_msgs if (m["content"] or "").strip()
        )
        last_wa_id = pending_msgs[-1]["wa_message_id"]

        logger.info(
            "[DEBOUNCE] Traitement %d message(s) agrégé(s) — conv=%s | phone=%s",
            len(pending_msgs), conv_id, phone,
        )

        try:
            manager = ConversationManager()
            manager.handle_incoming(
                phone=phone,
                text=aggregated_text,
                wa_message_id=last_wa_id,
                user_message_saved=True,
            )
        except Exception:
            logger.exception("[DEBOUNCE][ERR] conv=%s phone=%s", conv_id, phone)