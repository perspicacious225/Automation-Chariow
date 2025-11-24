import io, logging, os
from pathlib import Path
from dotenv import load_dotenv


from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .database_pg import get_drive_mappings


load_dotenv()
# Configuration
SERVICE_ACCOUNT_FILE =  Path(os.getenv("SERVICE_ACCOUNT_CRED", "/etc/secrets/credentials_drive_acces.json"))
SCOPES = ['https://www.googleapis.com/auth/drive']
DRIVE_USER_EMAIL=os.getenv("SENDER_EMAIL")
logger = logging.getLogger(__name__)



try:
    if not DRIVE_USER_EMAIL:
        raise ValueError("La variable d'environnement GOOGLE_DRIVE_OWNER_EMAIL (votre email perso) est requise.")
    

    creds_service_account = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    creds = creds_service_account.with_subject(DRIVE_USER_EMAIL)
    drive_service = build('drive', 'v3', credentials=creds)
    logger.info("🤖 Service Google Drive initialisé avec succès.")
except FileNotFoundError:
    drive_service = None
    logger.error(f"❌ Fichier de crédentials '{SERVICE_ACCOUNT_FILE}' introuvable. Le service Drive est désactivé.")
except ValueError as ve:
    drive_service = None
    logger.error(f"❌ Erreur de configuration Drive : {ve}")
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
                'role': 'reader', 
                'emailAddress': sale.customer_email
            }
            
            drive_service.permissions().create(
                fileId=folder_id,
                body=permission,
                sendNotificationEmail=False 
            ).execute()
            
            logger.info(f"✅ Accès au dossier {folder_id} donné avec succès à {sale.customer_email}.")

        except HttpError as error:

            # Gère les erreurs de l'API, par exemple si le dossier n'existe pas
            logger.error(f"Erreur API Google Drive pour le dossier {folder_id}: {error}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors du partage du dossier {folder_id}: {e}")


def upload_elevenlab_audio_generated_to_drive(audio_data:bytes, file_name: str, parent_folder_id)->str | None:

    """
    Téléverse un fichier audio (en bytes) vers un dossier Google Drive spécifié.
    Retourne l'ID du fichier téléversé ou None en cas d'erreur.
    """

    if drive_service is None:
        logger.error("Le service Drive n'est pas disponible, upload annulé.")
        return None
    
    try:

        file_metadata = {
        'name': file_name,
        'parents': [parent_folder_id] if parent_folder_id else  ['1cTcXKsdwri_JZEVcLe5FjJt4FrPMyHul']
        }

        media_body = MediaIoBaseUpload(
        io.BytesIO(audio_data),   
        mimetype='audio/mpeg',
        resumable=True           
        )

        logger.info(f"Début de l'upload de \"{file_name}\" vers le dossier Drive {parent_folder_id}...")

        file = drive_service.files().create(
            body=file_metadata, 
            media_body=media_body,
            fields='id',
           

        ).execute()

        file_id = file.get('id')
        logger.info(f"✅ Fichier '{file_name}' téléversé avec succès. ID: {file_id}")
        return file_id
    

    except HttpError as error:
            logger.error(f"Erreur API Google Drive lors de l'upload : {error}")
            return None
    except Exception as e:
        logger.error(f"Erreur inattendue lors de l'upload vers Drive : {e}")
        return None
