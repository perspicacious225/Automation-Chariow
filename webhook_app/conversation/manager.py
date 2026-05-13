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
        2. Récupérer / créer la conversation
        3. IA active ? Sinon → log et stop
        4. Sauvegarder le message utilisateur
        5. Construire le contexte LLM
        6. Appeler le LLM
        7. Sauvegarder la réponse assistant
        8. Envoyer via WhatsApp
        9. Mettre à jour l'état si nécessaire
        """

        # 1. Idempotence
        if wa_message_id and message_already_exists(wa_message_id):
            logger.info("Message déjà traité, ignoré : %s", wa_message_id)
            return

        # 2. Récupérer / créer la conversation
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

        # 3. IA désactivée → humain en main, on log uniquement
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

        # 4. Sauvegarder le message utilisateur
        save_message(
            conv_id,
            role="user",
            content=text,
            wa_message_id=wa_message_id,
        )

        # 5. Construire le contexte LLM
        history = fetch_history(conv_id)
        context = self.context_builder.build(
            conversation=conversation,
            history=history,
            user_message=text,
        )

        # 6. Appeler le LLM
        try:
            response_text, chunk_ids = self.llm_engine.generate(
                system_prompt=context["system_prompt"],
                messages=context["messages"],
            )
        except Exception as e:
            logger.exception("Erreur LLM pour conversation %s : %s", conv_id, e)
            # Message de fallback en cas d'erreur LLM
            response_text = (
                "Désolé, je rencontre une difficulté technique en ce moment. "
                "Un membre de notre équipe va vous répondre très bientôt. 🙏"
            )
            chunk_ids = []

        # 7. Sauvegarder la réponse assistant
        save_message(
            conv_id,
            role="assistant",
            content=response_text,
            metadata={"chunks_used": chunk_ids},
        )

        # 8. Envoyer via WhatsApp
        try:
            logger.info("Envoi réponse → phone=%s", phone)
            _wa.send_message_direct(chatId=phone, message=response_text, conv_id=conv_id, )
        except Exception as e:
            logger.exception(
                "Erreur envoi WhatsApp pour %s : %s", phone, e
            )

        # 9. Transition d'état
        new_state = self.state_machine.transition(
            current_state=current_state,
            user_message=text,
            assistant_response=response_text,
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