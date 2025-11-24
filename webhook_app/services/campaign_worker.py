import threading
import time
import json
import logging
from webhook_app.database_pg import (
    fetch_due_campaigns,
    claim_campaign,
    update_campaign_status,
    get_customer_details_for_personalization
)
from webhook_app.services.gmail_api_sender import EmailService

logger = logging.getLogger(__name__)

# On suppose qu'une instance de EmailService est disponible
# Vous devrez peut-être l'initialiser et la passer en paramètre
# Pour simplifier, nous la créons ici
try:
    email_service = EmailService()
except Exception as e:
    email_service = None
    logger.critical(f"Impossible d'initialiser EmailService dans le campaign_worker: {e}")

def start_campaign_worker():
    """Lance le worker de campagnes dans un thread séparé."""
    
    def worker():
        while True:
            if not email_service:
                logger.error("Campaign worker en pause car EmailService n'est pas disponible.")
                time.sleep(60)
                continue

            try:
                campaigns_to_run = fetch_due_campaigns(limit=5)
                for campaign in campaigns_to_run:
                    campaign_id = campaign['id']
                    
                    # 1. Réserver la campagne pour éviter les doublons
                    if not claim_campaign(campaign_id):
                        continue # Déjà prise par un autre processus

                    logger.info(f"[CAMPAIGN WORKER] Démarrage de la campagne ID: {campaign_id}")
                    
                    try:
                        recipients = campaign['recipients']
                        subject = campaign['subject']
                        html_body = campaign['html_body']
                        
                        # 2. Boucle sur les destinataires avec temporisation
                        for recipient_email in recipients:
                            
                            # 3. Personnalisation (optionnel)
                            customer_details = get_customer_details_for_personalization(recipient_email)
                            personalized_body = html_body
                            if customer_details:
                                # Exemple simple de remplacement
                                personalized_body = personalized_body.replace("{{customer_first_name}}", str(customer_details.get('first_name', '')))
                                # Ajoutez d'autres remplacements si nécessaire

                            # 4. Envoi de l'email
                            email_service.send_email(
                                recipient=recipient_email,
                                subject=subject,
                                html_body=personalized_body
                            )
                            
                            # 5. Temporisation pour éviter le spam
                            time.sleep(10) # Pause de 5 secondes entre chaque email
                        
                        # 6. Marquer la campagne comme terminée
                        update_campaign_status(campaign_id, "completed")
                        logger.info(f"[CAMPAIGN WORKER] Campagne ID: {campaign_id} terminée avec succès.")

                    except Exception as e:
                        logger.exception(f"[CAMPAIGN WORKER] Erreur durant l'exécution de la campagne ID: {campaign_id}")
                        update_campaign_status(campaign_id, "failed", str(e))
            
            except Exception as loop_error:
                logger.exception("[CAMPAIGN WORKER] Erreur dans la boucle principale du worker.")

            time.sleep(60) # Vérifie les nouvelles campagnes toutes les 60 secondes

    th = threading.Thread(target=worker, name="campaign-worker", daemon=True)
    th.start()
    logger.info("🚀 Campaign Worker démarré.")
    return th