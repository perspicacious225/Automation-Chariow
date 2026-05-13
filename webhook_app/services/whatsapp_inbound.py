"""
services/whatsapp_inbound.py — Réception messages WhatsApp entrants
====================================================================
Endpoint Flask qui reçoit les webhooks Green API (messages entrants).
Valide, déduplique, puis délègue au ConversationManager.

Green API envoie un POST à chaque message reçu sur le numéro WhatsApp.
Format du payload documenté : https://green-api.com/docs/receiving/
"""

import logging
import hmac
import hashlib
from flask import Blueprint, request, jsonify, current_app

from webhook_app.config import Config
from webhook_app.conversation.manager import ConversationManager
from webhook_app.services.whatsapp import WhatsAppService


logger = logging.getLogger(__name__)

inbound_bp = Blueprint("whatsapp_inbound", __name__)


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _extract_message(payload: dict) -> dict | None:
    """
    Extrait les champs utiles d'un payload Green API.

    Green API envoie différents types de notifications :
      - typeWebhook: "incomingMessageReceived" → message texte entrant
      - typeWebhook: "outgoingMessageStatus"   → statut message sortant (ignorer)
      - typeWebhook: "stateInstanceChanged"    → changement état instance (ignorer)

    Retourne un dict normalisé ou None si le payload n'est pas un message texte.
    """
    webhook_type = payload.get("typeWebhook")

    if webhook_type != "incomingMessageReceived":
        logger.debug("Webhook ignoré — type : %s", webhook_type)
        return None

    sender_data = payload.get("senderData") or {}
    message_data = payload.get("messageData") or {}
    text_message = message_data.get("textMessageData") or {}

    chat_id = sender_data.get("chatId") or ""
    sender = sender_data.get("sender") or chat_id

    # AJOUTER CETTE LIGNE
    logger.info("DEBUG chatId=%s | sender=%s", chat_id, sender)

    # Ignorer les messages de groupe (@g.us)
    if "@g.us" in chat_id:
        logger.debug("Message de groupe ignoré : %s", chat_id)
        
        return None

    # Extraire le numéro propre (sans @c.us)
    phone_raw = chat_id.replace("@c.us", "").strip()

    # ID unique du message Green API
    id_message = payload.get("idMessage") or ""

    # Contenu texte
    text = text_message.get("textMessage") or ""

    # Ignorer les messages vides ou non-texte
    if not text.strip():
        logger.debug("Message non-texte ou vide ignoré (idMessage: %s)", id_message)
        
        return None

    return {
        "phone_raw": phone_raw,
        "phone": chat_id,  # ← chatId COMPLET avec @c.us — bypass normalisation
        "sender": sender,
        "wa_message_id": id_message,
        "text": text.strip(),
        "timestamp": payload.get("timestamp"),
    }



# def _normalize_phone(phone_raw: str) -> str:
#     """
#     Green API envoie les numéros CI sans le 0 local.
#     22589333113 (11 chiffres) → 2250789333113 (12 chiffres)
#     """
#     digits = phone_raw.strip()

#     return digits


def _verify_signature(request_obj) -> bool:
    """
    Vérifie la signature HMAC du webhook Green API si une clé est configurée.
    Retourne True si pas de clé configurée (mode dev) ou si signature valide.
    """
    secret = getattr(Config, "GREEN_API_WEBHOOK_SECRET", None)
    if not secret:
        return True  # Pas de vérification en mode dev

    signature = request_obj.headers.get("X-Green-Api-Signature", "")
    body = request_obj.get_data()
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@inbound_bp.route("/webhook/whatsapp/inbound", methods=["POST"])
def whatsapp_inbound():
    """
    Reçoit les webhooks Green API pour les messages entrants.
    Répond immédiatement 200 OK à Green API, puis traite en synchrone.
    """
    # Vérification signature
    if not _verify_signature(request):
        logger.warning("Signature webhook invalide — requête rejetée.")
        return jsonify({"status": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        logger.warning("Payload JSON vide ou invalide reçu sur /inbound")
        return jsonify({"status": "ignored", "reason": "empty_payload"}), 200

    logger.debug("Webhook inbound reçu : typeWebhook=%s", payload.get("typeWebhook"))

    # Extraction et normalisation du message
    message = _extract_message(payload)
    logger.info("Payload inbound complet : %s", payload)
    if not message:
        # Pas un message texte entrant — on acquitte sans traiter
        return jsonify({"status": "ignored", "reason": "not_text_message"}), 200

    logger.info(
        "Message entrant — phone=%s | wa_id=%s | texte=%s",
        message["phone"],
        message["wa_message_id"],
        message["text"][:80],
    )

    # Traitement par le ConversationManager
    try:
        manager = ConversationManager()
        manager.handle_incoming(
            phone=message["phone"],
            text=message["text"],
            wa_message_id=message["wa_message_id"],
        )
    except Exception as e:
        # On log l'erreur mais on retourne 200 à Green API
        # pour éviter les renvois en boucle
        logger.exception("Erreur dans handle_incoming pour %s : %s", message["phone"], e)

    return jsonify({"status": "ok"}), 200