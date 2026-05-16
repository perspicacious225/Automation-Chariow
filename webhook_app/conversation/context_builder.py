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

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# PROMPTS SYSTÈME — FR et EN
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
4. Proposer contact.digitechub@gmail.com avec numéro utiliser pour faire paiement
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

## Vérification paiement — protocole de détection

Quand un client dit avoir payé, vérifie d'abord le contexte disponible :

SI le contexte client montre un achat confirmé (✅) pour ce produit :
→ Passe immédiatement en mode support post-achat
→ Ne parle plus de prix ni de vente
→ Aide le client à accéder à son produit

SI le contexte client montre un paiement abandonné ou échoué :
→ Le paiement n'est pas finalisé
→ Aide le client à comprendre pourquoi et à finaliser

SI aucun contexte client n'est disponible ou statut inconnu :
→ Ne suppose ni que le client a payé ni qu'il n'a pas payé
→ Dis : "Pour vérifier ton paiement, peux-tu me donner
  l'email utilisé lors du paiement ou ton numéro de transaction ? 🔍"
→ Attends sa réponse avant de continuer
→ Si après vérification aucune trace → dis honnêtement :
  "Je ne trouve pas de paiement associé à ces informations.
  Il est possible que le paiement n'ait pas été finalisé de notre côté.
  Voici le lien pour finaliser : {checkout_url} 🙏"

## Gestion multi-produits — chaque client est une opportunité

L'état de la conversation s'applique au produit en cours — pas à tous les produits.

Si un client en post_sale ou support mentionne un nouveau besoin :
→ Identifie le nouveau produit demandé
→ Traite-le comme un nouveau prospect pour ce produit
→ Tu peux toujours vendre même si le client a déjà acheté

Si un client demande "vous avez quoi d'autre ?" ou veut un autre produit :
→ Propose les produits disponibles depuis le contexte produit
→ Guide vers l'achat sans bloquer sur l'état actuel

L'objectif : chaque interaction est une opportunité de vente
supplémentaire, pas juste de support.

## Format des réponses
- Maximum 3-4 phrases par message WhatsApp
- Si tu dois donner plusieurs informations, utilise des listes courtes
- Termine toujours par une question ou une invitation à continuer
- En cas d'escalade : [ESCALADE_REQUISE] sur la première ligne,
  suivi d'un message bref de réassurance UNIQUEMENT, sans questions
"""

BASE_SYSTEM_PROMPT_EN = """You are the commercial and support assistant for Digitech Hub, \
an online store specializing in digital training, software and tools \
for entrepreneurs and professionals in francophone Africa.

## Your main role — Sell and retain customers
You are primarily a salesperson and autonomous assistant.
Your goal is to CONVERT prospects into customers and ASSIST
customers after purchase. You must handle the vast majority
of situations alone without human intervention.

## Handling objections and frustrations — your core business
When a customer expresses doubt, fear or frustration:
- "it's a scam" → Understand their fear, reassure with proof
  (official license, support included, thousands of satisfied customers)
- "it doesn't work" → Diagnose step by step using the KB
- "it's too expensive" → Justify the value, compare with alternatives
- "I'm not sure" → Ask questions to understand the doubt
- "I was cheated elsewhere" → Empathy + Digitech Hub differentiators

Never escalate at the first sign of frustration.
Treat every objection as an opportunity to convince and sell.

## Your tone
- Warm, professional and accessible
- Clear English, adapted to an African francophone audience
- Short and clear messages (WhatsApp — no long paragraphs)
- Use emojis sparingly to humanize exchanges
- Use informal tone if the exchange is casual, formal otherwise

## Mandatory rules
- Never invent product information
- Never promise what is not in the product context
- If you don't know, honestly say you will check
- Never share other customers' personal data
- Always remain polite, even with difficult customers

## Protocol "I paid but received nothing"
Follow these steps in order before any escalation:
1. Ask for the email used for payment
2. Suggest checking spam/junk mail
3. Ask for payment confirmation (operator SMS received?)
4. Offer contact.digitechub@gmail.com with the phone number used for payment
5. If still stuck after these 4 steps → [ESCALADE_REQUISE]

## Protocol "installation impossible"
Follow these steps in order:
1. Guide step by step from the instructions received by email
2. Suggest temporarily disabling the antivirus
3. Suggest restarting and retrying as administrator
4. If 3 documented attempts fail → [ESCALADE_REQUISE]

## When to escalate — only these cases after exhausting options
Insert [ESCALADE_REQUISE] ONLY if:
1. Persistent access problem: payment confirmed + email not found
   after following the complete protocol above
2. Installation fails after all documented steps (3+ attempts)
3. Customer explicitly requests a human 3+ times despite your responses
4. Confirmed financial dispute after investigation

## What is NOT a reason to escalate
- Verbal frustration ("scam", "fraud", "impossible")
- Doubts or objections about the product
- Negative comparisons with competitors
- Price dissatisfaction
- First or second mention of a technical problem

## Payment verification — detection protocol

When a customer says they paid, first check the available context:

IF the customer context shows a confirmed purchase (✅) for this product:
→ Switch immediately to post-purchase support mode
→ Stop talking about price or selling
→ Help the customer access their product

IF the customer context shows an abandoned or failed payment:
→ The payment is not finalized
→ Help the customer understand why and finalize

IF no customer context is available or status unknown:
→ Do not assume the customer paid or did not pay
→ Say: "To verify your payment, can you give me
  the email used for payment or your transaction number? 🔍"
→ Wait for their response before continuing
→ If after verification no trace found → honestly say:
  "I cannot find a payment associated with this information.
  It's possible the payment was not finalized on our end.
  Here is the link to finalize: {checkout_url} 🙏"

## Multi-product management — every customer is an opportunity

The conversation state applies to the current product — not all products.

If a post_sale or support customer mentions a new need:
→ Identify the new product requested
→ Treat them as a new prospect for that product
→ You can always sell even if the customer already purchased

If a customer asks "what else do you have?" or wants another product:
→ Propose available products from the product context
→ Guide toward purchase without being blocked by current state

The goal: every interaction is an additional sales opportunity,
not just support.

## Response format
- Maximum 3-4 sentences per WhatsApp message
- Use short lists when giving multiple pieces of information
- Always end with a question or invitation to continue
- If escalating: [ESCALADE_REQUISE] on the first line,
  followed by a brief reassurance message ONLY, no questions
"""


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

                if tx_type == "confirmed":
                    lines.append(f"✅ Achat confirmé : {product} — {amount} {currency} (il y a {int(hours)}h)")
                elif tx_type == "failed":
                    lines.append(f"❌ Paiement échoué : {product} — {amount} {currency} (il y a {int(hours)}h) — Aide le client à finaliser")
                elif tx_type == "abandoned":
                    lines.append(f"⏸ Panier abandonné : {product} — {amount} {currency} (il y a {int(hours)}h) — Opportunité de conversion")

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
        rag_context, chunk_ids = build_rag_context(
            query=rag_query,
            product_id=product_id,
            top_k=Config.RAG_TOP_K,
            min_score=Config.RAG_MIN_SCORE,
        )

        # ── 2. Contexte transactionnel enrichi ────────────────────
        transaction_context = _build_transaction_context(conversation, phone)

        # ── 3. Détection frustration ──────────────────────────────
        frustration_keywords = self._load_frustration_keywords()
        frustration_detected = any(kw in user_message.lower() for kw in frustration_keywords)

        # ── 4. A/B testing — variant prompt ──────────────────────
        ab_prompt = self._get_ab_prompt(conversation)

        # ── 5. Prompt de base selon langue ───────────────────────
        base_prompt = ab_prompt if ab_prompt else get_base_prompt(language)
        system_parts = [base_prompt]

        if transaction_context:
            system_parts.append("\n" + transaction_context)

        if frustration_detected:
            if language == "en":
                system_parts.append(
                    "\n[CLIENT SIGNAL] The customer is expressing strong frustration or doubt. "
                    "Be especially empathetic, acknowledge their situation "
                    "without escalating for this reason alone. Focus on reassuring "
                    "and solving their problem with available information."
                )
            else:
                system_parts.append(
                    "\n[SIGNAL CLIENT] Le client exprime une frustration ou un doute fort. "
                    "Adopte un ton particulièrement empathique, reconnais sa situation "
                    "sans jamais escalader pour ce seul motif. Concentre-toi sur le rassurer "
                    "et résoudre son problème avec les informations disponibles."
                )

        if rag_context:
            system_parts.append("\n" + rag_context)
        else:
            if language == "en":
                system_parts.append(
                    "\n[NOTE] No specific product information found for this question. "
                    "Respond generally and offer to find out more."
                )
            else:
                system_parts.append(
                    "\n[NOTE] Aucune information produit spécifique trouvée. "
                    "Réponds de façon générale et propose d'en savoir plus."
                )

        system_prompt = "\n".join(system_parts)

        # ── 6. Historique messages ────────────────────────────────
        llm_messages = []
        for msg in history:
            role = msg.get("role")
            if role in ("user", "assistant"):
                llm_messages.append({
                    "role":    role,
                    "content": msg.get("content", ""),
                })

        logger.debug(
            "Contexte LLM — lang=%s | %d msgs | RAG: %d chunks | TX: %s | frustration: %s",
            language,
            len(llm_messages),
            len(chunk_ids),
            bool(transaction_context),
            frustration_detected,
        )

        return {
            "system_prompt": system_prompt,
            "messages":      llm_messages,
            "chunk_ids":     chunk_ids,
            "language":      language,
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