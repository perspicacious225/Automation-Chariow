"""
conversation/manager.py — Orchestrateur des conversations
==========================================================
Point central qui coordonne :
  - La création/récupération du contexte conversationnel
  - La vérification de l'état ai_active
  - La construction du contexte LLM (historique + RAG + transaction)
  - L'appel au moteur LLM
  - La sauvegarde des messages
  - L'envoi de la réponse via WhatsApp
  - Les transitions d'état via la state machine
"""

import logging
from typing import Optional
import re

from webhook_app.database_conv import (
    get_or_create_conversation,
    save_message,
    fetch_history,
    message_already_exists,
    update_conversation_context,
)
from webhook_app.conversation.state_machine import StateMachine
from webhook_app.conversation.context_builder import ContextBuilder
from webhook_app.llm.engine import LLMEngine
from webhook_app.services.whatsapp import WhatsAppService

_wa = WhatsAppService()

logger = logging.getLogger(__name__)


class ConversationManager:
    """
    Orchestrateur principal — une instance par requête entrante.
    """

    def __init__(self):
        self.state_machine = StateMachine()
        self.context_builder = ContextBuilder()
        self.llm_engine = LLMEngine()

    # ──────────────────────────────────────────────────────────────────────
    # POINT D'ENTRÉE PRINCIPAL
    # ──────────────────────────────────────────────────────────────────────

    def handle_incoming(
        self,
        phone: str,
        text: str,
        wa_message_id: str,
        ) -> None:
        """
        Traite un message WhatsApp entrant.

        Flux :
        1. Idempotence — message déjà traité ?
        2. Blacklist — numéro bloqué ?
        3. Récupérer / créer la conversation
        4. IA active ? Sinon → log et stop
        5. Heures d'ouverture — on est ouvert ?
        6. Sauvegarder le message utilisateur
        7. Construire le contexte LLM
        8. Appeler le LLM / escalade immédiate
        9. Sauvegarder la réponse assistant
        10. Envoyer via WhatsApp
        11. Escalade automatique
        12. Transition d'état
        """

        # 1. Idempotence
        if wa_message_id and message_already_exists(wa_message_id):
            logger.info("Message déjà traité, ignoré : %s", wa_message_id)
            return

        # 2. Blacklist
        from webhook_app.database_v21 import (
            is_blacklisted,
            is_business_open,
            log_escalation,
        )
        if is_blacklisted(phone):
            logger.warning("Numéro blacklisté — message ignoré : %s", phone)
            return

        # 3. Récupérer / créer la conversation
        conversation = get_or_create_conversation(
            phone=phone,
            initial_state="new_prospect",
        )
        conv_id = str(conversation["id"])
        current_state = conversation["state"]
        ai_active = conversation["ai_active"]

        logger.info(
            "Conversation %s — state=%s | ai_active=%s",
            conv_id, current_state, ai_active,
        )

        # 4. IA désactivée → humain en main, on log uniquement
        if not ai_active:
            logger.info(
                "IA désactivée sur conversation %s — message loggé sans réponse auto.",
                conv_id,
            )
            save_message(
                conv_id,
                role="user",
                content=text,
                wa_message_id=wa_message_id,
                metadata={"ai_active": False},
            )
            return

        # 5. Heures d'ouverture
        is_open, closed_msg = is_business_open()
        if not is_open:
            logger.info("Hors heures d'ouverture — message automatique envoyé à %s", phone)
            save_message(
                conv_id,
                role="user",
                content=text,
                wa_message_id=wa_message_id,
            )
            save_message(
                conv_id,
                role="assistant",
                content=closed_msg,
                metadata={"source": "business_hours"},
            )
            try:
                _wa.send_message_direct(
                    chatId=phone,
                    message=closed_msg,
                    conv_id=conv_id,
                )
            except Exception as e:
                logger.exception("Erreur envoi message fermé pour %s : %s", phone, e)
            return

        # 6. Sauvegarder le message utilisateur
        save_message(
            conv_id,
            role="user",
            content=text,
            wa_message_id=wa_message_id,
        )

        # 7. Construire le contexte LLM
        history = fetch_history(conv_id)
        context = self.context_builder.build(
            conversation=conversation,
            history=history,
            user_message=text,
        )

        # 8. Escalade immédiate ou appel LLM
        if self.state_machine.should_escalate(text):
            logger.info("Escalade immédiate détectée avant LLM")
            response_text = (
                "Je comprends ta situation. Un membre de notre équipe "
                "va te contacter très rapidement pour résoudre ça. 🙏"
            )
            chunk_ids = []
            escalade_requise = True

        else:
            # Appeler le LLM
            try:
                response_text, chunk_ids = self.llm_engine.generate(
                    system_prompt=context["system_prompt"],
                    messages=context["messages"],
                )
            except Exception as e:
                logger.exception("Erreur LLM pour conversation %s : %s", conv_id, e)
                response_text = (
                    "Désolé, je rencontre une difficulté technique en ce moment. "
                    "Un membre de notre équipe va vous répondre très bientôt. 🙏"
                )
                chunk_ids = []

            # Détecter escalade via tag LLM
            escalade_requise = "[ESCALADE_REQUISE]" in response_text

        logger.info("=== ESCALADE CHECK ===")
        logger.info("response_text brut : %s", response_text[:200])
        logger.info("escalade_requise : %s", escalade_requise)
        logger.info("======================")

        response_clean = re.sub(r'\[ESCALADE_REQUISE\]', '', response_text).strip()

        # 9. Sauvegarder la réponse assistant
        save_message(
            conv_id,
            role="assistant",
            content=response_clean,
            metadata={
                "chunks_used": chunk_ids,
                "escalade": escalade_requise,
            },
        )

        # 10. Envoyer via WhatsApp
        try:
            _wa.send_message_direct(
                chatId=phone,
                message=response_clean,
                conv_id=conv_id,
            )
        except Exception as e:
            logger.exception("Erreur envoi WhatsApp pour %s : %s", phone, e)

        # 11. Escalade automatique + log
        if escalade_requise:
            logger.info("→ Déclenchement _handle_escalade pour conv=%s phone=%s", conv_id, phone)
            # Enregistrer dans escalation_log
            log_escalation(
                conversation_id=conv_id,
                phone=phone,
                trigger_message=text,
                product_id=conversation.get("product_id"),
            )
            _handle_escalade(conv_id, phone, response_clean)
        else:
            logger.info("→ Pas d'escalade détectée")

        # 12. Transition d'état
        new_state = self.state_machine.transition(
            current_state=current_state,
            user_message=text,
            assistant_response=response_clean,
            conversation=conversation,
        )
        if new_state and new_state != current_state:
            from webhook_app.database_conv import update_conversation_state
            update_conversation_state(conv_id, new_state)
            logger.info(
                "Transition état : %s → %s (conversation %s)",
                current_state, new_state, conv_id,
            )

    # ──────────────────────────────────────────────────────────────────────
    # LIAISON AVEC LES WEBHOOKS PAIEMENT (CHARIOW v1)
    # ──────────────────────────────────────────────────────────────────────

    def on_payment_event(
        self,
        phone: str,
        event_type: str,
        sale_id: str,
        product_id: str,
        contact_key: Optional[str] = None,
    ) -> None:
        """
        Appelé par le webhook paiement existant pour synchroniser
        le contexte conversationnel avec l'événement transactionnel.

        event_type : "successful.sale" | "failed.sale" | "abandoned.sale"
        """
        # Mapping événement → état conversationnel
        event_to_state = {
            "successful.sale": "payment_success",
            "failed.sale": "payment_failed",
            "abandoned.sale": "payment_abandoned",
        }
        new_state = event_to_state.get(event_type, "pre_sale")

        # Créer ou récupérer la conversation avec le contexte transactionnel
        conversation = get_or_create_conversation(
            phone=phone,
            contact_key=contact_key,
            product_id=product_id,
            last_sale_id=sale_id,
            initial_state=new_state,
        )

        # Mettre à jour le contexte si la conversation existait déjà
        update_conversation_context(
            str(conversation["id"]),
            product_id=product_id,
            last_sale_id=sale_id,
            contact_key=contact_key,
        )

        # Mettre à jour l'état selon l'événement
        if conversation["state"] != new_state:
            from webhook_app.database_conv import update_conversation_state
            update_conversation_state(str(conversation["id"]), new_state)

        logger.info(
            "Contexte conversationnel mis à jour — phone=%s | event=%s | state=%s",
            phone, event_type, new_state,
        )

def _handle_escalade(conv_id: str, phone: str, last_message: str) -> None:
    """
    Gère l'escalade automatique :
    1. Désactive l'IA sur la conversation
    2. Met l'état en escalation
    3. Notifie l'admin par WhatsApp
    """
    from webhook_app.database_conv import (
        update_conversation_state,
        toggle_ai,
    )
    from webhook_app.services.whatsapp import WhatsAppService
    from webhook_app.config import Config

    # Désactiver l'IA
    toggle_ai(conv_id, False)
    logger.info("IA désactivée — escalade conv=%s", conv_id)

    # Mettre l'état en escalation
    update_conversation_state(conv_id, "escalation")
    logger.info("État → escalation conv=%s", conv_id)

    # Extraire les 4 derniers chiffres pour identification rapide
    phone_digits = phone.replace("@c.us", "").strip()
    short_id = phone_digits[-4:] if len(phone_digits) >= 4 else phone_digits

    # Notification admin
    wa = WhatsAppService()
    notif = (
        f"🚨 *Escalade requise — #{short_id}*\n\n"
        f"Client : {phone_digits}\n"
        f"Dernier message IA : {last_message[:120]}...\n\n"
        f"Commandes disponibles dans la discussion client :\n"
        f"• *#REPRISE* — réactiver l'IA\n"
        f"• *#PAUSE* — garder la main\n"
        f"• *#RESOLU* — résolu + réactiver l'IA"
    )
    wa.send_to_admin(notif, conv_id=conv_id)
    logger.info("Notification escalade envoyée à l'admin")