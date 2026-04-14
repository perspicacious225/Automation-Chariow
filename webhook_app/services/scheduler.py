# webhook_app/services/scheduler.py
import threading, time, json, logging
from typing import Callable
from webhook_app.database_pg import (
    fetch_due_scheduled,
    mark_scheduled_error,
    claim_scheduled_job,
    has_confirmation_for_contact_product,
    cancel_cadence_for,
    fetch_pending_emails,          # ← à ajouter dans database_pg.py
    mark_pending_email_sent,       # ← à ajouter dans database_pg.py
    cancel_pending_emails_for,     # ← à ajouter dans database_pg.py
)
from webhook_app.models.sale import Sale

logger = logging.getLogger(__name__)

PENDING_EMAIL_THROTTLE_SECONDS = 5   # délai entre chaque email de rattrapage

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
        return  # Gmail toujours KO, on réessaiera dans 30s

    jobs = fetch_pending_emails()   # tous les email_pending groupés par contact
    if not jobs:
        return

    logger.info("[PENDING] Gmail revenue — rattrapage de %s contacts", len(jobs))

    for contact_key, contact_jobs in jobs.items():
        try:
            # Vérif achat entre-temps
            product_key = contact_jobs[0]["product_id"]
            if has_confirmation_for_contact_product(contact_key, product_key):
                logger.info("[PENDING][SKIP] achat détecté — annule %s emails contact=%s",
                            len(contact_jobs), contact_key)
                for job in contact_jobs:
                    cancel_pending_emails_for(contact_key, product_key)
                continue

            # Trie par due_at DESC → garde le plus récent
            contact_jobs.sort(key=lambda j: j["due_at"], reverse=True)
            to_send = contact_jobs[0]
            to_cancel = contact_jobs[1:]

            # Annule les plus anciens
            for old_job in to_cancel:
                cancel_pending_emails_for(contact_key, product_key,
                                          exclude_id=to_send["id"])
                logger.info("[PENDING][CANCEL] job_id=%s (plus ancien) contact=%s",
                            old_job["id"], contact_key)

            # Envoie le plus récent
            payload = json.loads(to_send["payload_json"])
            ok = email_service.send_email(
                recipient=payload["email_raw"],
                subject=payload["subject"],
                html_body=payload["html"],
                plain_fallback=payload.get("html", ""),
            )
            if ok:
                mark_pending_email_sent(to_send["id"], payload)
                logger.info("[PENDING][SENT] job_id=%s contact=%s", to_send["id"], contact_key)
            else:
                logger.warning("[PENDING][FAIL] job_id=%s — sera retenté", to_send["id"])

        except Exception:
            logger.exception("[PENDING][ERR] contact=%s", contact_key)

        time.sleep(PENDING_EMAIL_THROTTLE_SECONDS)


def start_scheduler(send_func: Callable[[Sale, str], bool], email_service=None):
    """
    send_func(sale, template_type) -> bool
    email_service : instance EmailService pour le rattrapage email_pending
    """
    def worker():
        while True:
            try:
                # ── Jobs normaux ──────────────────────────────────────────
                due = fetch_due_scheduled(limit=50)
                for job in due:
                    job_id = job["id"]
                    tpl = str(job.get("template_type", ""))

                    # Skip les email_pending (traités séparément)
                    if tpl.startswith("email_pending::"):
                        continue

                    if not claim_scheduled_job(job_id):
                        continue

                    if tpl.startswith("relance_"):
                        ckey = job.get("contact_key")
                        pkey = job.get("product_id")
                        if ckey and pkey and has_confirmation_for_contact_product(ckey, pkey):
                            logger.info(
                                "[SCHED][SKIP] Relance ignorée (confirmation déjà envoyée) "
                                "job_id=%s tpl=%s contact=%s product=%s",
                                job_id, tpl, ckey, pkey
                            )
                            try:
                                n = cancel_cadence_for(ckey, pkey)
                                logger.info("[CANCEL] cadence supprimée (%s jobs) contact=%s produit=%s",
                                            n, ckey, pkey)
                            except Exception:
                                logger.exception("Cancel cadence failed")
                            continue

                    sale = _build_sale_from_payload(job["payload_json"])
                    try:
                        send_func(sale, tpl)
                        logger.info("[SCHED][SENT] job_id=%s tpl=%s", job_id, tpl)
                    except Exception as e:
                        mark_scheduled_error(job_id, str(e))
                        logger.exception("[SCHED][ERR] job_id=%s tpl=%s err=%s", job_id, tpl, e)

                # ── Rattrapage email_pending ───────────────────────────────
                if email_service:
                    _process_pending_emails(email_service)

            except Exception:
                logger.exception("[SCHED] Unhandled scheduler loop error")

            time.sleep(30)

    th = threading.Thread(target=worker, name="relance-scheduler", daemon=True)
    th.start()
    return th