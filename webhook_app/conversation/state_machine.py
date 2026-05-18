"""
conversation/state_machine.py — Machine à états conversationnels
=================================================================
Transitions d'état basées sur deux sources :

  Priorité 1 — Tag LLM [STATE:xxx] extrait dans manager.py
               Le LLM comprend le contexte global — source principale

  Priorité 2 — Détection par signaux textuels (fallback)
               Si le LLM n'a pas inséré de tag valide

Règle fondamentale :
  La state machine ne doit JAMAIS escalader sur un signal
  de frustration verbale — c'est au LLM de gérer.
  L'escalade automatique est réservée aux demandes explicites
  d'intervention humaine ou aux litiges financiers réels.
"""

import logging
import re
import unicodedata

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# NORMALISATION
# ══════════════════════════════════════════════════════════════════════════════

def normalize(text: str) -> str:
    """
    Normalise un texte pour la détection de signaux.
    Résistant aux variations orthographiques fréquentes sur WhatsApp
    en Afrique francophone :
      - Minuscules
      - Suppression des accents (é→e, è→e, ç→c, à→a...)
      - Apostrophes et tirets → espace
      - Ponctuation → espace
      - Espaces multiples → un seul
    """
    # Minuscules
    text = text.lower()

    # Supprimer les accents
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")

    # Apostrophes et tirets → espace
    text = re.sub(r"[''`\-]", " ", text)

    # Ponctuation résiduelle → espace
    text = re.sub(r"[^\w\s]", " ", text)

    # Espaces multiples → un seul
    text = re.sub(r"\s+", " ", text).strip()

    return text


def _normalize_signals(signals: list[str]) -> list[str]:
    """Normalise une liste de signaux au chargement du module."""
    return [normalize(s) for s in signals]


def _contains_signal(text: str, signals: list[str]) -> bool:
    """
    Vérifie si le texte normalisé contient l'un des signaux.
    Les signaux sont déjà normalisés — comparaison par sous-chaîne.
    """
    norm = normalize(text)
    return any(signal in norm for signal in signals)


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAUX
# ══════════════════════════════════════════════════════════════════════════════

# ── Signaux d'intérêt informatif ──────────────────────────────────────────────
# Déclenchent new_prospect → interested_lead
# Le client s'informe avant de décider d'acheter
INTEREST_SIGNALS = _normalize_signals([
    # Français — demande d'information générale
    "en savoir plus", "plus d informations", "plus de details",
    "dites m en plus", "expliquez moi", "parlez moi de",
    "presentez moi", "c est quoi", "qu est ce que",
    "comment ca marche", "ca fait quoi", "ca inclut quoi",
    "ca contient quoi", "ca marche comment", "qu est ce que c est",
    "kesako", "c est quoi exactement", "vous proposez quoi",
    "vous avez quoi", "qu avez vous",
    # Noms de produits détectés directement
    "microsoft 365", "office 365", "excel ia",
    "formation excel", "formation video", "formation ia",
    "licence office", "licence microsoft",
    # Anglais
    "tell me more", "more information", "more details",
    "what is", "how does it work", "what does it include",
    "what do you offer", "what do you have",
])

# ── Signaux d'intention d'achat ───────────────────────────────────────────────
# Déclenchent → pre_sale ou interested_lead selon l'état courant
PURCHASE_SIGNALS = _normalize_signals([
    # Français
    "je veux acheter", "je veux commander", "comment payer",
    "comment acheter", "je prends", "je veux le",
    "combien ca coute", "c est combien", "quel est le prix",
    "lien de paiement", "je veux payer", "comment commander",
    "je souhaite acheter", "je voudrais acheter",
    "je veux bien", "envoie moi le lien", "le lien pour payer",
    "comment je fais pour payer", "comment proceder",
    # Variantes sans ponctuation
    "lien paiement", "je veux ca",
    # Anglais
    "i want to buy", "how to pay", "how much",
    "what is the price", "payment link", "i want to order",
    "i m interested in buying", "how do i pay",
    "send me the link", "i want to purchase",
])

# ── Signaux de support post-achat ─────────────────────────────────────────────
# Le client a (ou dit avoir) payé et rencontre un problème
SUPPORT_SIGNALS = _normalize_signals([
    # Accès et réception
    "j ai paye", "jai paye", "j ai achete", "j ai commande",
    "je n ai pas recu", "jai rien recu", "pas recu",
    "je n ai rien recu", "rien recu",
    # Problèmes techniques
    "ca ne marche pas", "ca marche pas", "ca ne fonctionne pas",
    "ca fonctionne pas", "probleme", "erreur", "j ai un souci",
    "j ai un probleme", "je n arrive pas", "je narrive pas",
    "je ne trouve pas", "bloque", "bloquee", "bloqué",
    # Accès produit
    "comment utiliser", "comment acceder", "comment activer",
    "mon acces", "ma formation", "mon lien", "ma licence",
    "installation", "activer", "activation", "cle d activation",
    "la cle ne marche pas", "cle invalide",
    # Aide générale
    "aide moi", "j ai besoin d aide", "besoin d aide",
    "pouvez vous m aider", "aidez moi",
    # Anglais
    "i paid", "i bought", "i can t", "it doesn t work",
    "not working", "problem", "error", "i have an issue",
    "how to use", "how to access", "how to activate",
    "my access", "i didn t receive", "i haven t received",
    "stuck", "blocked", "help me", "need help",
])

# ── Signaux de résolution ─────────────────────────────────────────────────────
# support → post_sale quand le problème est résolu
RESOLUTION_SIGNALS = _normalize_signals([
    # Français
    "c est bon", "cest bon", "ca marche", "ca fonctionne",
    "c est regle", "c est resolu", "probleme resolu",
    "c est ok", "nickel", "parfait merci", "tout fonctionne",
    "ca y est", "ca marche maintenant", "ca fonctionne maintenant",
    "merci ca marche", "merci beaucoup", "super merci",
    "c est parfait", "ca a marche", "c est fait",
    "j ai acces", "j ai reussi", "ca s est installe",
    # Anglais
    "it works", "it s working", "problem solved", "all good",
    "fixed", "resolved", "working now", "it worked",
    "thank you it works", "got access", "i can access",
    "installation done", "it s done",
])

# ── Signaux d'escalade — demandes explicites uniquement ───────────────────────
# NE PAS inclure : "arnaque", "escroquerie", "impossible"
# → Frustration verbale → le LLM gère avec empathie
# INCLURE : demandes explicites d'humain + litige financier réel
ESCALATION_SIGNALS = _normalize_signals([
    # Français — demande explicite d'humain
    "parler a quelqu un", "parler a un humain", "parler a un agent",
    "je veux un responsable", "un responsable s il vous plait",
    "service client humain", "je veux parler a une personne",
    "un vrai humain", "quelqu un de reel",
    # Français — litige financier explicite
    "je veux etre rembourse", "je veux mon remboursement",
    "rendez moi mon argent", "je veux annuler ma commande",
    "remboursez moi", "je demande un remboursement",
    # Anglais — demande explicite d'humain
    "speak to a human", "talk to someone real",
    "real person", "speak to an agent", "i want a human",
    # Anglais — litige financier
    "i want a refund", "i need a refund",
    "give me my money back", "refund please",
])

# Tag LLM pour l'escalade
LLM_ESCALATION_TAG = "[ESCALADE_REQUISE]"


# ══════════════════════════════════════════════════════════════════════════════
# STATE MACHINE
# ══════════════════════════════════════════════════════════════════════════════

class StateMachine:
    """
    Gère les transitions d'états conversationnels — rôle de fallback.
    La source principale de transition est le tag LLM extrait dans manager.py.
    Cette state machine intervient uniquement si le tag est absent ou invalide.
    """

    def transition(
        self,
        current_state: str,
        user_message: str,
        assistant_response: str,
        conversation: dict,
    ) -> str | None:
        """
        Calcule le prochain état en fonction des signaux textuels.
        Retourne le nouvel état ou None si pas de transition détectée.
        """

        # ── Priorité 1 — Escalade signalée par le LLM ─────────────────────
        # Le LLM a épuisé les options et insère [ESCALADE_REQUISE]
        if LLM_ESCALATION_TAG in assistant_response:
            logger.info("[StateMachine] Escalade signalée par le LLM.")
            return "escalation"

        # ── Priorité 2 — Demande explicite d'humain ou litige financier ───
        # Uniquement les signaux explicites — pas la frustration verbale
        if _contains_signal(user_message, ESCALATION_SIGNALS):
            logger.info("[StateMachine] Demande explicite d'humain détectée.")
            return "escalation"

        # ── new_prospect ───────────────────────────────────────────────────
        if current_state == "new_prospect":
            if _contains_signal(user_message, INTEREST_SIGNALS):
                logger.info("[StateMachine] new_prospect → interested_lead (intérêt)")
                return "interested_lead"
            if _contains_signal(user_message, PURCHASE_SIGNALS):
                logger.info("[StateMachine] new_prospect → interested_lead (achat)")
                return "interested_lead"
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] new_prospect → support")
                return "support"
            return None

        # ── interested_lead ────────────────────────────────────────────────
        if current_state == "interested_lead":
            if _contains_signal(user_message, PURCHASE_SIGNALS):
                logger.info("[StateMachine] interested_lead → pre_sale")
                return "pre_sale"
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] interested_lead → support")
                return "support"
            return None

        # ── pre_sale ───────────────────────────────────────────────────────
        if current_state == "pre_sale":
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] pre_sale → support")
                return "support"
            return None

        # ── payment_failed / payment_abandoned ─────────────────────────────
        if current_state in ("payment_failed", "payment_abandoned"):
            if _contains_signal(user_message, PURCHASE_SIGNALS):
                logger.info("[StateMachine] %s → pre_sale", current_state)
                return "pre_sale"
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] %s → support", current_state)
                return "support"
            return None

        # ── payment_success ────────────────────────────────────────────────
        # Transition vers post_sale dès que le client interagit
        # (pas automatiquement — attendre une vraie réponse)
        if current_state == "payment_success":
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] payment_success → support")
                return "support"
            if len(user_message.strip()) > 5:
                logger.info("[StateMachine] payment_success → post_sale")
                return "post_sale"
            return None

        # ── post_sale ──────────────────────────────────────────────────────
        if current_state == "post_sale":
            if _contains_signal(user_message, SUPPORT_SIGNALS):
                logger.info("[StateMachine] post_sale → support")
                return "support"
            return None

        # ── support ────────────────────────────────────────────────────────
        # Retour vers post_sale si le problème est résolu
        if current_state == "support":
            if _contains_signal(user_message, RESOLUTION_SIGNALS):
                logger.info("[StateMachine] support → post_sale (problème résolu)")
                return "post_sale"
            return None

        # ── escalation ─────────────────────────────────────────────────────
        # Sortie uniquement via tags admin (#REPRISE, #RESOLU)
        # La state machine ne gère pas la sortie de l'escalade
        if current_state == "escalation":
            return None

        return None

    def should_escalate(self, user_message: str) -> bool:
        """
        Vérification rapide avant appel LLM.
        Réservée aux demandes explicites d'humain — pas à la frustration.
        """
        return _contains_signal(user_message, ESCALATION_SIGNALS)