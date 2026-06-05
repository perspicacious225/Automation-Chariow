"""
conversation/context_builder.py — Assemblage du contexte LLM
=============================================================
Construit le prompt système et l'historique de messages
à envoyer au LLM à chaque tour de conversation.

Combine :
  - Le prompt système (FR/EN selon langue détectée)
  - Le contexte transactionnel enrichi (fact_sales — abandoned/failed/confirmed)
  - Les chunks RAG pertinents (knowledge base produit)
  - L'historique des messages récents
  - Les signaux de frustration
  - Le variant A/B si expérience active
"""

import logging
from typing import Optional

from webhook_app.rag.retriever import build_rag_context
from webhook_app.database_pg import get_connection, execute_with_retry
from webhook_app.config import Config
from webhook_app.llm.prompts import (

    get_base_prompt_adaptive,
    VENDOR_STATES
)

logger = logging.getLogger(__name__)



RAG_CONFIG: dict[str, dict] = {
    "new_prospect":      {"top_k": 3, "min_score": 0.35},
    "interested_lead":   {"top_k": 5, "min_score": 0.30},
    "pre_sale":          {"top_k": 3, "min_score": 0.35},
    "payment_failed":    {"top_k": 3, "min_score": 0.35},
    "payment_abandoned": {"top_k": 3, "min_score": 0.35},
    "payment_success":   {"top_k": 4, "min_score": 0.35},
    "post_sale":         {"top_k": 5, "min_score": 0.33},
    "support":           {"top_k": 6, "min_score": 0.33},
    "escalation":        {"top_k": 0, "min_score": 1.00},
}

DEFAULT_RAG_CONFIG = {"top_k": 4, "min_score": 0.35}



def _load_prompt(key: str, fallback: str) -> str:
    """Charge un prompt depuis la DB. Fallback en dur si absent."""
    try:
        from webhook_app.database_v21 import get_prompt
        db_content = get_prompt(key)
        if db_content:
            logger.debug("Prompt '%s' chargé depuis DB", key)
            return db_content
    except Exception as e:
        logger.warning("Impossible de charger prompt '%s' depuis DB : %s", key, e)
    return fallback



def get_base_prompt(state: str = "new_prospect") -> str:
    """
    Retourne le prompt adaptatif selon le mode et la langue.
    Fallback vers le prompt base complet si clé DB absente.
    """
    return get_base_prompt_adaptive(state)


# ════════════════════════════════
# ENRICHISSEMENT CONTEXTE TRANSACTIONNEL
# ════════════════════════════════
def _build_transaction_context(conversation: dict, phone: str = "") -> str:
    state        = conversation.get("state", "new_prospect")
    product_id   = conversation.get("product_id")
    last_sale_id = conversation.get("last_sale_id")

    # Si prospect totalement vierge, on ne pollue pas le prompt
    if state == "new_prospect" and not last_sale_id and not phone:
        return ""

    # On utilise une liste qu'on va joindre SANS sauts de ligne
    xml = ["<ctx>"]
    xml.append(f"<etat>{state}</etat>")

    # ── Vente Actuelle 
    sale_data = None
    if last_sale_id:
        sale_data = _fetch_sale_data(last_sale_id)

    if sale_data:
        prod = sale_data.get('product_name') or product_id or '?'
        mnt = sale_data.get('amount_value') or 'N/A'
        status = sale_data.get('status', '')
        
        xml.append("<vente>")
        xml.append(f"<prod>{prod}</prod><mnt>{mnt}</mnt><statut>{status}</statut>")
        if sale_data.get("email"):
            xml.append(f"<email>{sale_data['email']}</email>")
        xml.append(f"<tel>{sale_data.get('phone') or phone}</tel>")
        xml.append("</vente>")
        
    elif product_id:
        xml.append(f"<prod>{product_id}</prod>")

    # ── Historique (Format attributs ultra-compressé) ──
    if phone:
        transactions = _fetch_customer_transactions(phone)
        xml.append("<txs>")
        if transactions:
            priority = {"confirmed": 1, "failed": 2, "abandoned": 3}
            transactions.sort(key=lambda x: (
                priority.get(x.get("transaction_type", ""), 4),
                -(x.get("hours_since_created") or 0)
            ))

            for tx in transactions[:3]:
                t = tx.get("transaction_type", "")
                p = tx.get("product_name") or tx.get("product_id") or "?"
                hours_raw = tx.get("hours_since_created")
                h = f"{int(hours_raw)}h" if hours_raw is not None else "0h"
                
                # Format ultra-court : <tx type="failed" prod="Office" tps="2h"/>
                xml.append(f'<tx type="{t}" prod="{p}" tps="{h}"/>')
        else:
            xml.append("<info>0</info>")
        xml.append("</txs>")

    # ── Vérification paiement ────────────────────────
    if not last_sale_id and state in ("interested_lead", "pre_sale", "payment_failed", "payment_abandoned"):
        xml.append("<verif>non</verif>")

    xml.append("</ctx>")
    
    
    return "".join(xml)


def _was_link_recently_sent(history: list[dict], product_id: str) -> bool:
    """
    Vérifie si un lien de paiement a été envoyé dans les 3 derniers
    messages assistant.
    """
    from webhook_app.database_conv import get_chunks_by_section
    
    # Récupérer l'URL checkout depuis la KB
    chunks = get_chunks_by_section(product_id, "commercial") if product_id else []
    checkout_url = ""
    if chunks:
        import re
        match = re.search(r'https?://[^\s]+checkout[^\s]*', chunks[0].get("chunk_text", ""))
        if match:
            checkout_url = match.group(0)

    if not checkout_url:
        return False

    assistant_msgs = [m for m in history if m.get("role") == "assistant"]
    recent = assistant_msgs[-3:]   
    return any(checkout_url in (m.get("content") or "") for m in recent)


def _fetch_sale_data(sale_id: str) -> Optional[dict]:
    """Récupère les données de vente depuis fact_sales par sale_id."""
    try:
    
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(
                conn,
                """
                SELECT product_id, product_name, amount_value, currency,
                       status, completed_at, failed_at, abandoned_at,
                       email, phone  
                FROM fact_sales
                WHERE sale_id = %s LIMIT 1
                """,
                (sale_id,),
                fetch="one",
            )
            return dict(row) if row else None
    except Exception as e:
        logger.warning("Impossible de récupérer fact_sales pour %s : %s", sale_id, e)
        return None


def _fetch_customer_transactions(phone: str) -> list[dict]:
    """Récupère les transactions du client depuis la vue enrichie."""
    try:
        from webhook_app.database_v21 import get_customer_transactions
        return get_customer_transactions(phone)
    except Exception as e:
        logger.warning("Impossible de récupérer transactions pour %s : %s", phone, e)
        return []

def _translate_status(status: str) -> str:
    mapping = {
        "completed": "Paiement réussi ✅",
        "failed":    "Paiement échoué ❌",
        "abandoned": "Paiement abandonné ⏸",
        "pending":   "En attente ⏳",
    }
    return mapping.get((status or "").lower(), status)


def _translate_state(state: str) -> str:
    mapping = {
        "new_prospect":      "Nouveau prospect",
        "interested_lead":   "Prospect intéressé",
        "pre_sale":          "En cours d'achat",
        "payment_failed":    "Paiement échoué",
        "payment_abandoned": "Paiement abandonné",
        "payment_success":   "Achat réussi",
        "post_sale":         "Client — après achat",
        "support":           "Demande de support",
        "escalation":        "Escalade en cours",
    }
    return mapping.get(state, state)



def _extract_prefilled_fields(conversation: dict, phone: str = "") -> dict:
    """
    Extrait les champs clés depuis la DB pour les formater en XML.
    """
    fields = {
        "email_connu":      "ABSENT",
        "paiement_verifie": "non",
        "current_state":    conversation.get("state", "new_prospect"),
    }

    last_sale_id = conversation.get("last_sale_id")
    
    # 1. Tenter d'utiliser last_sale_id
    if last_sale_id:
        try:
            sale_data = _fetch_sale_data(last_sale_id)
            if sale_data:
                if sale_data.get("email"):
                    fields["email_connu"] = sale_data["email"]
                if sale_data.get("status") == "completed":
                    fields["paiement_verifie"] = "oui"
        except Exception as e:
            logger.warning("_extract_prefilled_fields (sale_id) erreur : %s", e)
            
    # 2. Si pas d'email via fact_sales, vérifier si le webhook l'a extrait
    if fields["email_connu"] == "ABSENT" and conversation.get("contact_key"):
         fields["email_connu"] = conversation["contact_key"]

    return fields


def _detect_cdd_phase(history: list[dict]) -> str | None:
    """
    Détecte la phase CDD en cours depuis l'historique.
    Si le dernier message assistant répondait à une objection
    → le client répond maintenant → phase discuter_demonter autorisée.
    """
    if len(history) < 2:
        return None

    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue

        metadata = msg.get("metadata") or {}
        last_type = metadata.get("decision_type", "")

        if last_type.startswith("objection_"):
            return "discuter_demonter"

        # Dernier message assistant trouvé mais pas une réponse à objection
        return None

    return None

def _generate_summary(messages: list[dict], language: str = "fr") -> str | None:
    """
    Génère un résumé condensé de l'historique ancien via Haiku.
    Appelé uniquement quand le résumé est absent ou obsolète.
    """
    if not messages:
        return None

    # Formater l'historique pour le résumé
    history_text = "\n".join([
        f"{msg['role'].upper()} : {(msg.get('content') or '')[:300]}"
        for msg in messages
        if msg.get("role") in ("user", "assistant") and msg.get("content")
    ])

    if not history_text.strip():
        return None

    if language == "en":
        prompt = (
            "You are summarizing a WhatsApp sales/support conversation "
            "for Digitech Hub. Generate a factual summary in 4-6 sentences.\n\n"
            "MANDATORY — include if mentioned:\n"
            "- Client name\n"
            "- Product discussed and price quoted\n"
            "- Payment status (paid / not paid / verification pending)\n"
            "- Email and phone used for payment\n"
            "- Technical problems encountered\n"
            "- Current conversation state "
            "(prospect / interested / pre-sale / support)\n"
            "- Any objections raised and how they were handled\n\n"
            "Be factual. Never invent information not in the conversation.\n\n"
            f"CONVERSATION:\n{history_text}"
        )
    else:
        prompt = (
            "Tu résumes une conversation WhatsApp de vente/support "
            "pour Digitech Hub. Génère un résumé factuel en 4 à 5 phrases.\n\n"
            "OBLIGATOIRE — inclure si mentionné :\n"
            "- Prénom/nom du client\n"
            "- Produit discuté et prix cité\n"
            "- Statut paiement (payé / non payé / vérification en attente)\n"
            "- Email et téléphone utilisés pour le paiement\n"
            "- Problèmes techniques rencontrés\n"
            "- État actuel de la conversation "
            "(prospect / intéressé / pré-achat / support)\n"
            "- Objections soulevées et comment elles ont été traitées\n\n"
            "Sois factuel. Ne jamais inventer d'information absente "
            "de la conversation.\n\n"
            f"CONVERSATION:\n{history_text}"
        )

    try:
        import anthropic
        from webhook_app.config import Config

        client = anthropic.Anthropic(api_key=Config.LLM_API_KEY)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",  
            max_tokens=300,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        summary = response.content[0].text.strip() if response.content else None
        logger.debug("Résumé généré : %d chars", len(summary) if summary else 0)
        return summary

    except Exception as e:
        logger.warning("_generate_summary erreur : %s", e)
        return None
# ════════════════════════════════
# CONTEXT BUILDER
# ════════════════════════════════

class ContextBuilder:

    def build(
        self,
        conversation: dict,
        history: list[dict],
        user_message: str,
    ) -> dict:
        product_id = conversation.get("product_id")
        phone      = conversation.get("phone", "")
        language   = conversation.get("language", "fr")
        state      = conversation.get("state", "new_prospect")


        # ── Query RAG enrichie 

        rag_query = user_message  
        
        if len(user_message.strip().split()) <= 6:
            prev_user_msgs = [
                m["content"] for m in history
                if m.get("role") == "user"
                and m.get("content") != user_message
            ]
            
            if prev_user_msgs:
                #  Historique récent
                combined = f"{prev_user_msgs[-1]} {user_message}"
                words = combined.split()
                rag_query = " ".join(words[:10]) if len(words) > 10 else combined
            
            elif product_id:
                #  Injection produit cold start
                rag_query = f"{user_message} {product_id} lien paiement prix"

        # ── 1. Contexte RAG
        
        _rag_cfg = RAG_CONFIG.get(state, DEFAULT_RAG_CONFIG)
        rag_context, chunk_ids = build_rag_context(
            query=rag_query,
            product_id=product_id,
            top_k=_rag_cfg["top_k"],
            min_score=_rag_cfg["min_score"],
            state=state
        )

        # ── 2. Contexte transactionnel enrichi 
        transaction_context = _build_transaction_context(conversation, phone)

        # ── 3. Détection frustration 
        frustration_keywords = self._load_frustration_keywords()
        frustration_detected = any(kw in user_message.lower() for kw in frustration_keywords)

        # ── 4. A/B testing — variant prompt \
        ab_prompt = self._get_ab_prompt(conversation)

        # ── 5. Prompt de base selon langue et ROUTAGE BINAIRE 

        base_prompt = ab_prompt if ab_prompt else get_base_prompt(state=state)
        static_prompt = base_prompt

        # Partie dynamique 
        dynamic_parts = []

        #VÉRITÉ XML UNIFIÉE 
    
        if transaction_context:
            dynamic_parts.append(transaction_context)
        if _was_link_recently_sent(history, product_id):
            dynamic_parts.append("<lien_recent>oui</lien_recent>")

        #PHASE CDD Traitement des objections
        # Injectée si le client répond à une clarification 
        cdd_phase = _detect_cdd_phase(history)
        if cdd_phase == "discuter_demonter":
            dynamic_parts.append(
                "\n[CDD_PHASE: discuter_demonter]\n"
                "Le client répond à ta question de clarification précédente.\n"
                "Tu peux maintenant utiliser les arguments, chiffres et preuves KB.\n"
            )

        # ── 3. RAG ET BASE DE CONNAISSANCES 
        if frustration_detected:
            if rag_context:
                # Injection de la RAG avec format attendu par la Source 1
                dynamic_parts.append(f"\n{rag_context}\n")

            else:

                dynamic_parts.append("\n[NOTE] Aucune information produit trouvée.\n")
        else:
            # Toujours injecter la RAG si elle existe 
            if rag_context:
                dynamic_parts.append(f"\n{rag_context}\n")
        


        dynamic_prompt = "\n".join(dynamic_parts)

        # ── 6. Construction des messages pour le LLM 

        # Seuil : si plus de 10 messages → résumé glissant
        HISTORY_THRESHOLD = 10
        HISTORY_RECENT_COUNT = 6
        SUMMARY_UPDATE_THRESHOLD = 5

        llm_messages = []

        if len(history) > HISTORY_THRESHOLD:
            # Partie ancienne → résumé
            old_msgs    = history[:-HISTORY_RECENT_COUNT]
            recent_msgs = history[-HISTORY_RECENT_COUNT:]

            # Charger le résumé existant depuis DB
            from webhook_app.database_conv import (
                get_conversation_summary,
                update_conversation_summary,
            )
            conv_id      = str(conversation.get("id", ""))
            summary_data = get_conversation_summary(conv_id)

            # Résumé à jour si existe ET écart < SUMMARY_UPDATE_THRESHOLD
            if summary_data and (len(old_msgs) - summary_data["msg_count"]) < SUMMARY_UPDATE_THRESHOLD:
                summary_text = summary_data["summary"]
                logger.debug(
                    "Résumé glissant : cache utilisé (%d msgs résumés, %d actuels)",
                    summary_data["msg_count"],
                    len(old_msgs),
                )
            else:
                # Résumé absent ou trop ancien 
                summary_text = _generate_summary(old_msgs, language)
                if summary_text and conv_id:
                    update_conversation_summary(conv_id, summary_text, len(old_msgs))
                    logger.info(
                        "Résumé glissant généré et sauvegardé (%d msgs résumés)",
                        len(old_msgs),
                    )
                else:
                    logger.warning(
                        "Résumé glissant : génération échouée — "
                        "fallback historique complet"
                    )
                    # Fallback :  tout l'historique si résumé échoue
                    summary_text = None
                    for msg in history:
                        role    = msg.get("role", "")
                        content = (msg.get("content") or "").strip()
                        if role in ("user", "assistant") and content:
                            llm_messages.append({"role": role, "content": content})

            # Injecter le résumé + messages récents si résumé disponible
            if summary_text:
                llm_messages.append({
                    "role": "user",
                    "content": (
                        f"[RÉSUMÉ CONVERSATION PRÉCÉDENTE]\n"
                        f"{summary_text}\n"
                        f"[FIN RÉSUMÉ]"
                    ),
                })
                llm_messages.append({
                    "role": "assistant",
                    "content": "Compris, je prends en compte le contexte de notre échange précédent.",
                })

                # Ajouter les messages récents complets
                for msg in recent_msgs:
                    role    = msg.get("role", "")
                    content = (msg.get("content") or "").strip()
                    if role in ("user", "assistant") and content:
                        llm_messages.append({"role": role, "content": content})

        else:
            # Historique court, envoyer tout
            for msg in history:
                role    = msg.get("role", "")
                content = (msg.get("content") or "").strip()
                if role in ("user", "assistant") and content:
                    llm_messages.append({"role": role, "content": content})

        # Ajouter le message utilisateur courant si pas déjà présent
        last_msg = llm_messages[-1] if llm_messages else None
        current_content = user_message.strip()
        if not last_msg or last_msg["role"] != "user" or last_msg["content"] != current_content:
            if current_content:
                llm_messages.append({"role": "user", "content": current_content})

        # # ── Debug résumé et tokens ─────────
        # if len(history) > HISTORY_THRESHOLD:
        #     logger.debug(
        #         "Résumé debug — history=%d old=%d recent=%d llm_msgs=%d",
        #         len(history),
        #         len(old_msgs),
        #         len(recent_msgs),
        #         len(llm_messages),
        #     )

        # logger.debug(
        #     "Static prompt debug — base=%d state=%d total=%d",
        #     len(base_prompt),
        #     len(static_prompt),
        #     len(base_prompt) + len(static_prompt)
        # )

        # logger.debug(
        #     "Tokens breakdown — static≈%d dynamic≈%d history≈%d rag≈%d",
        #     len(static_prompt) // 4,
        #     len(dynamic_prompt) // 4,
        #     sum(len(m.get("content", "")) for m in llm_messages) // 4,
        #     len(rag_context) // 4 if rag_context else 0,
        # )

        # # ── Log existant ────────────────────
        # logger.debug(
        #     "Contexte LLM — lang=%s | %d msgs | RAG: %d chunks | frustration: %s",
        #     language,
        #     len(llm_messages),
        #     len(chunk_ids),
        #     frustration_detected,
        # )

        return {
            "system_prompt":   static_prompt,
            "dynamic_context": dynamic_prompt,
            "messages":        llm_messages,
            "chunk_ids":       chunk_ids,
        }

    def _load_frustration_keywords(self) -> list[str]:
        """Charge les mots clés de frustration depuis la DB."""
        default = [
            "arnaque", "escroquerie", "trompé", "volé",
            "remboursement", "impossible", "ne fonctionne pas",
            "ne marche pas", "fraudé", "mensonge",
        ]
        try:
            
            with get_connection(readonly=True) as conn:
                rows = execute_with_retry(
                    conn,
                    """
                    SELECT keyword FROM escalation_keywords
                    WHERE is_active = TRUE AND category = 'frustration'
                    ORDER BY keyword
                    """,
                    fetch="all",
                ) or []
                if rows:
                    return [r["keyword"] for r in rows]
        except Exception as e:
            logger.warning("Chargement frustration keywords échoué : %s", e)
        return default

    def _get_ab_prompt(self, conversation: dict) -> Optional[str]:
        """
        Retourne le prompt du variant A/B si une expérience est active.
        Retourne None si pas d'expérience active.
        """
        try:
            from webhook_app.database_v21 import (
                get_active_experiment,
                get_or_assign_variant,
                get_prompt,
            )
            experiment = get_active_experiment()
            if not experiment:
                return None

            conv_id = str(conversation.get("id", ""))
            phone   = conversation.get("phone", "")

            variant = get_or_assign_variant(
                experiment_id=str(experiment["id"]),
                conversation_id=conv_id,
                phone=phone,
                split_percent=experiment.get("split_percent", 50),
            )

            prompt_key = (
                experiment["variant_b_key"]
                if variant == "B"
                else experiment["variant_a_key"]
            )
            prompt_content = get_prompt(prompt_key)
            if prompt_content:
                logger.debug("A/B variant %s — prompt '%s'", variant, prompt_key)
                return prompt_content

        except Exception as e:
            logger.warning("A/B testing échoué (non bloquant) : %s", e)
        return None