"""
rag/retriever.py — Recherche vectorielle RAG
=============================================
Point d'entrée unique pour interroger la knowledge base.
Prend une question en texte, génère son embedding,
recherche les chunks les plus pertinents dans Supabase/pgvector,
et retourne un contexte prêt à injecter dans le prompt LLM.
"""

import logging
from typing import Optional

from webhook_app.rag.embedder import embed_text
from webhook_app.database_conv import search_chunks
from webhook_app.config import Config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# RECHERCHE
# ══════════════════════════════════════════════════════════════════════════════

def retrieve(
    query: str,
    *,
    product_id: Optional[str] = None,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> list[dict]:
    """
    Recherche les chunks les plus pertinents pour une question.

    Args:
        query      : message du client / question à traiter
        product_id : si connu, filtre la recherche sur ce produit uniquement
        top_k      : nombre de chunks à retourner (défaut : Config.RAG_TOP_K)
        min_score  : seuil de similarité cosine minimum (défaut : Config.RAG_MIN_SCORE)

    Retourne une liste de dicts :
        { id, product_id, section, chunk_text, score, metadata }
    """
    top_k = top_k or Config.RAG_TOP_K
    min_score = min_score or Config.RAG_MIN_SCORE

    if not query or not query.strip():
        logger.warning("retrieve() appelé avec une query vide.")
        return []

    # Génération de l'embedding de la question
    query_embedding = embed_text(query.strip())

    # Recherche vectorielle dans Supabase/pgvector
    results = search_chunks(
        query_embedding,
        product_id=product_id,
        top_k=top_k,
        min_score=min_score,
    )

    logger.debug(
        "retrieve(%s, product=%s) → %d chunks (top score: %.3f)",
        query[:60],
        product_id or "all",
        len(results),
        results[0]["score"] if results else 0.0,
    )

    return results


# ══════════════════════════════════════════════════════════════════════════════
# FORMATAGE DU CONTEXTE POUR LE PROMPT LLM
# ══════════════════════════════════════════════════════════════════════════════

def build_rag_context(
    query: str,
    *,
    product_id: Optional[str] = None,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> tuple[str, list[str]]:
    """
    Récupère les chunks pertinents et les formate en bloc texte
    prêt à être injecté dans le prompt système du LLM.

    Retourne :
        context_block : str  — texte formaté à injecter dans le prompt
        chunk_ids     : list — IDs des chunks utilisés (pour logging/debug)
    """
    chunks = retrieve(
        query,
        product_id=product_id,
        top_k=top_k,
        min_score=min_score,
    )

    if not chunks:
        return "", []

    chunk_ids = [str(c["id"]) for c in chunks]

    # Construction du bloc de contexte
    lines = ["[CONTEXTE PRODUIT PERTINENT]", ""]
    for i, chunk in enumerate(chunks, 1):
        section_label = chunk.get("section") or "général"
        score = chunk.get("score", 0.0)
        lines.append(f"--- Source {i} (section: {section_label}, pertinence: {score:.2f}) ---")
        lines.append(chunk["chunk_text"])
        lines.append("")

    lines.append("[FIN DU CONTEXTE PRODUIT]")

    context_block = "\n".join(lines)

    return context_block, chunk_ids


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRE — TEST RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

def test_retrieval(query: str, product_id: Optional[str] = None):
    """
    Fonction de test — affiche les chunks retournés pour une query.
    Usage : python -c "from webhook_app.rag.retriever import test_retrieval;
                        test_retrieval('comment accéder à ma formation', 'prd_k3eyyy')"
    """
    print(f"\n🔍 Query : {query}")
    print(f"   Produit : {product_id or 'tous'}\n")

    chunks = retrieve(query, product_id=product_id)

    if not chunks:
        print("   Aucun chunk trouvé au-dessus du seuil de pertinence.")
        return

    for i, chunk in enumerate(chunks, 1):
        print(f"   [{i}] score={chunk['score']:.3f} | section={chunk.get('section')} | product={chunk['product_id']}")
        preview = chunk["chunk_text"][:150].replace("\n", " ")
        print(f"       {preview}...")
        print()