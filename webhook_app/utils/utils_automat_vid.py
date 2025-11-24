

import boto3
import os
import io
from dotenv import load_dotenv
import logging
logger = logging.getLogger(__name__)

load_dotenv()
s3_client = boto3.client('s3')

BUCKET_NAME = os.getenv("AWS_S3_BUCKET_NAME") 

# Téléversersement ---
def upload_audio_to_s3(audio_data: bytes, s3_filename: str) -> str | None:
    """Téléverse des données audio vers S3 et retourne l'URL (ou None)."""
    if not BUCKET_NAME:
        print("Erreur: Nom du bucket S3 non configuré.")
        return None
    try:
        # Créer un objet fichier en mémoire à partir des bytes
        audio_stream = io.BytesIO(audio_data)

        s3_client.upload_fileobj(
            audio_stream,       # L'objet fichier en mémoire
            BUCKET_NAME,       
            s3_filename,       
            ExtraArgs={'ContentType': 'audio/mpeg'} 
        )
        
        
        print(f"Upload vers S3 réussi : {s3_filename}")
        return s3_filename 

    except Exception as e:
        print(f"Erreur lors de l'upload vers S3 : {e}")
        return None
    

# Obtenir un lien 
def get_s3_public_url(s3_filename: str) -> str:
    """Construit l'URL publique standard d'un objet S3."""

    # Attention: Ne fonctionne que si l'objet est publiquement lisible
    region = os.getenv("AWS_DEFAULT_REGION")
    return f"https://{BUCKET_NAME}.s3.{region}.amazonaws.com/{s3_filename}"



def generate_presigned_s3_url(s3_filename: str, expiration_secs: int = 3600) -> str | None:
    """Génère une URL temporaire et sécurisée pour accéder à un objet privé."""
    if not BUCKET_NAME: return None
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': BUCKET_NAME, 'Key': s3_filename},
            ExpiresIn=expiration_secs 
        )
        return url
    except Exception as e:
        print(f"Erreur génération URL pré-signée S3 : {e}")
        return None


#EXTRACTION DU TEXTE
# ==============================================================================
from webhook_app.database_pg import get_generated_script_by_id
import logging

def extract_voice_over_text(brief_id: int) -> str | None:
    """Extrait le texte [VOIX OFF] nettoyé du script d'un brief donné."""
    try:
        brief_row = get_generated_script_by_id(brief_id)
        if not brief_row or not brief_row.get('generated_script'):
            logger.error(f"Script non trouvé ou vide pour le brief {brief_id} lors de l'extraction.")
            return None
            
        script_text = brief_row['generated_script']
        voice_over_parts = []
        for line in script_text.splitlines():
            clean_line = line.strip().strip('*')
            if clean_line.upper().startswith('[VOIX OFF]'):
                parts = clean_line.split(":", 1)
                if len(parts) > 1:
                    text_part = parts[1].strip()
                    # print(text_part)
                    if ('(') in text_part and ')' in text_part:
                        #  print(text_part)
                         text_part = text_part[text_part.find(')')+1:].strip()
                        #  print(text_part)
                    if text_part:
                        voice_over_parts.append(text_part)
        
        final_voice_over = " ".join(voice_over_parts)
        if not final_voice_over:
            logger.warning(f"Aucun texte [VOIX OFF] trouvé dans le script du brief {brief_id}.")
            return None
        return final_voice_over
    except Exception:
        logger.exception(f"Erreur inattendue lors de l'extraction pour le brief {brief_id}")
        return "error occurred"
    


print(extract_voice_over_text(2))