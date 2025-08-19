# webhook_app/services/scheduler.py
import threading, time, json, logging
from typing import Callable
from webhook_app.utils.database import (
    fetch_due_scheduled,
    mark_scheduled_error,
    claim_scheduled_job,
    has_confirmation_for_contact_product,
)
from webhook_app.models.sale import Sale

logger = logging.getLogger(__name__)

def _build_sale_from_payload(payload_json: str) -> Sale:
    data = json.loads(payload_json)
    if "id" in data and "status" in data:
        return Sale(**data)
    elif "sale" in data:
        return Sale(**data["sale"])
    return Sale(**data)

def start_scheduler(send_func: Callable[[Sale, str], bool]):
    """
    send_func(sale, template_type) -> bool
    Thread qui dépile les scheduled_notifications arrivées à échéance.
    """
    def worker():
        while True:
            try:
                due = fetch_due_scheduled(limit=50)
                for job in due:
                    job_id = job["id"]
                    tpl = str(job.get("template_type", ""))

                    # 1) Réserver atomiquement le job (anti-double envoi/concurrence)
                    if not claim_scheduled_job(job_id):
                        continue  # déjà pris/traité ailleurs

                    # 1bis) Garde anti-relance-après-confirmation
                    # Si le job est une RELANCE et qu'une confirmation a déjà été envoyée
                    # pour ce contact+produit, on n'envoie pas et on laisse le job "claimé".
                    if tpl.startswith("relance_"):
                        ckey = job.get("contact_key")
                        pkey = job.get("product_id")
                        if ckey and pkey and has_confirmation_for_contact_product(ckey, pkey):
                            logger.info(
                                "[SCHED][SKIP] Relance ignorée (confirmation déjà envoyée) "
                                "job_id=%s tpl=%s contact=%s product=%s",
                                job_id, tpl, ckey, pkey
                            )
                            # Rien d'autre à faire : le claim a déjà mis sent_at.
                            continue

                    # 2) Construire la vente et tenter l’envoi
                    sale = _build_sale_from_payload(job["payload_json"])
                    try:
                        send_func(sale, tpl)
                        # NOTE: sent_at a été posé par claim_scheduled_job
                        logger.info("[SCHED][SENT] job_id=%s tpl=%s", job_id, tpl)
                    except Exception as e:
                        # Le job restera "claimé" (sent_at non NULL) + on loggue l'erreur
                        mark_scheduled_error(job_id, str(e))
                        logger.exception("[SCHED][ERR] job_id=%s tpl=%s err=%s", job_id, tpl, e)
                        continue
            except Exception:
                # On évite de tuer le thread; ajoute du logging si besoin
                logger.exception("[SCHED] Unhandled scheduler loop error")
            time.sleep(30)  # intervalle de poll

    th = threading.Thread(target=worker, name="relance-scheduler", daemon=True)
    th.start()
    return th
