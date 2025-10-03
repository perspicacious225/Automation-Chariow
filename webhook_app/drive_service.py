# webhook_app/drive_service.py

import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pathlib import Path
import os
from .database_pg import get_drive_mappings

# Configuration
SERVICE_ACCOUNT_FILE =  Path(os.getenv("SERVICE_ACCOUNT_CRED", "/etc/secrets/credentials_drive_acces.json")) 
SCOPES = ['https://www.googleapis.com/auth/drive']
logger = logging.getLogger(__name__)


try:
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("🤖 Service Google Drive initialisé avec succès.")
except FileNotFoundError:
    drive_service = None
    logger.error(f"❌ Fichier de crédentials '{SERVICE_ACCOUNT_FILE}' introuvable. Le service Drive est désactivé.")
except Exception as e:
    drive_service = None
    logger.error(f"❌ Erreur lors de l'initialisation du service Drive: {e}")


def grant_access_for_sale(sale):
    """
    Fonction principale qui orchestre le partage sur Google Drive pour une vente.
    """
    if drive_service is None:
        logger.warning("Le service Drive n'est pas disponible, le partage est annulé.")
        return

    # 1. Récupérer les dossiers associés à ce produit depuis la BDD
    folder_ids = get_drive_mappings(sale.product_id)
    
    if not folder_ids:
        logger.info(f"Aucun dossier Drive à partager pour le produit {sale.product_id}. On ne fait rien.")
        return

    logger.info(f"Produit {sale.product_id} acheté. Partage de {len(folder_ids)} dossier(s) à {sale.customer_email}.")

    # 2. Pour chaque dossier, donner l'accès au client
    for folder_id in folder_ids:
        try:
            permission = {
                'type': 'user',
                'role': 'reader', # Accès en lecture seule
                'emailAddress': sale.customer_email
            }
            
            drive_service.permissions().create(
                fileId=folder_id,
                body=permission,
                sendNotificationEmail=False # Envoie l'email standard de Google
            ).execute()
            
            logger.info(f"✅ Accès au dossier {folder_id} donné avec succès à {sale.customer_email}.")

        except HttpError as error:
            # Gère les erreurs de l'API, par exemple si le dossier n'existe pas
            logger.error(f"Erreur API Google Drive pour le dossier {folder_id}: {error}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors du partage du dossier {folder_id}: {e}")