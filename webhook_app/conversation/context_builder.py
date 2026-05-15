"""
conversation/context_builder.py — Assemblage du contexte LLM
=============================================================
Construit le prompt système et l'historique de messages
à envoyer au LLM à chaque tour de conversation.

Combine :
  - Le prompt système fixe (rôle, règles, ton)
  - Le contexte transactionnel (si disponible via fact_sales)
  - Les chunks RAG pertinents (knowledge base produit)
  - L'historique des messages récents
"""

import logging
from typing import Optional

from webhook_app.rag.retriever import build_rag_context
from webhook_app.config import Config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPT SYSTÈME DE BASE
# ══════════════════════════════════════════════════════════════════════════════

BASE_SYSTEM_PROMPT = """Tu es l'assistant commercial et support de Digitech Hub, \
une boutique en ligne spécialisée dans les formations digitales, logiciels et outils \
pour entrepreneurs et professionnels en Afrique francophone.

## Ton rôle
- Accueillir chaleureusement les prospects et clients
- Répondre aux questions sur les produits avec précision
- Aider les prospects à prendre leur décision d'achat
- Assister les clients après leur achat (accès, utilisation, problèmes)
- Gérer les objections avec bienveillance et professionnalisme
- Identifier les situations qui nécessitent une intervention humaine

## Ton ton
- Chaleureux, professionnel et accessible
- Français courant, adapté à un public africain francophone
- Messages courts et clairs (WhatsApp — pas de longs paragraphes)
- Utilise des emojis avec modération pour humaniser les échanges
- Tutoie le client si l'échange est informel, vouvoie sinon

## Règles impératives
- Ne jamais inventer d'informations sur un produit
- Ne jamais promettre ce qui n'est pas dans le contexte produit
- Si tu ne sais pas, dire honnêtement que tu vas vérifier
- Ne jamais communiquer de données personnelles d'autres clients
- Toujours rester poli, même face à un client difficile

## Signaux d'escalade
Si le client demande un remboursement, mentionne une arnaque,
veut parler à un responsable, ou si la situation dépasse tes capacités,
réponds UNIQUEMENT avec ce format exact — rien d'autre :

[ESCALADE_REQUISE]
Je comprends ta situation. Un membre de notre équipe va te contacter
très rapidement pour résoudre ça. 🙏

IMPORTANT : ne pose AUCUNE question supplémentaire après le tag.
Ne demande pas d'email, de numéro de commande ou d'autres informations.
Le message doit se terminer après la phrase de réassurance.


## Format des réponses
- Maximum 3-4 phrases par message WhatsApp
- Si tu dois donner plusieurs informations, utilise des listes courtes
- Termine toujours par une question ou une invitation à continuer si pertinent
"""


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXTE TRANSACTIONNEL
# ══════════════════════════════════════════════════════════════════════════════

def _build_transaction_context(conversation: dict) -> str:
    """
    Construit le bloc de contexte transactionnel à partir
    des données de la conversation et de fact_sales.
    """
    lines = []

    state = conversation.get("state", "new_prospect")
    product_id = conversation.get("product_id")
    last_sale_id = conversation.get("last_sale_id")

    # Enrichissement depuis fact_sales si sale_id disponible
    sale_data = None
    if last_sale_id:
        sale_data = _fetch_sale_data(last_sale_id)

    if state == "new_prospect":
        return ""  # Pas de contexte transactionnel pour un nouveau prospect

    lines.append("[CONTEXTE CLIENT]")

    if sale_data:
        lines.append(f"Produit : {sale_data.get('product_name', product_id)}")
        lines.append(f"Montant : {sale_data.get('amount_value', '')} {sale_data.get('currency', 'XOF')}")
        lines.append(f"Statut paiement : {_translate_status(sale_data.get('status', ''))}")
        if sale_data.get("completed_at"):
            lines.append(f"Date achat : {str(sale_data['completed_at'])[:10]}")
    elif product_id:
        lines.append(f"Produit concerné : {product_id}")

    lines.append(f"État conversation : {_translate_state(state)}")
    lines.append("[FIN CONTEXTE CLIENT]")

    return "\n".join(lines)


def _fetch_sale_data(sale_id: str) -> Optional[dict]:
    """Récupère les données de vente depuis fact_sales."""
    try:
        from webhook_app.database_pg import get_connection, execute_with_retry
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(
                conn,
                """
                SELECT product_id, product_name, amount_value, currency,
                       status, completed_at, failed_at, abandoned_at
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


def _translate_status(status: str) -> str:
    mapping = {
        "completed": "Paiement réussi ✅",
        "failed": "Paiement échoué ❌",
        "abandoned": "Paiement abandonné ⏸",
        "pending": "En attente ⏳",
    }
    return mapping.get(status.lower(), status)


def _translate_state(state: str) -> str:
    mapping = {
        "new_prospect": "Nouveau prospect",
        "interested_lead": "Prospect intéressé",
        "pre_sale": "En cours d'achat",
        "payment_failed": "Paiement échoué",
        "payment_abandoned": "Paiement abandonné",
        "payment_success": "Achat réussi",
        "post_sale": "Client — après achat",
        "support": "Demande de support",
        "escalation": "Escalade en cours",
    }
    return mapping.get(state, state)


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

class ContextBuilder:
    """
    Assemble le contexte complet à envoyer au LLM.
    """

    def build(
        self,
        conversation: dict,
        history: list[dict],
        user_message: str,
    ) -> dict:
        product_id = conversation.get("product_id")

        # ── Query RAG enrichie avec le contexte récent ────────────────
        # Pour les messages courts ou ambigus, on ajoute le dernier
        # message utilisateur précédent pour donner du contexte au RAG
        rag_query = user_message
        if len(user_message.strip().split()) <= 6:
            # Chercher le dernier message user dans l'historique
            prev_user_msgs = [
                m["content"] for m in history
                if m.get("role") == "user"
                and m.get("content") != user_message
            ]
            if prev_user_msgs:
                # Prendre le plus récent
                rag_query = f"{prev_user_msgs[-1]} {user_message}"

        # ── 1. Contexte RAG ───────────────────────────────────────────
        rag_context, chunk_ids = build_rag_context(
            query=rag_query,  # ← query enrichie
            product_id=product_id,
            top_k=Config.RAG_TOP_K,
            min_score=Config.RAG_MIN_SCORE,
        )
      

        # ── 2. Contexte transactionnel ────────────────────────────────────
        transaction_context = _build_transaction_context(conversation)

        # ── 3. Assemblage du prompt système ──────────────────────────────
        system_parts = [BASE_SYSTEM_PROMPT]

        if transaction_context:
            system_parts.append("\n" + transaction_context)

        if rag_context:
            system_parts.append("\n" + rag_context)
        else:
            system_parts.append(
                "\n[NOTE] Aucune information produit spécifique trouvée pour cette question. "
                "Réponds de façon générale et propose d'en savoir plus."
            )

        system_prompt = "\n".join(system_parts)

        # ── 4. Historique des messages au format LLM ──────────────────────
        # On exclut les messages system de l'historique
        # (ils sont déjà dans le system_prompt)
        llm_messages = []
        for msg in history:
            role = msg.get("role")
            if role in ("user", "assistant"):
                llm_messages.append({
                    "role": role,
                    "content": msg.get("content", ""),
                })

        logger.debug(
            "Contexte LLM construit — %d messages historique | RAG: %d chunks | TX: %s",
            len(llm_messages),
            len(chunk_ids),
            bool(transaction_context),
        )

        return {
            "system_prompt": system_prompt,
            "messages": llm_messages,
            "chunk_ids": chunk_ids,  # Pour logging dans save_message
        }