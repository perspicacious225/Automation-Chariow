"""
conversation/output_parser.py — Parser XML pour le format de sortie LLM v2.5
===========================================================================
Extrait <decision>, <message> et <state> de la réponse LLM.
Valide la cohérence entre decision et message (Contraintes strictes).
Protection anti-fuite de prompt (Checklists).


Usage dans manager.py :
    from webhook_app.conversation.output_parser import parse_llm_output, validate_output

    output = parse_llm_output(response_text, current_state)
    output = validate_output(output, current_state, phone=phone, conversation=conversation)
    response_clean = output.message
    new_state = output.state
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── States réservés aux webhooks 
WEBHOOK_ONLY_STATES = {"payment_failed", "payment_abandoned", "payment_success"}

# ── States nécessitant un paiement vérifié 
PAYMENT_REQUIRED_STATES = {"post_sale", "support"}

# ── States valides 
VALID_STATES = {
    "new_prospect", "interested_lead", "pre_sale",
    "payment_failed", "payment_abandoned", "payment_success",
    "post_sale", "support", "escalation",
}

# ── Régression interdite 
NO_REGRESSION = {
    "pre_sale": {"interested_lead", "new_prospect"},
}

# ════════════════════════════════════════
#  Constantes de classification des types
# ════════════════════════════════════════
#
# IMPOSSIBLE_WITHOUT_PURCHASE : types sémantiquement impossibles en zone vendeur
#   sans achat vérifié → blocage total → message vérification
#
# REDIRECT_TO_VERIFY : types possibles en zone vendeur mais qui doivent
#   rester sur la piste vérification → empathie + redirection
#
# NEVER_INTERCEPT : types légitimes en zone vendeur ET support
#   → jamais interceptés par le Fix 4
#
# Cartographie complète :
#   VENDOR exclusif  : question_produit, objection_*, demande_achat,
#                      incident_paiement
#   SUPPORT exclusif : probleme_technique, suivi_resolution
#   COMMUN           : salutation, confirmation_paiement, demande_support,
#                      frustration, hors_sujet
# ═══════════════════════════════════════════════════════════════════════════

# Blocage total — impossible sans achat vérifié sur le produit concerné
IMPOSSIBLE_WITHOUT_PURCHASE = {
    "demande_support",
    "probleme_technique",
    "suivi_resolution",    # résolution impossible sans support autorisé
}

# Empathie + redirection vérification — possible mais sans technique
REDIRECT_TO_VERIFY = {
    "frustration",
}

# Jamais intercepté — légitime dans tous les états
NEVER_INTERCEPT = {
    "salutation",            
    "incident_paiement",      
    "confirmation_paiement",  
    "hors_sujet",
    "question_produit",
    "demande_achat",
    "objection_credibilite",
    "objection_prix",
    "objection_urgence",
    "objection_desir",
    "objection_preuve",
}

# États vendeurs : aucun achat validé attendu
VENDOR_STATES_CHECK = {
    "new_prospect", "interested_lead", "pre_sale",
    "payment_failed", "payment_abandoned",
}

# Messages de blocage standard
_MSG_VERIF_REQUISE = (
    "Je ne peux fournir aucune assistance technique sans vérifier "
    "votre commande au préalable. "
    "Quel email avez-vous utilisé pour le paiement ? 🔍"
)

_MSG_FRUSTRATION_REDIRECT = (
    "Je comprends votre frustration. "
    "Pour vous aider, je dois d'abord vérifier votre commande. "
    "Quel email avez-vous utilisé pour le paiement ? 🔍"
)


# ═══════════════
# DATACLASS SORTIE
# ═══════════════

@dataclass
class LLMOutput:
    """Résultat structuré du parsing de la réponse LLM."""
    message: str
    state: str
    decision: str = ""
    raw: str = ""
    is_valid: bool = True
    validation_notes: list[str] = field(default_factory=list)
    # Champs extraits de <decision>
    produit_cible: str = "inconnu"         # intention d'achat
    decision_type: str = ""
    decision_strategie: str = ""
    decision_contraintes: str = ""
    decision_support_status: str = ""
    decision_produit_support: str = ""     
    # Signal escalade pour manager.py
    escalade_signal: bool = False


# ═══════════════
# PARSING
# ═══════════════

def parse_llm_output(response_text: str, current_state: str) -> LLMOutput:
    """Parse la réponse XML du LLM."""

    output = LLMOutput(raw=response_text, message="", state=current_state)
    output.state = current_state

    # ── Tentative 1 — Format XML complet
    message_match  = re.search(r'<message>(.*?)</message>',  response_text, re.DOTALL)
    state_match    = re.search(r'<state>(.*?)</state>',       response_text, re.DOTALL)
    decision_match = re.search(r'<decision>(.*?)</decision>', response_text, re.DOTALL)

    match_produit = re.search(r'produit_cible:\s*([^\n<]+)', response_text)
    output.produit_cible = match_produit.group(1).strip() if match_produit else "inconnu"

    if message_match:
        output.message = message_match.group(1).strip()

        if state_match:
            output.validation_notes.append(
                f"<state> résiduel détecté et ignoré : {state_match.group(1).strip()}"
            )

        if decision_match:
            output.decision = decision_match.group(1).strip()
            _parse_decision_fields(output)

        logger.info(
            "parse_llm_output XML — state=%s | type=%s | produit=%s | produit_support=%s",
            output.state, output.decision_type,
            output.produit_cible, output.decision_produit_support,
        )
        return output

    # ── Tentative 2 — Format ancien [STATE:xxx] 
    state_tag_match = re.search(
        r'\[STATE:(new_prospect|interested_lead|pre_sale|payment_failed|'
        r'payment_abandoned|payment_success|post_sale|support|escalation)\]',
        response_text,
    )
    if state_tag_match:
        output.state   = state_tag_match.group(1)
        output.message = re.sub(r'\[STATE:[^\]]+\]', '', response_text).strip()
        output.validation_notes.append("format ancien [STATE:xxx] détecté")
        # logger.info("parse_llm_output LEGACY — state=%s", output.state)
        return output

    # ── Tentative 3 — Aucun format reconnu 
    output.message = response_text.strip()
    output.state   = current_state
    output.validation_notes.append("aucun format reconnu → message brut + state actuel")
    logger.warning("parse_llm_output FALLBACK — aucun format détecté")
    return output


def _parse_decision_fields(output: LLMOutput) -> None:
    """Extrait les champs clé-valeur du bloc <decision> v2.5."""
    decision = output.decision

    field_map = {
        "type":            "decision_type",
        "strategie":       "decision_strategie",
        "contraintes":     "decision_contraintes",
        "support_status":  "decision_support_status",
        "produit_support": "decision_produit_support",
    }

    for key, attr in field_map.items():
        match = re.search(rf'^{key}:\s*(.*)$', decision, re.MULTILINE)
        if match:
            setattr(output, attr, match.group(1).strip())


# ═══════════════
# RÉSOLUTION STATE
# ═══════════════

STICKY_STATES = {"payment_failed", "payment_abandoned"}

PRODUCT_TYPES = {
    "question_produit", "objection_credibilite", "objection_prix",
    "objection_urgence", "objection_desir", "objection_preuve",
}


def _resolve_state(
    current_state: str,
    decision_type: str,
    has_escalade: bool = False,
    transactions: list = None,
) -> str:
    """Résolution déterministe du state de sortie."""

    if has_escalade:
        return "escalation"
    if current_state == "escalation":
        return "escalation"
    if current_state in STICKY_STATES:
        return current_state

    # ── Reverse Bridge : Vendeur → Support 
    if decision_type == "demande_support":
        has_confirmed_purchase = False
        if transactions:
            has_confirmed_purchase = any(
                tx.get("transaction_type") == "confirmed" for tx in transactions
            )
        if has_confirmed_purchase or current_state in ("post_sale", "support", "payment_success"):
            return "support"
        else:
            return current_state

    # ── incident_paiement → reste dans l'état actuel 
    if decision_type == "incident_paiement":
        return current_state

    if current_state == "payment_success":
        if decision_type in ("probleme_technique", "frustration"):
            return "support"
        return "post_sale"

    if current_state in ("post_sale", "support"):
        if decision_type in PRODUCT_TYPES:
            return "interested_lead"
        if decision_type == "demande_achat":
            return "pre_sale"
        if decision_type in ("probleme_technique", "frustration"):
            return "support"
        if decision_type == "suivi_resolution":
            return "post_sale"
        return current_state

    if current_state == "new_prospect":
        if decision_type in ("demande_achat", "confirmation_paiement"):
            return "pre_sale"
        if decision_type in PRODUCT_TYPES:
            return "interested_lead"
        return "new_prospect"

    if current_state == "interested_lead":
        if decision_type in ("demande_achat", "confirmation_paiement"):
            return "pre_sale"
        return "interested_lead"

    if current_state == "pre_sale":
        return "pre_sale"

    return current_state


# ══════════════════════════════════════
# RÉSOLUTION PRODUIT SUPPORT (Fix 4b)
# ══════════════════════════════════════

def _resolve_support_product_id(
    decision_produit_support: str,
    conversation: dict,
) -> str | None:
    """
    Résout le product_id du produit concerné par la demande technique.

    Priorité :
      1. Ce que le LLM a déclaré dans produit_support (ID exact commençant par prd_)
      2. product_id de la conversation (produit vérifié/acheté en DB)
      3. None → scope inconnu → laisser passer sans bloquer

\\
    produit_cible = intention d'achat (nouveau produit).
    produit_support = produit déjà acheté concerné par le support.
    Ces deux champs sont sémantiquement distincts.
    """
    raw = (decision_produit_support or "").strip().lower()
    if raw and raw not in ("vide", "") and raw.startswith("prd_"):
        return raw

    conv_product = conversation.get("product_id")
    if conv_product:
        return conv_product

    return None


# ═══════════════
# VALIDATION
# ═══════════════

def validate_output(
    output: LLMOutput,
    current_state: str,
    phone: str = "",
    conversation: dict = None,
) -> LLMOutput:
    """
    Valide et corrige la sortie parsée.
    Applique le Filet de Sécurité (Couche 3).
    """
    conversation = conversation or {}

    # ── 1. Nettoyage résiduel ancien format 
    output.message = re.sub(r'\[ESCALADE_REQUISE\]', '', output.message).strip()

    # ── 2. Détecter support_status: exhausted 
    output.escalade_signal = (
        output.decision_support_status.strip().lower() == "exhausted"
    )
    if output.escalade_signal:
        output.validation_notes.append("support_status=exhausted → escalade par code")

    # ── 3. State déterministe + fetch transactions 

    transactions = []
    if phone:
        try:
            from webhook_app.conversation.context_builder import _fetch_customer_transactions
            transactions = _fetch_customer_transactions(phone)
        except Exception as e:
            logger.warning("Erreur fetch transactions : %s", e)

    output.state = _resolve_state(
        current_state=current_state,
        decision_type=output.decision_type,
        has_escalade=getattr(output, 'escalade_signal', False),
        transactions=transactions,
    )

    if output.state != current_state:
        output.validation_notes.append(
            f"state : {current_state} → {output.state} (type={output.decision_type})"
        )

    # ── 4. Anti-Fuite de Prompt 
    if re.search(r'\[\s?[xX ]\s?\]|COMPOSANTS OBLIGATOIRES|INTERDITS ABSOLUS', output.message):
        output.validation_notes.append("VIOLATION — Fuite de format de checklist dans le message")
        output.is_valid = False

    # ── 5. Validation contraintes contre le message 
    contraintes = {
        c.strip()
        for c in output.decision_contraintes.split(",")
        if c.strip() and c.strip() != "aucune"
    }

    if "no_price" in contraintes:
        if re.search(r'\d[\d\s.,]*\s*(?:XOF|CFA|FCFA|€|\$|EUR|USD)', output.message, re.IGNORECASE):
            output.validation_notes.append("VIOLATION contrainte no_price — montant détecté")
            output.is_valid = False

    if "no_email_ask" in contraintes:
        if re.search(r'(quel|votre|ton|un)\s*(e-?mail|adresse\s*mail|adresse)', output.message, re.IGNORECASE):
            output.validation_notes.append("VIOLATION contrainte no_email_ask — demande email détectée")
            output.is_valid = False

    if "no_confirm" in contraintes:
        if re.search(
            r'(accès\s*(est\s*)?activ|ta\s*licence\s*est\s*prête|'
            r'ton\s*achat\s*est\s*confirm|bienvenue\s*(en\s*tant\s*que\s*client|dans\s*ton))',
            output.message, re.IGNORECASE,
        ):
            output.validation_notes.append("VIOLATION contrainte no_confirm — confirmation détectée")
            output.is_valid = False

    if "clarify_only" in contraintes:
        if '?' not in output.message:
            output.validation_notes.append("VIOLATION contrainte clarify_only — aucune question détectée")
            output.is_valid = False
        if re.search(r'\d{3,}', output.message):
            output.validation_notes.append("VIOLATION contrainte clarify_only — chiffre détecté")
            output.is_valid = False

    # ── 6. VERIFY_PAYMENT — validation identifiant 
    verify_match = re.search(r'\[VERIFY_PAYMENT:([^\]]+)\]', output.message)
    if verify_match:
        identifier = verify_match.group(1).strip()
        is_email = '@' in identifier and '.' in identifier
        is_phone = re.match(r'^[\d\s+()-]{7,}$', identifier)
        if not is_email and not is_phone:
            output.validation_notes.append(f"VERIFY_PAYMENT placeholder invalide : '{identifier}'")
            output.message  = re.sub(r'\[VERIFY_PAYMENT:[^\]]+\]', '', output.message).strip()
            output.is_valid = False

    # ── 7. Vocabulaire interne 
    vocab_interdit = re.compile(
        r'\b(escalad\w*|équipe technique|en interne|ticket\b|transférer)',
        re.IGNORECASE,
    )
    if vocab_interdit.search(output.message):
        output.validation_notes.append("Vocabulaire interne détecté dans message — à surveiller")

    # ── 8.Anti-fuite message support en zone vendeur ──
    #
    # Cartographie des types :
    #   IMPOSSIBLE_WITHOUT_PURCHASE  : demande_support, probleme_technique, suivi_resolution
    #   REDIRECT_TO_VERIFY           : frustration
    #   NEVER_INTERCEPT              : salutation, incident_paiement, confirmation_paiement,
    #                                  hors_sujet, objection_*, demande_achat, question_produit

    if (
        current_state in VENDOR_STATES_CHECK
        and output.decision_type not in NEVER_INTERCEPT
    ):

        if output.decision_type in IMPOSSIBLE_WITHOUT_PURCHASE:

            # Résoudre le produit concerné par la demande
            support_product_id = _resolve_support_product_id(
                output.decision_produit_support,
                conversation,
            )

            # Match : achat confirmed sur ce produit exact
            has_confirmed_for_product = any(
                tx.get("transaction_type") == "confirmed"
                and (
                    support_product_id is None
                    or tx.get("product_id") == support_product_id
                )
                for tx in transactions
            )

            if not has_confirmed_for_product:
                output.validation_notes.append(
                    f"BLOCAGE — type={output.decision_type} "
                    f"state={current_state} "
                    f"produit_support={support_product_id or 'inconnu'} : "
                    f"aucun achat confirmé pour ce produit"
                )
                output.is_valid = False
                output.message  = _MSG_VERIF_REQUISE

            else:
                output.validation_notes.append(
                    f"AUTORISÉ — type={output.decision_type} "
                    f"state={current_state} "
                    f"produit_support={support_product_id} : achat confirmé ✅"
                )

        elif output.decision_type in REDIRECT_TO_VERIFY:

            # Frustration sans aucun achat confirmé → rediriger vers vérification
            has_any_confirmed = any(
                tx.get("transaction_type") == "confirmed"
                for tx in transactions
            )

            if not has_any_confirmed:
                output.validation_notes.append(
                    f"REDIRECT — type={output.decision_type} "
                    f"state={current_state} : frustration sans achat confirmé"
                )
                output.message = _MSG_FRUSTRATION_REDIRECT

    # ── Log final 
    if output.validation_notes:
        logger.info("validate_output — notes: %s", " | ".join(output.validation_notes))

    return output