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

import re as _re

from webhook_app.conversation.state_machine import StateMachine
from webhook_app.conversation.context_builder import ContextBuilder
from webhook_app.llm.engine import LLMEngine
from webhook_app.services.whatsapp import WhatsAppService
from webhook_app.database_pg import get_connection, execute_with_retry

from webhook_app.conversation.output_parser import parse_llm_output, validate_output



from webhook_app.database_conv import (
    get_or_create_conversation,
    save_message,
    fetch_history,
    toggle_ai,
    update_message_send_status,
    update_conversation_state,
    update_conversation_context,
)
from webhook_app.database_v21 import (
    is_blacklisted,
    is_business_open,
    log_escalation,
    find_sale_by_identifier,
    save_escalation_summary
)


_wa = WhatsAppService()

logger = logging.getLogger(__name__)


def _convert_markdown_to_whatsapp(text: str) -> str:
    """
    Convertit le formatage Markdown du LLM vers le formatage natif WhatsApp.

    Markdown (LLM)     WhatsApp
    **texte**       →  *texte*   (gras)
    *texte*         →  _texte_   (italique)
    **url**         →  url       (supprimer ** autour des URLs)
    `url`           →  url       (supprimer backticks autour des URLs)
    —               →  :         (tiret long → deux-points)
    """

    # Blocs complets <tag>...</tag>
    text = _re.sub(r'<[a-zA-Z][^>]*>.*?</[a-zA-Z][^>]*>', '', text, flags=_re.DOTALL)

    text = _re.sub(r'</?[a-zA-Z][^>]{0,50}>', '', text)
    # Marqueurs internes LLM
    text = _re.sub(r'\[CDD_PHASE:[^\]]*\]', '', text)
    text = _re.sub(r'\[RÉSULTAT VÉRIFICATION[^\]]*\]', '', text, flags=_re.IGNORECASE)
    text = _re.sub(r'\[MÉDIA REÇU[^\]]*\]', '', text, flags=_re.IGNORECASE)
    text = _re.sub(r'\[DOCUMENT REÇU[^\]]*\]', '', text, flags=_re.IGNORECASE)
    #Balise XML résiduelle collée à une lettre → contenu suspect → vider
    if _re.search(r'</?[a-zA-Z]', text):
        logger.error(
            "SÉCURITÉ — balise XML résiduelle après nettoyage — message vidé : %s",
            text[:100],
        )
        text = ""


    # Nettoyer les ** ou __ autour des URLs 
    text = _re.sub(r'\*{1,2}(https?://[^\s*]+)\*{1,2}', r'\1', text)
    text = _re.sub(r'_{1,2}(https?://[^\s_]+)_{1,2}', r'\1', text)
    text = _re.sub(r'`(https?://[^\s`]+)`', r'\1', text)

    # Gras : **texte** → *texte* 
    # Le marqueur §§ évite les collisions avec l'italique WhatsApp
    text = _re.sub(r'\*\*(.+?)\*\*', r'§§\1§§', text)

    # Italique : *texte* → _texte_ 
    text = _re.sub(r'\*(.+?)\*', r'_\1_', text)

    # Convertir marqueur temporaire en gras WhatsApp 
    text = _re.sub(r'§§(.+?)§§', r'*\1*', text)

    #Supprimer backticks résiduels 
    text = _re.sub(r'`([^`]+)`', r'\1', text)

    # ── Étape 6 — Fix tiret long (—)
    text = text.replace('\u2014', '—')   
    text = _re.sub(r'\s—\s', ' : ', text)   
    text = _re.sub(r'—\s', ': ', text)       
    text = _re.sub(r'\s—', ',', text)        
    text = text.replace('—', ' ')        

    # Effacer fragments de checklist hallucinés
    text = _re.sub(r'\[\s?[xX ]\s?\]', '', text)
    text = _re.sub(r'COMPOSANTS OBLIGATOIRES.*?:?', '', text, flags=_re.IGNORECASE)
    text = _re.sub(r'INTERDITS ABSOLUS.*?:?', '', text, flags=_re.IGNORECASE)
    text = _re.sub(r'MODE:.*?\n', '', text, flags=_re.IGNORECASE)

    text = _re.sub(r' {2,}', ' ', text)   
    text = _re.sub(r'\n{3,}', '\n\n', text)

    text = _re.sub(r'^.*?</thinking>', '', text, flags=_re.DOTALL | _re.IGNORECASE).strip()
    text = text.strip()

    return text



# States réservés aux webhooks Fedapay — jamais via LLM
WEBHOOK_ONLY_STATES = {"payment_failed", "payment_abandoned", "payment_success"}

# States nécessitant un paiement vérifié pour la transition
PAYMENT_REQUIRED_STATES = {"post_sale", "support"}


# WEBHOOK_ONLY_STATES    = {"payment_failed", "payment_abandoned", "payment_success"}
# PAYMENT_REQUIRED_STATES = {"post_sale", "support"}

def _is_payment_verified(conversation: dict) -> bool:
    """
    Vérification paiement en 4 niveaux par priorité.

    Niveau 1 : last_sale_id → completed direct
    Niveau 2 : email connu → completed (même email, numéro différent)
    Niveau 3 : phone connu → completed (même numéro, email différent)
    Niveau 4 : échec → False → LLM demandera email/numéro manuellement

    Si un niveau 2 ou 3 trouve un paiement completed :
    → Met à jour last_sale_id dans conversations automatiquement
    """
    last_sale_id = conversation.get("last_sale_id")
    product_id   = conversation.get("product_id")
    conv_id      = str(conversation.get("id", ""))
    phone_raw    = conversation.get("phone", "").replace("+", "").replace("@c.us", "")
    phone_suffix = phone_raw[-8:] if len(phone_raw) >= 8 else phone_raw

    try:
        
        from webhook_app.database_conv import update_conversation_context

        with get_connection(readonly=True) as conn:

            # ── Niveau 1 — last_sale_id direct ────────────────────────────────
            if last_sale_id:
                row = execute_with_retry(
                    conn,
                    "SELECT status FROM fact_sales WHERE sale_id = %s LIMIT 1",
                    (last_sale_id,),
                    fetch="one"
                )
                if row and row["status"] == "completed":
                    logger.debug(
                        "_is_payment_verified L1 : sale_id=%s → completed ✅",
                        last_sale_id,
                    )
                    return True
                logger.debug(
                    "_is_payment_verified L1 : sale_id=%s → %s — passage L2",
                    last_sale_id,
                    row["status"] if row else "not_found",
                )

            # Récupérer l'email depuis le last_sale_id pour le Niveau 2
            known_email = None
            if last_sale_id:
                email_row = execute_with_retry(
                    conn,
                    "SELECT email FROM fact_sales WHERE sale_id = %s LIMIT 1",
                    (last_sale_id,),
                    fetch="one"
                )
                if email_row:
                    known_email = email_row.get("email")

            # ── Niveau 2 — email connu (priorité haute) ───────────────────────
            # Cas fréquent : même email, numéro de paiement différent (Orange → MTN)
            if known_email and product_id:
                row = execute_with_retry(
                    conn,
                    """
                    SELECT sale_id FROM fact_sales
                    WHERE email = %s
                    AND product_id = %s
                    AND status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (known_email, product_id),
                    fetch="one"
                )
                if row:
                    new_sale_id = row["sale_id"]
                    logger.info(
                        "_is_payment_verified L2 : email=%s → completed "
                        "sale_id=%s ✅ — update last_sale_id",
                        known_email, new_sale_id,
                    )
                    if conv_id:
                        update_conversation_context(
                            conv_id, last_sale_id=new_sale_id
                        )
                        conversation["last_sale_id"] = new_sale_id
                    return True
                logger.debug(
                    "_is_payment_verified L2 : email=%s → pas de completed — passage L3",
                    known_email,
                )

            # ── Niveau 3 — phone connu ────────────────────────────────────────
            # Cas : même numéro WhatsApp, email différent lors du retry
            if phone_suffix and product_id:
                row = execute_with_retry(
                    conn,
                    """
                    SELECT sale_id FROM fact_sales
                    WHERE phone LIKE %s
                    AND product_id = %s
                    AND status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (f"%{phone_suffix}", product_id),
                    fetch="one"
                )
                if row:
                    new_sale_id = row["sale_id"]
                    logger.info(
                        "_is_payment_verified L3 : phone suffix=%s → completed "
                        "sale_id=%s ✅ — update last_sale_id",
                        phone_suffix, new_sale_id,
                    )
                    if conv_id:
                        update_conversation_context(
                            conv_id, last_sale_id=new_sale_id
                        )
                        conversation["last_sale_id"] = new_sale_id
                    return True
                logger.debug(
                    "_is_payment_verified L3 : phone=%s → pas de completed — L4",
                    phone_suffix,
                )

            # ── Niveau 4 — échec total ────────────────────────────────────────
            # Le LLM va demander email/numéro manuellement via [VERIFY_PAYMENT]
            logger.info(
                "_is_payment_verified : L1+L2+L3 échoués → False "
                "(conv=%s product=%s)",
                conv_id, product_id,
            )
            return False

    except Exception as e:
        logger.warning("_is_payment_verified — erreur DB : %s", e)
        return False

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
        user_message_saved: bool = False,
        dry_run: bool = False,
        media: dict | None = None, 
        ) -> str | None:

        """
        Traite un message WhatsApp entrant.

        Flux :
        1. Idempotence — message déjà traité ?
        2. Blacklist — numéro bloqué ?
        3. Récupérer / créer la conversation
        4. IA active ? Sinon -> log et stop
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
        if not user_message_saved:
            if wa_message_id and message_already_exists(wa_message_id):
                logger.info("Message déjà traité, ignoré : %s", wa_message_id)
                return

        # 2. Blacklist
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

        # 4. IA désactivée -> humain en main, on log uniquement
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


            if not dry_run:
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
        if not user_message_saved:
            save_message(conv_id, role="user", content=text, wa_message_id=wa_message_id)

        # ── Détection email pour vérification paiement ────────────────
        email_pattern = _re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        email_match = email_pattern.search(text)
        context_note = ""

        # Vérifier si le sale existant correspond au produit en discussion
        current_product = conversation.get("product_id")
        current_sale_id = conversation.get("last_sale_id")

        sale_matches_product = False
        if current_sale_id and current_product:
            try:
                
                with get_connection(readonly=True) as conn:
                    row = execute_with_retry(
                        conn,
                        "SELECT product_id FROM fact_sales WHERE sale_id = %s LIMIT 1",
                        (current_sale_id,),
                        fetch="one",
                    )
                    if row:
                        sale_matches_product = row["product_id"] == current_product
            except Exception as e:
                logger.warning("Vérification sale_matches_product échouée : %s", e)

        needs_verification = (
            email_match
            and conversation.get("state") not in ("post_sale", "payment_success", "new_prospect")
            and (
                not current_sale_id          
                or not sale_matches_product  
            )
        )

        if needs_verification:
            identifier = email_match.group(0)
            logger.info("Email détecté en contexte vérification : %s", identifier)

            current_product = conversation.get("product_id")
            sale = find_sale_by_identifier(identifier, product_id=current_product)


            if sale:
                status = sale.get("status", "")
                update_conversation_context(conv_id, last_sale_id=sale["sale_id"], product_id=sale["product_id"])
                status_to_state = {
                    "completed": "payment_success",
                    "failed":    "payment_failed",
                    "abandoned": "payment_abandoned",
                }
                new_state = status_to_state.get(status)
                if new_state:
                    update_conversation_state(conv_id, new_state)
                    conversation["state"] = new_state
                conversation["last_sale_id"] = sale["sale_id"]
                conversation["product_id"]   = sale["product_id"]
                context_note = (
                    f"\n[RÉSULTAT VÉRIFICATION] Paiement '{status}' trouvé "
                    f"pour {sale.get('product_name', sale.get('product_id'))}. "
                    f"Adapte ta réponse selon le statut."
                )
            else:
                context_note = (
                    f"\n[RÉSULTAT VÉRIFICATION] Aucun paiement trouvé pour '{identifier}'. "
                    f"Dis honnêtement qu'aucune trace n'existe et propose le lien "
                    f"de finalisation ou demande un autre identifiant."
                )
        logger.info(
            "VERIFY CHECK — email_match=%s | last_sale_id=%s | state=%s | needs_verification=%s",
            email_match.group(0) if email_match else None,
            conversation.get("last_sale_id"),
            conversation.get("state"),
            needs_verification,
        )


        # SÉPARATION INTENTION VS ACQUISITION
        current_product = conversation.get("product_id")          # Produit Acquis/Vérifié
        target_product = conversation.get("target_product_id")    # Intention (Catalogue)
        
        # Le RAG et le LLM doivent se baser sur le produit actif (Acquis en priorité, sinon Intention)
        active_product = current_product if current_product else target_product

        if not active_product:

            from webhook_app.database_conv import get_mini_catalogue_text
            catalogue_text = get_mini_catalogue_text()
            context_note += f"\n\n{catalogue_text}"
            logger.info("Mode Juge activé — Injection du Catalogue dans le contexte.")

        # HACK ÉLÉGANT : On simule temporairement le product_id pour que ton 
        temp_real_product = conversation.get("product_id")
        conversation["product_id"] = active_product

        # ── Traitement média (image / document)
        processed_media = None
        if media:
            try:
                from webhook_app.services.media_processor import (
                    process_media,
                    build_media_context_note,
                )
                processed_media = process_media(media)
                logger.info(
                    "Média traité — status=%s | fichier=%s",
                    processed_media.get("status"), processed_media.get("filename"),
                )
                # Pour les cas non-vision → injecter dans context_note (texte)
                if processed_media.get("status") != "image_ok":
                    context_note += build_media_context_note(processed_media)
                    processed_media = None   # pas d'injection multimodale nécessaire
            except Exception as e:
                logger.warning("Traitement média échoué (non bloquant) : %s", e)
                processed_media = None

        # 7. Construire le contexte LLM — toujours exécuté

        user_message_for_llm = (text + context_note).strip()
        if not user_message_for_llm and media:
            user_message_for_llm = "[Image reçue]"

        history = fetch_history(conv_id)
        context = self.context_builder.build(
            conversation=conversation,
            history=history,
            user_message=user_message_for_llm,
        )

        if processed_media and processed_media.get("status") == "image_ok":
            messages = context["messages"]
            if messages and messages[-1]["role"] == "user":
                last_text = messages[-1]["content"]
                # Construire le contenu multimodal Anthropic
                messages[-1]["content"] = [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": processed_media["mime_type"],
                            "data":       processed_media["data"],
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            last_text if last_text and last_text.strip() not in ("", "[Image reçue]") else "Le client a envoyé cette image sans légende."),
                    },
                ]
                logger.info("Image injectée en multimodal dans le dernier message user")
        
        # On restaure la stricte vérité (Acquis ou None)
        conversation["product_id"] = temp_real_product
        # ────────────────────────────────────────────────────────────────────

        use_cache = len(history) >= 2
    
        # Appel LLM direct — le modèle juge seul si escalade nécessaire

        # # === X-RAY DEBUG : CE QUE LE LLM LIT ===
        # print(f"\n{'-'*40}\n🔍 [X-RAY INPUT] DYNAMIC CONTEXT (XML & RAG/CATALOGUE)\n{'-'*40}")
        # print(context.get("system_prompt", "")) 
        # print(f"{'-'*40}\n")

        try:
            response_text, chunk_ids = self.llm_engine.generate(
                system_prompt=context["system_prompt"],
                messages=context["messages"],
                dynamic_context=context.get("dynamic_context", ""),
                use_cache=use_cache,
            )
        except Exception as e:
            logger.exception("Erreur LLM pour conversation %s : %s", conv_id, e)
            # response_text = (
            #     "Désolé, je rencontre une difficulté technique en ce moment. "
            #     "Un membre de notre équipe va vous répondre très bientôt. 🙏"
            # )
            response_text = ()
            chunk_ids = []

       
        # Escalade détectée via support_status: exhausted 

        escalade_requise = False  # mise a jour apres parsing

        # ── Détecter tag VERIFY_PAYMENT ───────────────────────────────
    
        verify_match = _re.search(r'\[VERIFY_PAYMENT:([^\]]+)\]', response_text)

        if verify_match:
            identifier = verify_match.group(1).strip()
            logger.info("VERIFY_PAYMENT déclenché : %s", identifier)

            # Stripper le tag avant tout traitement
            response_text = _re.sub(r'\[VERIFY_PAYMENT:[^\]]+\]', '', response_text).strip()

            # Recherche dans fact_sales avec vérification croisée produit

            current_product = conversation.get("product_id")
            sale = find_sale_by_identifier(identifier, product_id=current_product)

            if sale:
                status       = sale.get("status", "")
                found_product = sale.get("product_id")

                # Mettre à jour la conversation avec les vraies données
                status_to_state = {
                    "completed": "payment_success",
                    "failed":    "payment_failed",
                    "abandoned": "payment_abandoned",
                }
                new_state = status_to_state.get(status)
                update_conversation_context(
                    conv_id,
                    last_sale_id=sale["sale_id"],
                    product_id=found_product,
                )
                if new_state:
                    update_conversation_state(conv_id, new_state)
                    conversation["state"]    = new_state
                conversation["last_sale_id"] = sale["sale_id"]
                conversation["product_id"]   = found_product

                # Cas produit différent de celui en discussion
                if current_product and found_product != current_product:
                    context_note = (
                        f"\n[RÉSULTAT VÉRIFICATION] Paiement '{status}' trouvé "
                        f"pour {sale.get('product_name', found_product)} — "
                        f"différent du produit en discussion ({current_product}). "
                        f"Informe le client et adapte ta réponse."
                    )
                else:
                    context_note = (
                        f"\n[RÉSULTAT VÉRIFICATION] Paiement '{status}' trouvé "
                        f"pour {sale.get('product_name', found_product)}. "
                        f"Adapte ta réponse selon le statut."
                    )
            else:
                context_note = (
                    f"\n[RÉSULTAT VÉRIFICATION] Aucun paiement trouvé "
                    f"pour '{identifier}' "
                    f"sur le produit ({current_product or 'inconnu'}). "
                    f"Demande un autre identifiant (email ou téléphone) "
                    f"ou oriente vers l'achat si aucune trace."
                )
                logger.info("VERIFY_PAYMENT : aucune transaction pour %s", identifier)


            # Régénérer la réponse avec le vrai résultat
            temp_real_product2 = conversation.get("product_id")
            active_product2 = temp_real_product2 if temp_real_product2 else conversation.get("target_product_id")
            conversation["product_id"] = active_product2

            context2 = self.context_builder.build(
                conversation=conversation,
                history=history,
                user_message=text + context_note,
            )
            
            conversation["product_id"] = temp_real_product2
            # ─────────────────────────────────────────────
            
            response_text, chunk_ids = self.llm_engine.generate(
                system_prompt=context2["system_prompt"],
                messages=context2["messages"],
            )

            # Stripper le tag si le LLM le régénère
            response_text = _re.sub(r'\[VERIFY_PAYMENT:[^\]]+\]', '', response_text).strip()

        #Parser XML

        output = parse_llm_output(response_text, current_state)
        output = validate_output(output, current_state, phone=phone, conversation=conversation)

        produit_deduit = getattr(output, "produit_cible", "inconnu").strip()
        if not conversation.get("product_id") and not conversation.get("target_product_id") and produit_deduit.lower() != "inconnu" and produit_deduit:
            logger.info("[Découverte] Produit d'intention verrouillé par l'IA : %s", produit_deduit)

            conversation["target_product_id"] = produit_deduit
            update_conversation_context(conv_id, target_product_id=produit_deduit)

        # === X-RAY DEBUG : CE QUE LE LLM PENSE ===
        # print(f"\n{'-'*40}\n🧠 [X-RAY OUTPUT] DECISION DU LLM\n{'-'*40}")
        # print(f"Type: {output.decision_type}")
        # print(f"Produit_cible: {getattr(output, 'produit_cible', 'inconnu')}") 
        # print(f"Message: {output.message}")
        # print(f"{'-'*40}\n")
        

        # Escalade pilotée par support_status: exhausted
        escalade_requise = output.escalade_signal

        if output.validation_notes:
            logger.info(
                "Parser validation — conv=%s notes=%s",
                conv_id, " | ".join(output.validation_notes),
            )

        response_clean = _convert_markdown_to_whatsapp(output.message)

        # 9. Sauvegarder la réponse assistant
        save_message(
            conv_id,
            role="assistant",
            content=response_clean,
            metadata={
                "chunks_used":      chunk_ids,
                "escalade":         escalade_requise,
                "decision_type":    output.decision_type,
                "strategie":        output.decision_strategie or None, 
                "contraintes":      output.decision_contraintes or None,
                "support_status":   output.decision_support_status or None,
                "state_sortie":     output.state,
                "parser_valid":     output.is_valid,
                "parser_notes":     output.validation_notes or None,
            },
        )

        reading_delay, typing_delay = WhatsAppService.calculate_delays(
            incoming=text,
            outgoing=response_clean,
        )


        try:
            sent = _wa.send_message_direct(
                chatId=phone,
                message=response_clean,
                conv_id=conv_id,
                reading_delay=reading_delay,
                typing_delay=typing_delay,
            )

        except Exception as e:
            logger.exception("Erreur envoi WhatsApp pour %s : %s", phone, e)

        try: 
            update_message_send_status(
                wa_message_id,
                "sent" if sent else "failed",
            )

        except Exception as e:
            logger.exception("Erreur de sauvegarde du message pour %s : %s", phone, e)

        # 11. Escalade automatique + log
        if escalade_requise:
            logger.info("-> Déclenchement _handle_escalade pour conv=%s phone=%s", conv_id, phone)
            # Enregistrer dans escalation_log
            log_escalation(
                conversation_id=conv_id,
                phone=phone,
                trigger_message=text,
                product_id=conversation.get("product_id"),
            )
            _handle_escalade(conv_id, phone, response_clean)
        else:
            logger.info("-> Pas d'escalade détectée")



        # 12. ── Transition d'état
 
        if output.state != current_state:
            update_conversation_state(conv_id, output.state)
            logger.info(
                "Transition : %s → %s (conversation %s)",
                current_state, output.state, conv_id,
            )
        else:
            logger.info(
                "State maintenu : %s (conversation %s)",
                current_state, conv_id,
            )

        return  response_clean


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
        # Mapping événement -> état conversationnel
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
    3. Génère un résumé de la conversation via Claude
    4. Sauvegarde le résumé dans escalation_log
    5. Notifie l'admin par WhatsApp
    """
    from webhook_app.services.whatsapp import WhatsAppService
    from webhook_app.config import Config

    # 1. Désactiver l'IA
    toggle_ai(conv_id, False)
    logger.info("IA désactivée — escalade conv=%s", conv_id)

    # 2. Mettre l'état en escalation
    update_conversation_state(conv_id, "escalation")
    logger.info("État -> escalation conv=%s", conv_id)

    # 3. Générer le résumé via Claude
    summary = _generate_escalation_summary(conv_id, last_message)
    if summary:
        save_escalation_summary(conv_id, summary)
        logger.info("Résumé escalade sauvegardé pour conv=%s", conv_id)

    # 4. Extraire les 4 derniers chiffres pour identification rapide
    phone_digits = phone.replace("@c.us", "").strip()
    short_id = phone_digits[-4:] if len(phone_digits) >= 4 else phone_digits

    # 5. Notification admin avec résumé
    wa = WhatsAppService()
    notif = (
        f"🚨 *Escalade requise — #{short_id}*\n\n"
        f"Client : {phone_digits}\n"
        f"Dernier message : {last_message[:120]}\n\n"
    )

    if summary:
        notif += f"📋 *Résumé :*\n{summary}\n\n"

    notif += (
        f"Commandes dans la discussion client :\n"
        f"• *#REPRISE* — réactiver l'IA\n"
        f"• *#PAUSE* — garder la main\n"
        f"• *#RESOLU* — résolu + réactiver l'IA"
    )

    wa.send_to_admin(notif)
    logger.info("Notification escalade envoyée à l'admin")


def _generate_escalation_summary(conv_id: str, last_message: str) -> str | None:
    """
    Génère un résumé concis de la conversation via Claude.
    Utilisé pour l'admin lors d'une escalade.
    """
    try:
        from webhook_app.llm.engine import LLMEngine

        history = fetch_history(conv_id, limit=20)
        if not history:
            return None

        # Construire le transcript
        transcript_lines = []
        for msg in history:
            role = "Client" if msg.get("role") == "user" else "IA"
            content = (msg.get("content") or "")[:200]
            transcript_lines.append(f"{role}: {content}")

        transcript = "\n".join(transcript_lines)

        # Prompt de résumé
        summary_prompt = (
            "Tu es un assistant qui résume des conversations WhatsApp "
            "pour aider un admin à comprendre rapidement une situation d'escalade.\n\n"
            "Résume en 3-4 phrases maximum :\n"
            "1. Ce que le client voulait\n"
            "2. Le problème principal rencontré\n"
            "3. Ce qui a été tenté comme solution\n"
            "4. Pourquoi l'escalade a été déclenchée\n\n"
            "Sois concis et factuel. Pas de formules de politesse."
        )

        llm = LLMEngine()
        response_text, _ = llm.generate(
            system_prompt=summary_prompt,
            messages=[
                {
                    "role": "user",
                    "content": f"Conversation à résumer :\n\n{transcript}\n\nDernier message déclencheur : {last_message}"
                }
            ],
        )
        return response_text.strip() if response_text else None

    except Exception as e:
        logger.warning("Génération résumé escalade échouée (non bloquant) : %s", e)
        return None