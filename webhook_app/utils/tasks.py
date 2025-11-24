import os
import logging
from celery import shared_task
from google import genai
from elevenlabs.client import ElevenLabs


from webhook_app.utils.utils_automat_vid import extract_voice_over_text, upload_audio_to_s3 
from webhook_app.database_pg import get_brief_by_id, update_brief_audio_url_and_status, update_brief_script_and_status
from psycopg2 import OperationalError

#Configuration des clients API
logger = logging.getLogger(__name__)

try:

    # genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
    gemini_client = genai.Client()
    logger.info("🤖 Client Google Gemini initialisé.")
except Exception as e:
    gemini_client = None
    logger.error(f"❌ Erreur d'initialisation du client Gemini : {e}")

try:
    # Configuration ElevenLabs
    elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    logger.info("🎤 Client ElevenLabs initialisé.")
except Exception as e:
    elevenlabs_client = None
    logger.error(f"❌ Erreur d'initialisation du client ElevenLabs : {e}")


# TÂCHE 1 : GÉNÉRATION DU SCRIPT
@shared_task(bind=True, max_retries=3, default_retry_delay=5, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=300, retry_jitter=True, time_limit=600, soft_time_limit=55)
def generate_video_script(self, brief_id: int):
    """
    Étape 1 du flux : Génère le script vidéo à partir d'un brief.
    Retourne le brief_id en cas de succès pour le chaînage.
    """

    logger.info(f"--- DÉMARRAGE TÂCHE SCRIPT (Brief ID: {brief_id}) ---")

    # Vérification des clients API
    if not gemini_client:
        error_msg = "Client Gemini non initialisé. Vérifiez la clé API."
        logger.error(error_msg)
        update_brief_script_and_status(brief_id, status='failed', error_message=error_msg)
        raise Exception(error_msg)


    try:
    
        brief_data = get_brief_by_id(brief_id)

        if not brief_data:

                error_msg = f"Brief ID {brief_id} non trouvé dans la base de données. Annulation de la tâche.."
                logger.error(error_msg)
                update_brief_script_and_status(brief_id, status='failed', error_message='Brief not found')
                raise
    except Exception as e:
        error_msg = f"Erreur DB lors de la lecture du brief : {e}"
        logger.exception(error_msg)
        update_brief_script_and_status(brief_id, status='failed', error_message=error_msg)
        raise


        
    prompt = f"""
            **Rôle :** Tu es un expert en copywriting spécialisé dans la création de scripts vidéo publicitaires ultra-courts (moins de 15 secondes) et à haute conversion pour Meta (Facebook & Instagram Reels/Stories). 
            Tu penses "mobile-first" et tes scripts sont compréhensibles même sans le son.

            **Tâche :** Écris un script vidéo publicitaire percutant basé sur les informations suivantes :

            * **Persona Ciblée :** {brief_data['persona']}
            * **Désir Profond Visé :** {brief_data['desire']}
            * **Niveau de Conscience :** {brief_data['awareness']}
            * **Angle d'Attaque / Message Clé :** {brief_data['angle']}
            * **Idées Visuelles Suggérées (optionnel) :** {brief_data.get('visual_instructions', 'Aucune instruction visuelle spécifique.')}

            **Contraintes et Bonnes Pratiques IMPÉRATIVES :**
            1.  **Durée Totale :** Moins de 15 secondes.
            2.  **Accroche (Hook) :** Les 3 PREMIÈRES secondes doivent capter l'attention IMMÉDIATEMENT (utiliser une question, un fait surprenant, une interpellation directe).
            3.  **Mobile & Sans Son :** Le message principal doit passer VISUELLEMENT. Inclus des suggestions claires pour le TEXTE À L'ÉCRAN (grand, lisible, concis).
            4.  **Structure :** Suivre approximativement : Accroche (0-3s) -> Problème/Solution Rapide (3-10s) -> Appel à l'Action Clair (10-15s).
            5.  **Appel à l'Action (CTA) :** Doit être direct, orienté bénéfice (ex: "Débloquez...", "Commencez...", "Accès immédiat...").

            **Format de Sortie Attendu (IMPORTANT - suis ce format) :**
            Fournis le script sous forme de scènes numérotées. Pour chaque scène, indique :
            * **[VISUEL] :** Description courte de ce qu'on voit à l'écran.
            * **[VOIX OFF] :** Le texte de la voix off (si pertinent, mais le visuel prime).
            * **[TEXTE ÉCRAN] :** Le texte clé affiché à l'écran (TRÈS IMPORTANT).

            **Exemple de format pour une scène :**
            1.  **[VISUEL] :** Gros plan sur un visage stressé regardant un écran d'ordinateur chaotique.
                **[VOIX OFF] :** (Voix calme) Vous vous sentez dépassé par vos projets ?
                **[TEXTE ÉCRAN] :** PROJETS EN CHAOS ?

            Ne génère que le script, sans introduction ni conclusion supplémentaire. Commence directement par la scène 1.
            """
    

    try:
        logger.info(f"Envoi du prompt à l'API Gemini pour le brief {brief_id}...")
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        
        if not response.parts:
             raise ValueError("La réponse de l'API est vide ou a été bloquée.")
        
        # print(response.text)
        generated_script = response.text
        logger.info(f"Script reçu de Gemini pour le brief {brief_id}.")
        
        update_brief_script_and_status(brief_id, script=generated_script, status="script_generated")
        logger.info(f"Script sauvegardé et statut mis à jour pour le brief {brief_id}.")

    except Exception as e:
        error_msg = f"Échec de la génération/sauvegarde du script : {type(e).__name__}: {e}"
        logger.exception(error_msg)
        update_brief_script_and_status(brief_id, status='failed', error_message=error_msg)
        raise # Relance l'erreur

    logger.info(f"--- FIN TÂCHE SCRIPT (Brief ID: {brief_id}) ---")
    return brief_id

# TÂCHE 2 : GÉNÉRATION DE L'AUDIO

@shared_task(bind=True, max_retries=3, default_retry_delay=5, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=300, retry_jitter=True, time_limit=600, soft_time_limit=55)
def generate_audio_task(self, brief_id):
    """
        Étape 2 du flux : Génère l'audio à partir du script, l'uploade vers S3.
        Reçoit brief_id de la tâche précédente.
    """
    logger.info(f"--- DÉMARRAGE TÂCHE AUDIO (Brief ID: {brief_id}) ---")


    # Vérification des clients API
    if not elevenlabs_client:
        error_msg = "Client ElevenLabs non initialisé. Vérifiez la clé API."
        logger.error(error_msg)
        update_brief_script_and_status(brief_id, status='failed', error_message=error_msg)
        raise Exception(error_msg)

    try:
        # Extraire le texte à convertir
        text_to_convert = extract_voice_over_text(brief_id)
        if not text_to_convert:
            raise ValueError("Extraction du texte VOIX OFF échouée ou texte vide.")
        
        
         # Generer l'audio
        else:
            logger.info(f"Extraction du texte VOIX OFF (Brief {brief_id}): {text_to_convert[:50]}...")
            audio_stream = elevenlabs_client.text_to_speech.convert(
                text=text_to_convert,
                voice_id="JBFqnCBsd6RMkjVDRZzb",
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128"
            )
            audio_bytes = b"".join(audio_stream)

            # Uploader vers aws S3
            s3_filename = f"audio_briefs/brief_{brief_id}.mp3"
            uploaded_key = upload_audio_to_s3(
                audio_data=audio_bytes,
                s3_filename=s3_filename
            )

        if not uploaded_key:
            raise Exception("L'upload vers AWS S3 a échoué.")
        
        logger.info(f"Upload S3 réussi ! Clé: {uploaded_key}")

        update_brief_audio_url_and_status(brief_id, generated_audio_url=uploaded_key, status='audio_generated')
        logger.info(f"URL audio sauvegardée et statut mis à jour pour le brief {brief_id}.")

        
    except Exception as e:
        error_msg = f"Échec de la génération/upload de l'audio : {type(e).__name__}: {e}"
        logger.exception(error_msg)
        update_brief_script_and_status(brief_id, status='failed', error_message=error_msg)
        raise
    logger.info(f"--- FIN TÂCHE AUDIO (Brief ID: {brief_id}) ---")
    return brief_id


@shared_task(bind=True, max_retries=3, default_retry_delay=5, autoretry_for=(OperationalError,), retry_backoff=True, retry_backoff_max=300, retry_jitter=True, time_limit=600, soft_time_limit=55)
def generate_video_task(self):

    pass