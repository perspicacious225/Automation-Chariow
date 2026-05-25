"""
rag/retriever.py — Recherche vectorielle RAG
=============================================
Point d'entrée unique pour interroger la knowledge base.
Prend une question en texte, génère son embedding,
recherche les chunks les plus pertinents dans Supabase/pgvector,
et retourne un contexte prêt à injecter dans le prompt LLM.

Fix 1 — Filtrage RAG par section selon l'état conversationnel.
Les sections techniques (installation, support, acces) sont physiquement
exclues du contexte LLM tant que l'achat n'est pas validé.
"""

import logging
from typing import Optional

from webhook_app.rag.embedder import embed_text
from webhook_app.database_conv import search_chunks
from webhook_app.config import Config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# SECTIONS AUTORISÉES PAR ÉTAT (Fix 1 — Isolation physique KB)
# ══════════════════════════════════════════════════════════
#
# None  = toutes les sections autorisées (pas de filtre)
# set() = aucune section autorisée (RAG désactivé — ex: escalation)
#
# Logique :
#   - États vendeurs  → sections commerciales uniquement
#   - États post-achat → toutes les sections (support inclus)
#   - Escalation       → rien (l'IA ne doit pas parler)

SECTION_FILTER_BY_STATE: dict[str, set[str] | None] = {
    # ── Zone vendeur — INTERDIT technique 
    "new_prospect":      {"commercial", "presentation", "promesse", "faq", "objections"},
    "interested_lead":   {"commercial", "presentation", "promesse", "faq", "objections"},
    "pre_sale":          {"commercial", "presentation", "promesse", "faq", "objections"},
    "payment_failed":    {"commercial", "faq", "objections"},
    "payment_abandoned": {"commercial", "presentation", "promesse", "faq", "objections"},

    # ── Zone post-achat — tout autorisé 
    "payment_success":   None,
    "post_sale":         None,
    "support":           None,

    # ── Escalation — RAG désactivé 
    "escalation":        set(),
}

# Fallback si l'état est inconnu ou absent
DEFAULT_SECTION_FILTER: set[str] = {
    "commercial", "presentation", "promesse", "faq", "objections"
}


# ═════════════════
# RECHERCHE
# ═════════════════

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
        top_k      : nombre de chunks à retourner 
        min_score  : seuil de similarité cosine minimum 

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

    return results


# ══════════════════════════════════════════
# FORMATAGE DU CONTEXTE POUR LE PROMPT LLM
# ══════════════════════════════════════════

def build_rag_context(
    query: str,
    *,
    product_id: Optional[str] = None,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
    allowed_sections: Optional[set[str]] = None,  # Fix 1 — filtre par section
    state: Optional[str] = None,                  # Fix 1 — résolution auto du filtre
) -> tuple[str, list[str]]:
    """
    Construit le contexte RAG à injecter dans le prompt LLM.

    Paramètres Fix 1 :
        allowed_sections : set de sections autorisées.
                           Si None ET state fourni → résolu via SECTION_FILTER_BY_STATE.
                           Si None ET pas de state → DEFAULT_SECTION_FILTER appliqué.
        state            : état conversationnel courant — utilisé pour résoudre
                           allowed_sections automatiquement si non fourni.

    Retourne (contexte_texte, liste_chunk_ids).
    """

    # ── Résolution du filtre de sections ──────────────────────────────────
    # Priorité : allowed_sections explicite > résolution par state > default
    if allowed_sections is None:
        if state is not None:
            # Lookup dans la table — None signifie "tout autorisé"
            allowed_sections = SECTION_FILTER_BY_STATE.get(state, DEFAULT_SECTION_FILTER)
        else:
            allowed_sections = DEFAULT_SECTION_FILTER


    # ── 1. Recherche vectorielle classique 
    chunks = retrieve(
        query,
        product_id=product_id,
        top_k=top_k,
        min_score=min_score,
    )

    # ── 2. Filtrage par section autorisée (Fix 1) 
    # None = tout passe. set() vide = tout bloqué.
    if allowed_sections is not None:
        before = len(chunks)
        chunks = [
            c for c in chunks
            if (c.get("section") or "général") in allowed_sections
        ]
        filtered = before - len(chunks)
        if filtered > 0:
            logger.info(
                "RAG — %d chunk(s) filtrés (sections non autorisées pour state=%s)",
                filtered, state,
            )

    #  3. Injection forcée du chunk commercial 
    # Uniquement si la section "commercial" est autorisée
    if product_id and (allowed_sections is None or "commercial" in allowed_sections):
        from webhook_app.database_conv import get_chunks_by_section
        commercial_chunks = get_chunks_by_section(product_id, "commercial")

        if commercial_chunks:
            chunk_ids_existing = [c["id"] for c in chunks]
            if commercial_chunks[0]["id"] not in chunk_ids_existing:
                commercial_chunks[0]["score"] = 1.0
                chunks.insert(0, commercial_chunks[0])
                # logger.debug("RAG — chunk commercial forcé en tête de contexte")

    if not chunks:
        logger.debug("RAG — aucun chunk retourné après filtrage (state=%s)", state)
        return "", []

    chunk_ids = [str(c["id"]) for c in chunks]

    # ── 4. Construction du bloc de contexte 
    lines = ["[CONTEXTE PRODUIT PERTINENT]"]
    for i, chunk in enumerate(chunks, 1):
        section_label = chunk.get("section") or "général"
        score = chunk.get("score", 0.0)
        lines.append(
            f"\n--- Source {i} (section: {section_label}, pertinence: {score:.2f}) ---"
        )
        lines.append(chunk["chunk_text"])

    lines.append("\n[FIN DU CONTEXTE PRODUIT]")

    return "\n".join(lines), chunk_ids


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRE — TEST RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

def test_retrieval(
    query: str,
    product_id: Optional[str] = None,
    state: Optional[str] = None,
):
    """
    Fonction de test — affiche les chunks retournés pour une query.

    Usage :
        python -c "
        from webhook_app.rag.retriever import test_retrieval
        test_retrieval('la clé ne marche pas', 'prd_k3eyyy', state='pre_sale')
        "
    """
    print(f"\n🔍 Query  : {query}")
    print(f"   Produit : {product_id or 'tous'}")
    print(f"   State   : {state or 'non fourni'}")

    if state:
        allowed = SECTION_FILTER_BY_STATE.get(state, DEFAULT_SECTION_FILTER)
        print(f"   Sections autorisées : {allowed if allowed is not None else 'TOUTES'}\n")
    else:
        print()

    context, ids = build_rag_context(query, product_id=product_id, state=state)

    if not context:
        print("   Aucun chunk retourné après filtrage.")
        return

    print(context)
    print(f"\n   Chunk IDs : {ids}")



