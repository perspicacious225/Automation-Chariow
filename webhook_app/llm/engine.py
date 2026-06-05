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
        dynamic_context: str = "",
        use_cache: bool = True,
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> tuple[str, list[str]]:
        max_tokens = max_tokens or Config.LLM_MAX_TOKENS

        if self.provider == "anthropic":
            return self._generate_anthropic(
                system_prompt, messages, max_tokens, temperature,
                dynamic_context=dynamic_context,
                use_cache=use_cache,              
            )
        elif self.provider == "openai":
            return self._generate_openai(
                system_prompt, messages, max_tokens, temperature,
                dynamic_context=dynamic_context,  
            )

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
        dynamic_context: str = "",  
        use_cache=True,
    ) -> tuple[str, list]:

        client = self._get_anthropic_client()
        clean_messages = _sanitize_messages_anthropic(messages)

        # ── Construction des blocs système 
        # Bloc 1 — Statique (base + state) → mis en cache
        # Éligible au cache si >= 1 024 tokens
        # TTL : 5 minutes côté Anthropic 

        system_blocks = []

        # Bloc 1 — Statique (base + state) → mis en cache
        if system_prompt and system_prompt.strip():
            static_block = {
                "type": "text",
                "text": system_prompt.strip(),
            }
            if use_cache:
                static_block["cache_control"] = {"type": "ephemeral"}
            system_blocks.append(static_block)

        # Bloc 2 — Dynamique (contexte client + RAG)
        if dynamic_context and dynamic_context.strip():
            system_blocks.append({
                "type": "text",
                "text": dynamic_context.strip(),
            })

        if not system_blocks:
            system_blocks = [{"type": "text", "text": "Tu es Yanick, assistant de Digitech Hub."}]

        for attempt in range(3):
            try:
                response = client.messages.create(
                    model=Config.LLM_MODEL,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=system_blocks,
                    messages=clean_messages,
                )
                text = response.content[0].text if response.content else ""

                # ── Log tokens avec détail cache 
                usage         = response.usage
                cache_read    = getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_created = getattr(usage, "cache_creation_input_tokens", 0) or 0

                logger.info(
                    "LLM — input=%d output=%d | cache_read=%d",
                    usage.input_tokens, usage.output_tokens, cache_read,
                )

                return text.strip(), []

            except Exception as e:
                err = str(e).lower()
                if "rate_limit" in err or "overloaded" in err or "529" in err:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "Anthropic rate limit or surchargé (529) — attente %ds (tentative %d/3)",
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
        dynamic_context: str = "",  # ← AJOUTER
    ) -> tuple[str, list]:

        client = self._get_openai_client()

        # OpenAI → pas de cache natif → fusionner statique + dynamique
        full_system = system_prompt
        if dynamic_context:
            full_system = system_prompt + "\n" + dynamic_context

        openai_messages = [{"role": "system", "content": full_system}]
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
 
    Gère les contenus texte (str) ET multimodaux (list) pour Claude vision.
    """
    if not messages:
        return []
 
    clean = []
    for msg in messages:
        role    = msg.get("role")
        content = msg.get("content")
 
        if role not in ("user", "assistant"):
            continue
 
        # ── Contenu multimodal (list) — image + texte ──────────────────────
        # Ne jamais fusionner les messages multimodaux
        if isinstance(content, list):
            if content:   # ignorer les listes vides
                clean.append({"role": role, "content": content})
            continue
 
        # ── Contenu texte (str) ────────────────────────────────────────────
        content_str = (content or "").strip()
        if not content_str:
            continue
 
        # Fusionner les messages TEXTE consécutifs du même rôle uniquement
        if (
            clean
            and clean[-1]["role"] == role
            and isinstance(clean[-1]["content"], str)
        ):
            clean[-1]["content"] += "\n" + content_str
        else:
            clean.append({"role": role, "content": content_str})
 
    # S'assurer que le premier message est role="user"
    if clean and clean[0]["role"] != "user":
        clean.pop(0)
 
    # S'assurer que le dernier message est role="user"
    if clean and clean[-1]["role"] != "user":
        clean = clean[:-1]
 
    return clean