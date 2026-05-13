"""
llm/engine.py — Moteur LLM
===========================
Appelle le LLM configuré (Anthropic Claude ou OpenAI GPT)
avec le prompt système et l'historique de messages.

Le provider est sélectionné via Config.LLM_PROVIDER.
L'interface est identique pour les deux providers —
le reste du code n'a pas besoin de savoir lequel est utilisé.
"""

import logging
import time
from typing import Optional

from webhook_app.config import Config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# INTERFACE COMMUNE
# ══════════════════════════════════════════════════════════════════════════════

class LLMEngine:
    """
    Moteur LLM unifié — abstrait le provider sous-jacent.
    Instancier une fois par requête dans ConversationManager.
    """

    def __init__(self):
        self.provider = (Config.LLM_PROVIDER or "anthropic").lower()
        self._client = None

    def generate(
        self,
        system_prompt: str,
        messages: list[dict],
        *,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> tuple[str, list[str]]:
        """
        Génère une réponse du LLM.

        Args:
            system_prompt : prompt système complet (rôle + RAG + contexte TX)
            messages      : historique au format [{role, content}, ...]
                            Le dernier message est le message utilisateur courant.
            max_tokens    : limite de tokens (défaut : Config.LLM_MAX_TOKENS)
            temperature   : créativité (0.0 = déterministe, 1.0 = créatif)

        Retourne :
            response_text : str       — réponse générée
            chunk_ids     : list[str] — toujours [] ici (passés via metadata)
        """
        max_tokens = max_tokens or Config.LLM_MAX_TOKENS

        if self.provider == "anthropic":
            return self._generate_anthropic(
                system_prompt, messages, max_tokens, temperature
            )
        elif self.provider == "openai":
            return self._generate_openai(
                system_prompt, messages, max_tokens, temperature
            )
        else:
            raise ValueError(f"Provider LLM inconnu : {self.provider}")

    # ──────────────────────────────────────────────────────────────────────
    # ANTHROPIC — CLAUDE
    # ──────────────────────────────────────────────────────────────────────

    def _get_anthropic_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=Config.LLM_API_KEY)
        return self._client

    def _generate_anthropic(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, list]:

        client = self._get_anthropic_client()

        # Anthropic exige que le premier message soit role="user"
        # et que les rôles alternent strictement user/assistant
        clean_messages = _sanitize_messages_anthropic(messages)

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=Config.LLM_MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_prompt,
                    messages=clean_messages,
                )
                text = response.content[0].text if response.content else ""
                logger.debug(
                    "Anthropic — tokens used: input=%d output=%d",
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
                return text.strip(), []

            except Exception as e:
                err = str(e).lower()
                # Rate limit → retry exponentiel
                if "rate_limit" in err or "overloaded" in err:
                    wait = 2 ** attempt
                    logger.warning(
                        "Anthropic rate limit — attente %ds (tentative %d/3)",
                        wait, attempt + 1
                    )
                    time.sleep(wait)
                    continue
                logger.exception("Erreur Anthropic : %s", e)
                raise

        raise RuntimeError("LLM Anthropic : échec après 3 tentatives.")

    # ──────────────────────────────────────────────────────────────────────
    # OPENAI — GPT
    # ──────────────────────────────────────────────────────────────────────

    def _get_openai_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=Config.LLM_API_KEY)
        return self._client

    def _generate_openai(
        self,
        system_prompt: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, list]:

        client = self._get_openai_client()

        # OpenAI accepte le system prompt comme premier message role="system"
        openai_messages = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(messages)

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=Config.LLM_MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=openai_messages,
                )
                text = response.choices[0].message.content or ""
                logger.debug(
                    "OpenAI — tokens used: prompt=%d completion=%d",
                    response.usage.prompt_tokens,
                    response.usage.completion_tokens,
                )
                return text.strip(), []

            except Exception as e:
                err = str(e).lower()
                if "rate_limit" in err:
                    wait = 2 ** attempt
                    logger.warning(
                        "OpenAI rate limit — attente %ds (tentative %d/3)",
                        wait, attempt + 1
                    )
                    time.sleep(wait)
                    continue
                logger.exception("Erreur OpenAI : %s", e)
                raise

        raise RuntimeError("LLM OpenAI : échec après 3 tentatives.")


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize_messages_anthropic(messages: list[dict]) -> list[dict]:
    """
    Anthropic exige :
    - Au moins un message
    - Premier message role="user"
    - Alternance stricte user → assistant → user → ...
    - Pas de messages consécutifs du même rôle

    Cette fonction nettoie l'historique pour respecter ces contraintes.
    """
    if not messages:
        return []

    clean = []
    for msg in messages:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        # Fusionner les messages consécutifs du même rôle
        if clean and clean[-1]["role"] == role:
            clean[-1]["content"] += "\n" + content
        else:
            clean.append({"role": role, "content": content})

    # S'assurer que le premier message est role="user"
    if clean and clean[0]["role"] != "user":
        clean.pop(0)

    # S'assurer que le dernier message est role="user"
    # (sinon Anthropic retourne une erreur)
    if clean and clean[-1]["role"] != "user":
        clean = clean[:-1]

    return clean