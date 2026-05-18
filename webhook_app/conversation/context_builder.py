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
from webhook_app.config import Config
from webhook_app.llm.prompts import (
    BASE_SYSTEM_PROMPT,
    BASE_SYSTEM_PROMPT_EN,
    get_base_prompt_adaptive,
    VENDOR_STATES,
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


def get_base_prompt(language: str = "fr") -> str:
    """Retourne le prompt de base selon la langue — DB en priorité."""
    if language == "en":
        return _load_prompt("base_en", BASE_SYSTEM_PROMPT_EN)
    return _load_prompt("base", BASE_SYSTEM_PROMPT)

def get_base_prompt(language: str = "fr", state: str = "new_prospect") -> str:
    """
    Retourne le prompt adaptatif selon le mode et la langue.
    Fallback vers le prompt base complet si clé DB absente.
    """
    return get_base_prompt_adaptive(state, language)



# ══════════════════════════════════════════════════════════════════════════════
# DÉTECTION LANGUE
# ══════════════════════════════════════════════════════════════════════════════

def detect_language(text: str) -> str:
    """
    Détecte la langue du message — FR ou EN.
    Utilise des marqueurs linguistiques simples sans bibliothèque externe.
    Défaut : 'fr'
    """
    text_lower = text.lower().strip()

    en_markers = [
        "hello", "hi", "hey", "good morning", "good evening",
        "i need", "i want", "i would like", "please", "thank you",
        "how much", "what is", "can you", "do you", "i have",
        "i paid", "i bought", "it doesn't work", "not working",
        "help me", "my order", "my account", "send me",
    ]

    fr_markers = [
        "bonjour", "bonsoir", "salut", "allô", "allo",
        "je veux", "je voudrais", "j'ai besoin", "s'il vous plaît",
        "merci", "combien", "qu'est-ce", "pouvez-vous", "est-ce que",
        "j'ai payé", "j'ai acheté", "ça ne fonctionne", "aide-moi",
        "ma commande", "mon compte", "envoyez-moi", "c'est",
    ]

    en_score = sum(1 for m in en_markers if m in text_lower)
    fr_score = sum(1 for m in fr_markers if m in text_lower)

    if en_score > fr_score:
        return "en"
    return "fr"


# ══════════════════════════════════════════════════════════════════════════════
# ENRICHISSEMENT CONTEXTE TRANSACTIONNEL
# ══════════════════════════════════════════════════════════════════════════════

def _build_transaction_context(conversation: dict, phone: str = "") -> str:
    state        = conversation.get("state", "new_prospect")
    product_id   = conversation.get("product_id")
    last_sale_id = conversation.get("last_sale_id")

    if state == "new_prospect" and not last_sale_id and not phone:
        return ""

    lines = ["[CONTEXTE CLIENT]"]
    lines.append(f"État conversation : {_translate_state(state)}")

    # ── Enrichissement depuis fact_sales par sale_id ──────────────
    sale_data = None
    if last_sale_id:
        sale_data = _fetch_sale_data(last_sale_id)

    if sale_data:
        status = sale_data.get("status", "")
        lines.append(f"Produit : {sale_data.get('product_name', product_id)}")
        lines.append(f"Montant : {sale_data.get('amount_value', '')} {sale_data.get('currency', 'XOF')}")
        lines.append(f"Statut paiement : {_translate_status(status)}")
        if sale_data.get("completed_at"):
            lines.append(f"Date achat : {str(sale_data['completed_at'])[:10]}")
    elif product_id:
        lines.append(f"Produit concerné : {product_id}")

    # ── Historique transactions par numéro ────────────────────────
    if phone:
        transactions = _fetch_customer_transactions(phone)
        if transactions:
            lines.append("")
            lines.append("[HISTORIQUE TRANSACTIONS CLIENT]")

            priority = {"confirmed": 1, "failed": 2, "abandoned": 3}
            transactions.sort(key=lambda x: (
                priority.get(x.get("transaction_type", ""), 4),
                -(x.get("hours_since_created") or 0)
            ))

            has_confirmed = any(t.get("transaction_type") == "confirmed" for t in transactions)
            has_abandoned = any(t.get("transaction_type") == "abandoned" for t in transactions)
            has_failed    = any(t.get("transaction_type") == "failed" for t in transactions)

            for tx in transactions[:3]:
                tx_type  = tx.get("transaction_type", "")
                product  = tx.get("product_name") or tx.get("product_id") or "—"
                amount   = tx.get("amount_value", "")
                currency = tx.get("currency", "XOF")
                hours    = tx.get("hours_since_created", 0)

                hours_display = f"il y a {int(hours)}h" if hours is not None else "récemment"
                if tx_type == "confirmed": 
                    lines.append(f"✅ Achat confirmé : {product} — {amount} {currency} ({hours_display})")
                elif tx_type == "failed":
                    lines.append(f"❌ Paiement échoué : {product} — {amount} {currency} ({hours_display}) — Aide le client à finaliser")
                elif tx_type == "abandoned":
                    lines.append(f"⏸ Panier abandonné : {product} — {amount} {currency} ({hours_display}) — Opportunité de conversion")

            lines.append("")
            if has_confirmed and has_abandoned:
                lines.append("→ Ce client a déjà acheté ET a un panier abandonné. S'il parle du produit acheté → support. S'il mentionne un autre produit → propose-le.")
            elif has_confirmed:
                lines.append("→ Paiement confirmé. Mode support post-achat pour ce produit. Reste ouvert à vendre d'autres produits si le client en exprime le besoin.")
            elif has_abandoned:
                lines.append("→ Panier abandonné. Opportunité de conversion — aide à finaliser.")
            elif has_failed:
                lines.append("→ Paiement échoué. Aide le client à identifier le problème et à finaliser.")

            lines.append("[FIN HISTORIQUE]")

        else:
            # ── Aucune transaction trouvée par numéro ─────────────
            lines.append("")
            lines.append("[AUCUNE TRANSACTION TROUVÉE POUR CE NUMÉRO]")
            lines.append(
                "→ Si le client dit avoir payé : demande son email ou numéro "
                "de téléphone utilisé lors du paiement pour vérification interne."
            )

    # ── Aucun paiement vérifié — instruction au LLM ───────────────
    # Placé ici — après tout l'historique — pour couvrir le cas
    # où aucune transaction n'existe ET l'état suggère une vérification
    if not last_sale_id and state in (
        "interested_lead", "pre_sale",
        "payment_failed", "payment_abandoned"
    ):
        lines.append("")
        lines.append("[AUCUN PAIEMENT VÉRIFIÉ POUR L'INSTANT]")
        lines.append(
            "→ Si le client dit avoir payé et fournit un email ou téléphone : "
            "insère [VERIFY_PAYMENT:valeur] IMMÉDIATEMENT. "
            "Ne confirme JAMAIS un paiement sans [RÉSULTAT VÉRIFICATION] dans le contexte."
        )

    lines.append("[FIN CONTEXTE CLIENT]")
    return "\n".join(lines)

def _fetch_sale_data(sale_id: str) -> Optional[dict]:
    """Récupère les données de vente depuis fact_sales par sale_id."""
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


def _fetch_customer_transactions(phone: str) -> list[dict]:
    """Récupère les transactions du client depuis la vue enrichie."""
    try:
        from webhook_app.database_v21 import get_customer_transactions
        return get_customer_transactions(phone)
    except Exception as e:
        logger.warning("Impossible de récupérer transactions pour %s : %s", phone, e)
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


# ══════════════════════════════════════════════════════════════════════════════
# CONTEXT BUILDER
# ══════════════════════════════════════════════════════════════════════════════

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

        # ── Détection langue sur premier message ──────────────────
        # Si langue pas encore définie ou par défaut fr,
        # on tente de détecter sur chaque message
        if not language or language == "fr":
            detected = detect_language(user_message)
            if detected != language:
                language = detected
                # Mise à jour en DB en arrière-plan
                try:
                    from webhook_app.database_v21 import set_conversation_language
                    conv_id = str(conversation.get("id", ""))
                    if conv_id:
                        set_conversation_language(conv_id, language)
                        logger.info("Langue détectée : %s → conv %s", language, conv_id)
                except Exception as e:
                    logger.warning("Mise à jour langue échouée : %s", e)

        # ── Query RAG enrichie ────────────────────────────────────
        rag_query = user_message
        if len(user_message.strip().split()) <= 6:
            prev_user_msgs = [
                m["content"] for m in history
                if m.get("role") == "user"
                and m.get("content") != user_message
            ]
            if prev_user_msgs:
                rag_query = f"{prev_user_msgs[-1]} {user_message}"

        # ── 1. Contexte RAG ───────────────────────────────────────

        _rag_cfg = RAG_CONFIG.get(state, DEFAULT_RAG_CONFIG)
        rag_context, chunk_ids = build_rag_context(
            query=rag_query,
            product_id=product_id,
            top_k=_rag_cfg["top_k"],
            min_score=_rag_cfg["min_score"],
        )

        # ── 2. Contexte transactionnel enrichi ────────────────────
        transaction_context = _build_transaction_context(conversation, phone)

        # ── 3. Détection frustration ──────────────────────────────
        frustration_keywords = self._load_frustration_keywords()
        frustration_detected = any(kw in user_message.lower() for kw in frustration_keywords)

        # ── 4. A/B testing — variant prompt ──────────────────────
        ab_prompt = self._get_ab_prompt(conversation)

        # ── 5. Prompt de base selon langue ───────────────────────

        from webhook_app.llm.prompts import get_state_prompt
        base_prompt = ab_prompt if ab_prompt else get_base_prompt(language, state=state)
        state_prompt = get_state_prompt(state)
        static_prompt = base_prompt + "\n" + state_prompt

        # Partie dynamique → jamais mise en cache
        dynamic_parts = []

        if transaction_context:
            dynamic_parts.append(transaction_context)

        if frustration_detected:
            if language == "en":
                dynamic_parts.append(
                    "[CLIENT SIGNAL] Customer expressing strong frustration. "
                    "Be especially empathetic. Focus on reassuring and solving."
                )
            else:
                dynamic_parts.append(
                    "[SIGNAL CLIENT] Le client exprime une frustration forte. "
                    "Adopte un ton empathique. Concentre-toi sur le rassurer et résoudre."
                )

        if rag_context:
            dynamic_parts.append(rag_context)
        else:
            if language == "en":
                dynamic_parts.append(
                    "[NOTE] No specific product information found. Respond generally."
                )
            else:
                dynamic_parts.append(
                    "[NOTE] Aucune information produit trouvée. Réponds de façon générale."
                )

        dynamic_prompt = "\n".join(dynamic_parts)

        # ── 6. Construction des messages pour le LLM ──────────────
        # Historique au format Anthropic [{role, content}]
        llm_messages = []
        for msg in history:
            role    = msg.get("role", "")
            content = (msg.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                llm_messages.append({"role": role, "content": content})

        # Ajouter le message utilisateur courant en dernier
        if user_message.strip():
            llm_messages.append({"role": "user", "content": user_message.strip()})

        logger.debug(
            "Contexte LLM — lang=%s | %d msgs | RAG: %d chunks | frustration: %s",
            language,
            len(llm_messages),
            len(chunk_ids),
            frustration_detected,
        )

        return {
            "system_prompt":   static_prompt,
            "dynamic_context": dynamic_prompt,
            "messages":        llm_messages,
            "chunk_ids":       chunk_ids,
            "language":        language,
        }

    def _load_frustration_keywords(self) -> list[str]:
        """Charge les mots clés de frustration depuis la DB."""
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