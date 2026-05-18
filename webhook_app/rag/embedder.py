"""
rag/embedder.py — Génération d'embeddings via Voyage AI
=========================================================
Convertit du texte en vecteur float[1024] via voyage-4-lite.
Utilisé par le pipeline d'ingestion ET par le moteur de recherche RAG.

"""

import logging
import time
from typing import Optional

import voyageai

from webhook_app.config import Config

logger = logging.getLogger(__name__)

# Client Voyage AI — instancié une seule fois
_client: Optional[voyageai.Client] = None

# Dimension des vecteurs voyage-4-lite
EMBEDDING_DIM = 1024


def _get_client() -> voyageai.Client:
    global _client
    if _client is None:
        _client = voyageai.Client(api_key=Config.EMBEDDING_API_KEY)
    return _client


def embed_text(text: str, *, retries: int = 3, base_delay: float = 1.0) -> list[float]:
    """
    Génère l'embedding d'un texte (question client).
    Retourne un vecteur float[1024].
    input_type="query" — optimisé pour les requêtes de recherche.
    """
    text = text.strip()
    if not text:
        raise ValueError("embed_text : le texte ne peut pas être vide.")

    client = _get_client()

    for attempt in range(retries):
        try:
            result = client.embed(
                [text],
                model=Config.EMBEDDING_MODEL,
                input_type="query",
            )
            return result.embeddings[0]

        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "limit" in err or "429" in err:
                wait = base_delay * (2 ** attempt)
                logger.warning(
                    "Rate limit Voyage AI — attente %.1fs (tentative %d/%d)",
                    wait, attempt + 1, retries
                )
                time.sleep(wait)
                continue
            logger.error("Erreur Voyage AI embed_text : %s", e)
            raise

    raise RuntimeError(f"embed_text : échec après {retries} tentatives.")


def embed_documents(texts: list[str], *, batch_size: int = 128) -> list[list[float]]:
    """
    Génère les embeddings d'une liste de documents (chunks KB).
    input_type="document" — optimisé pour l'ingestion.
    Retourne une liste de vecteurs dans le même ordre que l'input.
    """
    if not texts:
        return []

    client = _get_client()
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = [t.strip() for t in texts[i:i + batch_size] if t.strip()]
        if not batch:
            continue

        for attempt in range(3):
            try:
                result = client.embed(
                    batch,
                    model=Config.EMBEDDING_MODEL,
                    input_type="document",
                )
                all_embeddings.extend(result.embeddings)
                logger.debug(
                    "Batch embeddings : %d/%d documents traités.",
                    min(i + batch_size, len(texts)), len(texts)
                )
                break

            except Exception as e:
                err = str(e).lower()
                if ("rate" in err or "limit" in err or "429" in err) and attempt < 2:
                    wait = 2 ** attempt * 2
                    logger.warning("Rate limit batch — attente %ds", wait)
                    time.sleep(wait)
                    continue
                logger.error("Erreur Voyage AI embed_documents batch %d : %s", i, e)
                raise

    return all_embeddings


# Alias pour compatibilité avec ingestion.py qui appelle embed_batch
embed_batch = embed_documents