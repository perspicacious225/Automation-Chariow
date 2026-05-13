"""
conversation/state_machine.py — Machine à états conversationnels
=================================================================
Détermine les transitions d'état basées sur :
  - L'état actuel
  - Le contenu du message utilisateur
  - La réponse générée par le LLM
  - Le contexte transactionnel

Les transitions sont déterministes et basées sur des signaux textuels.
Le LLM peut aussi signaler une escalade via un tag spécial dans sa réponse.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── Signaux textuels détectés dans les messages utilisateur ──────────────────

# Signaux d'intention d'achat
PURCHASE_SIGNALS = [
    "je veux acheter", "je veux commander", "comment payer",
    "comment acheter", "je suis intéressé", "ça m'intéresse",
    "je prends", "je veux le", "combien ça coûte", "c'est combien",
    "quel est le prix", "lien de paiement", "je veux payer",
]

# Signaux de support post-achat
SUPPORT_SIGNALS = [
    "j'ai payé", "j'ai acheté", "j'ai commandé", "j'ai accès",
    "je n'arrive pas", "ça ne marche pas", "problème", "erreur",
    "aide moi", "j'ai un souci", "ça ne fonctionne pas",
    "je ne trouve pas", "comment utiliser", "comment accéder",
    "mon accès", "ma formation", "mon lien",
]

# Signaux d'escalade vers humain
ESCALATION_SIGNALS = [
    "remboursement", "rembourser", "je veux être remboursé",
    "parler à quelqu'un", "parler à un humain", "parler à un agent",
    "service client", "responsable", "plainte", "arnaque",
    "je veux annuler", "annulation",
]

# Tag que le LLM peut insérer pour signaler une escalade nécessaire
LLM_ESCALATION_TAG = "[ESCALADE_REQUISE]"


class StateMachine:
    """
    Gère les transitions d'états de la conversation.
    """

    def transition(
        self,
        current_state: str,
        user_message: str,
        assistant_response: str,
        conversation: dict,
    ) -> str | None:
        """
        Calcule le prochain état en fonction du contexte.
        Retourne le nouvel état ou None si pas de transition.
        """
        msg = user_message.lower()

        # Priorité 1 : le LLM a signalé une escalade
        if LLM_ESCALATION_TAG in assistant_response:
            logger.info("Escalade signalée par le LLM.")
            return "escalation"

        # Priorité 2 : signaux d'escalade dans le message utilisateur
        if any(signal in msg for signal in ESCALATION_SIGNALS):
            logger.info("Signal d'escalade détecté dans le message.")
            return "escalation"

        # Depuis un état post-paiement : détecter le besoin de support
        if current_state in ("payment_success", "post_sale"):
            if any(signal in msg for signal in SUPPORT_SIGNALS):
                return "support"
            # Reste en post_sale après paiement réussi
            if current_state == "payment_success":
                return "post_sale"

        # Depuis new_prospect ou interested_lead
        if current_state in ("new_prospect", "interested_lead"):
            if any(signal in msg for signal in PURCHASE_SIGNALS):
                return "pre_sale"
            if any(signal in msg for signal in SUPPORT_SIGNALS):
                # Le prospect mentionne un problème → peut-être déjà client
                return "support"
            if current_state == "new_prospect" and len(msg) > 10:
                # Il engage la conversation → interested_lead
                return "interested_lead"

        # Depuis pre_sale
        if current_state == "pre_sale":
            if any(signal in msg for signal in SUPPORT_SIGNALS):
                return "support"

        # Depuis payment_failed ou payment_abandoned
        if current_state in ("payment_failed", "payment_abandoned"):
            if any(signal in msg for signal in PURCHASE_SIGNALS):
                return "pre_sale"
            if any(signal in msg for signal in SUPPORT_SIGNALS):
                return "support"

        # Depuis support
        if current_state == "support":
            # Reste en support sauf escalade (déjà gérée)
            pass

        # Pas de transition détectée
        return None

    def should_escalate(self, user_message: str) -> bool:
        """
        Vérification rapide si un message nécessite une escalade immédiate.
        Utilisé avant l'appel LLM pour les cas évidents.
        """
        msg = user_message.lower()
        return any(signal in msg for signal in ESCALATION_SIGNALS)