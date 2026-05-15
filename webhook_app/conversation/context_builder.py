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
# PROMPT SYSTÈME DE BASE — avec fallback DB
# ══════════════════════════════════════════════════════════════════════════════

BASE_SYSTEM_PROMPT = """Tu es l'assistant commercial et support de Digitech Hub, \
une boutique en ligne spécialisée dans les formations digitales, logiciels et outils \
pour entrepreneurs et professionnels en Afrique francophone.

## Ton rôle principal — Vendre et fidéliser
Tu es avant tout un vendeur et un assistant autonome.
Ton objectif est de CONVERTIR les prospects en clients et d'ASSISTER
les clients après achat. Tu dois gérer seul la très grande majorité
des situations sans intervention humaine.

## Gestion des objections et frustrations — ton cœur de métier
Quand un client exprime un doute, une peur ou une frustration :
- "c'est une arnaque" → Comprends sa peur, rassure avec des preuves
  (licence officielle, support inclus, milliers de clients satisfaits)
- "ça ne fonctionne pas" → Diagnostique étape par étape avec la KB
- "c'est trop cher" → Justifie la valeur, compare avec les alternatives
- "je ne suis pas sûr" → Pose des questions pour comprendre le doute
- "j'ai été trompé ailleurs" → Empathie + différenciateurs Digitech Hub

Ne jamais escalader au premier signe de frustration.
Traite chaque objection comme une opportunité de convaincre et de vendre.

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

## Protocole "j'ai payé mais rien reçu"
Suis ces étapes dans l'ordre avant toute escalade :
1. Demander l'email utilisé pour le paiement
2. Suggérer de vérifier spam/courrier indésirable
3. Demander confirmation du paiement (SMS opérateur reçu ?)
4. Proposer https://digitechhub.store/support avec numéro de transaction
5. Si toujours bloqué après ces 4 étapes → [ESCALADE_REQUISE]

## Protocole "installation impossible"
Suis ces étapes dans l'ordre :
1. Guider étape par étape depuis les instructions reçues par email
2. Suggérer de désactiver l'antivirus temporairement
3. Suggérer de redémarrer et réessayer en administrateur
4. Si 3 tentatives documentées échouent → [ESCALADE_REQUISE]

## Quand escalader — uniquement ces cas après épuisement des options
Insère [ESCALADE_REQUISE] UNIQUEMENT si :
1. Problème d'accès persistant : paiement confirmé + email introuvable
   après avoir suivi le protocole complet ci-dessus
2. Installation échoue après toutes les étapes documentées (3+ tentatives)
3. Le client demande explicitement un humain 3 fois ou plus
   malgré tes réponses
4. Litige financier confirmé par les deux parties après investigation

## Ce qui N'est PAS une raison d'escalader
- Frustration verbale ("arnaque", "escroquerie", "impossible")
- Doutes ou objections sur le produit
- Comparaisons négatives avec la concurrence
- Mécontentement du prix
- Première ou deuxième mention d'un problème technique

## Format des réponses
- Maximum 3-4 phrases par message WhatsApp
- Si tu dois donner plusieurs informations, utilise des listes courtes
- Termine toujours par une question ou une invitation à continuer
- En cas d'escalade : [ESCALADE_REQUISE] sur la première ligne,
  suivi d'un message bref de réassurance UNIQUEMENT, sans questions
"""


def _load_prompt(key: str, fallback: str) -> str:
    """
    Charge un prompt depuis la DB.
    Si absent ou erreur → retourne le fallback en dur.
    """
    try:
        from webhook_app.database_v21 import get_prompt
        db_content = get_prompt(key)
        if db_content:
            logger.debug("Prompt '%s' chargé depuis DB", key)
            return db_content
    except Exception as e:
        logger.warning("Impossible de charger prompt '%s' depuis DB : %s", key, e)
    return fallback


def get_base_prompt() -> str:
    """Retourne le prompt de base — DB en priorité, fallback en dur."""
    return _load_prompt("base", BASE_SYSTEM_PROMPT)




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

        # ── Query RAG enrichie ────────────────────────────────────────
        rag_query = user_message
        if len(user_message.strip().split()) <= 6:
            prev_user_msgs = [
                m["content"] for m in history
                if m.get("role") == "user"
                and m.get("content") != user_message
            ]
            if prev_user_msgs:
                rag_query = f"{prev_user_msgs[-1]} {user_message}"

        # ── 1. Contexte RAG ───────────────────────────────────────────
        rag_context, chunk_ids = build_rag_context(
            query=rag_query,
            product_id=product_id,
            top_k=Config.RAG_TOP_K,
            min_score=Config.RAG_MIN_SCORE,
        )

        # ── 2. Contexte transactionnel ────────────────────────────────
        transaction_context = _build_transaction_context(conversation)

        # ── Détection signal de frustration ──────────────────────────
        frustration_keywords = self._load_frustration_keywords()
        frustration_detected = any(kw in user_message.lower() for kw in frustration_keywords)

        # ── 3. Prompt de base ─────────────────────────────────────────
        base_prompt = get_base_prompt()
        system_parts = [base_prompt]

        if transaction_context:
            system_parts.append("\n" + transaction_context)

        # Signal frustration → instruction empathie
        if frustration_detected:
            system_parts.append(
                "\n[SIGNAL CLIENT] Le client exprime une frustration ou un doute fort. "
                "Adopte un ton particulièrement empathique, reconnais sa situation "
                "sans jamais escalader pour ce seul motif. Concentre-toi sur le rassurer "
                "et résoudre son problème avec les informations disponibles."
            )

        if rag_context:
            system_parts.append("\n" + rag_context)
        else:
            system_parts.append(
                "\n[NOTE] Aucune information produit spécifique trouvée. "
                "Réponds de façon générale et propose d'en savoir plus."
            )

        system_prompt = "\n".join(system_parts)

        # ── 5. Historique messages ────────────────────────────────────
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
            "chunk_ids": chunk_ids,
        }
    
    def _load_frustration_keywords(self) -> list[str]:
        """
        Charge les mots clés de frustration depuis la DB.
        Fallback sur liste par défaut si DB indisponible.
        """
        default = [
            "arnaque", "escroquerie", "trompé", "volé",
            "remboursement", "impossible", "ne fonctionne pas",
            "ne marche pas", "fraudé", "mensonge",
        ]
        try:
            from webhook_app.database_pg import get_connection, execute_with_retry
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