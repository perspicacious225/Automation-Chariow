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
    webhook_type = payload.get("typeWebhook")
 
    if webhook_type not in ("incomingMessageReceived", "outgoingMessageReceived"):
        logger.debug("Webhook ignoré — type : %s", webhook_type)
        return None
 
    sender_data  = payload.get("senderData") or {}
    message_data = payload.get("messageData") or {}
    text_message = message_data.get("textMessageData") or {}
 
    chat_id = sender_data.get("chatId") or ""
    sender  = sender_data.get("sender") or chat_id
 
    logger.info("DEBUG chatId=%s | sender=%s | type=%s", chat_id, sender, webhook_type)
 
    if "@g.us" in chat_id:
        return None
 
    phone_raw  = chat_id.replace("@c.us", "").strip()
    id_message = payload.get("idMessage") or ""
 
    # ── Extraction texte — textMessage + reply natif ──────────────────────
    extended    = message_data.get("extendedTextMessageData") or {}
    text        = text_message.get("textMessage") or extended.get("text") or ""
 
    quoted      = extended.get("quotedMessage") or {}
    quoted_text = (
        quoted.get("textMessage")
        or (quoted.get("extendedTextMessageData") or {}).get("text")
        or ""
    )
    if quoted_text.strip():
        text = f'[En réponse à : "{quoted_text.strip()[:100]}"]\n{text}'
 
    # ── PATCH : Détection médias (image + document) ───────────────────────
    media       = None
    type_msg    = message_data.get("typeMessage", "")
    file_data   = message_data.get("fileMessageData") or {}
 
    if type_msg == "imageMessage" and file_data.get("downloadUrl"):
        media = {
            "type":      "image",
            "url":       file_data.get("downloadUrl", ""),
            "caption":   (file_data.get("caption") or "").strip(),
            "filename":  file_data.get("fileName") or "image.jpg",
            "mime_type": file_data.get("mimeType") or "image/jpeg",
        }
        # Si pas de texte → utiliser la caption comme texte de base
        if not text and media["caption"]:
            text = media["caption"]
 
    elif type_msg == "documentMessage" and file_data.get("downloadUrl"):
        media = {
            "type":      "document",
            "url":       file_data.get("downloadUrl", ""),
            "caption":   (file_data.get("caption") or "").strip(),
            "filename":  file_data.get("fileName") or "document",
            "mime_type": file_data.get("mimeType") or "application/octet-stream",
        }
        if not text and media["caption"]:
            text = media["caption"]
    # ─────────────────────────────────────────────────────────────────────
 
    # Ignorer si ni texte ni média
    if not text.strip() and not media:
        return None
 
    is_outgoing = webhook_type == "outgoingMessageReceived"
 
    return {
        "phone_raw":     phone_raw,
        "phone":         chat_id,
        "sender":        sender,
        "wa_message_id": id_message,
        "text":          text.strip(),
        "timestamp":     payload.get("timestamp"),
        "is_outgoing":   is_outgoing,
        "media":         media,   # None si pas de média
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



# Tags reconnus — envoyés par l'admin dans la discussion client
ADMIN_TAGS = {
    "#REPRISE": {"ai_active": True,  "state": None},
    "#PAUSE":   {"ai_active": False, "state": None},
    "#RESOLU":  {"ai_active": True,  "state": None},
}


def _parse_admin_command(text: str) -> tuple[str, str | None]:
    """
    Parse le tag et le numéro client optionnel.
    Retourne (tag, phone_client) ou (tag, None)
    """
    parts = text.strip().split()
    tag = parts[0].upper()
    phone = parts[1] if len(parts) > 1 else None
    if phone and not phone.endswith("@c.us"):
        phone = phone + "@c.us"
    return tag, phone

def _is_admin_command(sender: str, text: str) -> bool:
    """
    Vérifie STRICTEMENT que :
    1. Le sender correspond exactement à ADMIN_PHONE
    2. Le texte est un tag reconnu dans ADMIN_TAGS
    """
    admin_phone = Config.ADMIN_PHONE or ""
    if not admin_phone:
        return False

    sender_digits = sender.replace("@c.us", "").strip().lstrip("+")
    admin_digits  = admin_phone.replace("+", "").replace(" ", "").strip()

    is_admin = sender_digits == admin_digits
    is_tag   = text.strip().upper() in ADMIN_TAGS

    if is_tag and not is_admin:
        logger.warning(
            "Tag admin rejeté — sender %s non autorisé (ADMIN_PHONE=%s)",
            sender, admin_phone
        )

    return bool(is_admin and is_tag)

def send_to_admin(self, message: str, conv_id: str | None = None) -> bool:
    admin_phone = Config.ADMIN_PHONE
    if not admin_phone:
        logger.warning("ADMIN_PHONE non configuré — notification admin ignorée")
        return False

    # Utiliser send_message_direct avec le conv_id admin
    # pour bénéficier du cache LID
    chat_id = f"{admin_phone}@c.us"
    return self.send_message_direct(chatId=chat_id, message=message)

def _handle_admin_command(
    phone: str,
    text: str,
    wa_message_id: str,
    chat_id: str,
    is_outgoing: bool = True,
) -> None:
    from webhook_app.database_conv import (
        get_conversation_by_phone,
        toggle_ai,
        update_conversation_state,
    )
    from webhook_app.database_v21 import resolve_escalation
    from webhook_app.services.whatsapp import WhatsAppService

    tag = text.strip().upper()
    action = ADMIN_TAGS.get(tag)
    if not action:
        return

    wa = WhatsAppService()

    # Supprimer le tag uniquement si message sortant
    if is_outgoing:
        wa.delete_message(chat_id, wa_message_id)

    # Trouver la conversation
    conv = get_conversation_by_phone(phone)
    if not conv:
        logger.warning("Commande admin : aucune conversation pour %s", phone)
        wa.send_to_admin(f"⚠️ Aucune conversation trouvée pour {phone}")
        return

    conv_id = str(conv["id"])

    # Appliquer l'action
    toggle_ai(conv_id, action["ai_active"])
    if action["state"]:
        update_conversation_state(conv_id, action["state"])

    # ── Résoudre l'escalade dans l'historique ───────────────
    resolution_map = {
        "#REPRISE": "reprise",
        "#PAUSE":   "pause",
        "#RESOLU":  "resolu",
    }
    resolution = resolution_map.get(tag)
    if resolution:
        resolved = resolve_escalation(conv_id, resolution)
        logger.info(
            "Escalade résolue — conv=%s | resolution=%s | ok=%s",
            conv_id, resolution, resolved,
        )

    # Confirmation à l'admin
    labels = {
        "#REPRISE": "✅ IA réactivée — le bot reprend la conversation",
        "#PAUSE":   "⏸ IA désactivée — vous êtes en main",
        "#RESOLU":  "✅ Résolu — IA réactivée, état → post_sale",
    }
    wa.send_to_admin(labels.get(tag, "Commande appliquée"))
    logger.info("Commande admin %s appliquée sur conv=%s", tag, conv_id)

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

@inbound_bp.route("/webhook/whatsapp/inbound", methods=["POST"])
def whatsapp_inbound():

    raw = request.get_json(silent=True) or {}
    logger.info("=== INBOUND BRUT === %s", raw)

    if not _verify_signature(request):
        logger.warning("Signature webhook invalide — requête rejetée.")
        return jsonify({"status": "unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        logger.warning("Payload JSON vide ou invalide reçu sur /inbound")
        return jsonify({"status": "ignored", "reason": "empty_payload"}), 200

    logger.debug("Webhook inbound reçu : typeWebhook=%s", payload.get("typeWebhook"))

    message = _extract_message(payload)
    if not message:
        return jsonify({"status": "ignored", "reason": "not_text_message"}), 200

    logger.info(
        "Message — phone=%s | wa_id=%s | texte=%s | outgoing=%s",
        message["phone"],
        message["wa_message_id"],
        message["text"][:80],
        message.get("is_outgoing", False),
    )

    # ── Détecter commande admin ──────────────────────────────────
    is_admin_cmd = (
        message.get("is_outgoing")
        and message["text"].strip().upper().startswith("#")
    ) or _is_admin_command(message["sender"], message["text"])

    is_admin_cmd = (
    # Cas 1 : message sortant depuis le téléphone de l'instance (toujours admin)
    message.get("is_outgoing")
    and message["text"].strip().upper().startswith("#")
    and message["text"].strip().upper() in ADMIN_TAGS
    ) or (
        # Cas 2 : message entrant MAIS sender = ADMIN_PHONE configuré
        not message.get("is_outgoing")
        and _is_admin_command(message["sender"], message["text"])
    )

    if is_admin_cmd:
        logger.info("Commande admin détectée : %s → conv %s",
                    message["text"], message["phone"])
        try:
            _handle_admin_command(
                phone=message["phone"],       
                text=message["text"],
                wa_message_id=message["wa_message_id"],
                chat_id=message["phone"],
            )
        except Exception as e:
            logger.exception("Erreur commande admin : %s", e)
        return jsonify({"status": "ok"}), 200

    # ── Ignorer les messages sortants non-commandes ──────────────
    # (réponses IA, relances) — évite les boucles
    if message.get("is_outgoing"):
        logger.debug("Message sortant non-commande ignoré : %s", message["text"][:40])
        return jsonify({"status": "ignored", "reason": "outgoing_non_command"}), 200

    # ── Traitement normal — message client entrant ───────────────
    try:
        manager = ConversationManager()
        manager.handle_incoming(
            phone=message["phone"],
            text=message["text"],
            wa_message_id=message["wa_message_id"],
            media=message.get("media"),
        )
    except Exception as e:
        logger.exception("Erreur handle_incoming pour %s : %s", message["phone"], e)

    return jsonify({"status": "ok"}), 200