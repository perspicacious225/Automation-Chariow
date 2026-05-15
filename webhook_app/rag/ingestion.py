"""
rag/ingestion.py — Pipeline d'ingestion des documents produit
=============================================================
Lit un fichier .md de knowledge_base/products/,
le découpe en chunks thématiques,
génère les embeddings via embedder.py,
et insère tout dans knowledge_chunks via database_conv.py.

Usage CLI :
    python -m webhook_app.rag.ingestion --product prd_k3eyyy
    python -m webhook_app.rag.ingestion --all
"""

import os
import re
import logging
import argparse
from pathlib import Path
from typing import Optional

from webhook_app.rag.embedder import embed_batch
from webhook_app.database_conv import insert_chunk, delete_chunks_for_product
from webhook_app.database_v21 import save_kb_source, get_all_kb_sources

logger = logging.getLogger(__name__)

# Chemin vers les documents produit (depuis la racine du projet)
KB_DIR = Path(__file__).resolve().parents[1] / "knowledge_base" / "products"

# Taille max d'un chunk en caractères (~500 tokens ≈ 2000 caractères)
CHUNK_MAX_CHARS = 2000

# Sections reconnues dans le format standard Digitech Hub
KNOWN_SECTIONS = {
    "présentation générale": "presentation",
    "promesse": "promesse",
    "contenu détaillé": "contenu",
    "accès après achat": "acces",
    "problèmes courants": "support",
    "faq": "faq",
    "objections courantes": "objections",
    "limites": "escalade",
    "informations commerciales": "commercial",
}


# ══════════════════════════════════════════════════════════════════════════════
# DÉCOUPAGE
# ══════════════════════════════════════════════════════════════════════════════

def _detect_section(heading: str) -> Optional[str]:
    """Mappe un titre de section markdown vers un label normalisé."""
    h = heading.lower().strip()
    for key, label in KNOWN_SECTIONS.items():
        if key in h:
            return label
    return None


def split_into_chunks(text: str, source: str) -> list[dict]:
    """
    Découpe un document markdown en chunks thématiques.

    Stratégie :
    - Chaque titre de niveau 2 (##) démarre un nouveau chunk.
    - Si un bloc dépasse CHUNK_MAX_CHARS, il est subdivisé par paragraphe.
    - Chaque chunk conserve son label de section.

    Retourne une liste de dicts :
        { chunk_text, section, chunk_index, source }
    """
    chunks = []
    current_section = None
    current_lines: list[str] = []

    def _flush(lines: list[str], section: str, index: int) -> Optional[dict]:
        content = "\n".join(lines).strip()
        if not content:
            return None
        return {
            "chunk_text": content,
            "section": section,
            "chunk_index": index,
            "source": source,
        }

    def _subdivide(text_block: str, section: str, start_index: int) -> list[dict]:
        """Subdivise un bloc trop long en sous-chunks par paragraphe."""
        sub_chunks = []
        paragraphs = re.split(r"\n{2,}", text_block)
        buffer = ""
        idx = start_index
        for para in paragraphs:
            if len(buffer) + len(para) > CHUNK_MAX_CHARS and buffer:
                sub_chunks.append({
                    "chunk_text": buffer.strip(),
                    "section": section,
                    "chunk_index": idx,
                    "source": source,
                })
                idx += 1
                buffer = para
            else:
                buffer = (buffer + "\n\n" + para).strip() if buffer else para
        if buffer.strip():
            sub_chunks.append({
                "chunk_text": buffer.strip(),
                "section": section,
                "chunk_index": idx,
                "source": source,
            })
        return sub_chunks

    chunk_index = 0
    lines = text.split("\n")

    for line in lines:
        # Nouveau titre H2 → flush le bloc précédent et démarre un nouveau
        if line.startswith("## "):
            if current_lines:
                chunk = _flush(current_lines, current_section or "general", chunk_index)
                if chunk:
                    if len(chunk["chunk_text"]) > CHUNK_MAX_CHARS:
                        sub = _subdivide(chunk["chunk_text"], chunk["section"], chunk_index)
                        chunks.extend(sub)
                        chunk_index += len(sub)
                    else:
                        chunks.append(chunk)
                        chunk_index += 1
                current_lines = []

            heading_text = line.lstrip("#").strip()
            current_section = _detect_section(heading_text)
            # On inclut le titre dans le chunk pour que le LLM ait le contexte
            current_lines = [line]

        # Ignorer les métadonnées d'en-tête (# PRODUIT :, # ID :, etc.)
        elif line.startswith("# "):
            continue

        else:
            current_lines.append(line)

    # Flush du dernier bloc
    if current_lines:
        chunk = _flush(current_lines, current_section or "general", chunk_index)
        if chunk:
            if len(chunk["chunk_text"]) > CHUNK_MAX_CHARS:
                sub = _subdivide(chunk["chunk_text"], chunk["section"], chunk_index)
                chunks.extend(sub)
            else:
                chunks.append(chunk)

    return chunks


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE D'INGESTION
# ══════════════════════════════════════════════════════════════════════════════

def ingest_product(product_id: str, *, force: bool = False, text_override: str | None = None) -> dict:
    """
    Ingère le document d'un produit dans la knowledge base.
    text_override : texte déjà extrait (depuis upload dashboard) — bypass lecture fichier.
    """
    # ── Source du texte ───────────────────────────────────────────
    if text_override:
        # Texte fourni directement (upload dashboard)
        text = text_override
        source = f"{product_id}_upload"
    else:
        # Lecture depuis le filesystem
        md_path = KB_DIR / f"{product_id}.md"
        if not md_path.exists():
            logger.error("Fichier introuvable : %s", md_path)
            return {"product_id": product_id, "chunks_created": 0, "status": "file_not_found"}
        text = md_path.read_text(encoding="utf-8")
        source = md_path.name

    logger.info("Ingestion de %s...", product_id)

    # ── Sauvegarder la source en DB ───────────────────────────────
    try:
        save_kb_source(
            product_id=product_id,
            filename=source,
            content=text,
        )
        logger.info("Source KB sauvegardée en DB : %s / %s", product_id, source)
    except Exception as e:
        logger.warning("Sauvegarde KB source échouée (non bloquant) : %s", e)

    # Découpage
    chunks = split_into_chunks(text, source)
    if not chunks:
        logger.warning("Aucun chunk extrait pour %s", product_id)
        return {"product_id": product_id, "chunks_created": 0, "status": "no_chunks"}

    logger.info("%d chunks extraits pour %s", len(chunks), product_id)

    # Génération des embeddings en batch
    texts = [c["chunk_text"] for c in chunks]
    embeddings = embed_batch(texts)

    if len(embeddings) != len(chunks):
        logger.error("Mismatch embeddings/chunks : %d vs %d", len(embeddings), len(chunks))
        return {"product_id": product_id, "chunks_created": 0, "status": "embedding_mismatch"}

    # Suppression des anciens chunks
    deleted = delete_chunks_for_product(product_id)
    if deleted:
        logger.info("%d anciens chunks supprimés pour %s", deleted, product_id)

    # Insertion des nouveaux chunks
    created = 0
    for chunk, embedding in zip(chunks, embeddings):
        insert_chunk(
            product_id=product_id,
            source=chunk["source"],
            chunk_text=chunk["chunk_text"],
            embedding=embedding,
            section=chunk["section"],
            chunk_index=chunk["chunk_index"],
        )
        created += 1

    logger.info("Ingestion terminée : %d chunks créés pour %s", created, product_id)
    return {"product_id": product_id, "chunks_created": created, "status": "ok"}

def ingest_all(*, force: bool = False) -> list[dict]:
    """
    Ingère tous les fichiers .md présents dans knowledge_base/products/.
    Retourne la liste des résultats par produit.
    """
    if not KB_DIR.exists():
        logger.error("Dossier KB introuvable : %s", KB_DIR)
        return []

    md_files = list(KB_DIR.glob("*.md"))
    if not md_files:
        logger.warning("Aucun fichier .md trouvé dans %s", KB_DIR)
        return []

    logger.info("%d fichiers à ingérer...", len(md_files))
    results = []
    for md_file in md_files:
        product_id = md_file.stem  # nom du fichier sans extension
        result = ingest_product(product_id, force=force)
        results.append(result)

    total = sum(r["chunks_created"] for r in results)
    logger.info("Ingestion complète : %d chunks créés sur %d produits.", total, len(results))
    return results

def ingest_all_from_db(*, force: bool = False) -> list[dict]:
    """
    Réingère tous les produits depuis les sources stockées en DB.
    Utilisé après un redémarrage Render pour reconstruire les chunks
    sans accès au filesystem.
    """
    sources = get_all_kb_sources()
    if not sources:
        logger.warning("Aucune source KB en DB — fallback sur filesystem")
        return ingest_all(force=force)

    logger.info("%d sources KB trouvées en DB", len(sources))

    # Grouper par product_id et concaténer les fichiers
    from collections import defaultdict
    by_product: dict[str, list[str]] = defaultdict(list)
    for src in sources:
        by_product[src["product_id"]].append(src["content"])

    results = []
    for product_id, contents in by_product.items():
        combined_text = "\n\n".join(contents)
        result = ingest_product(
            product_id,
            force=force,
            text_override=combined_text,
        )
        results.append(result)

    total = sum(r["chunks_created"] for r in results)
    logger.info("Réingestion DB complète : %d chunks sur %d produits", total, len(results))
    return results

# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")

    parser = argparse.ArgumentParser(description="Pipeline d'ingestion KB CHARIOW v2")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--product", type=str, help="ID du produit à ingérer (ex: prd_k3eyyy)")
    group.add_argument("--all", action="store_true", help="Ingérer tous les produits")
    parser.add_argument("--force", action="store_true", help="Forcer la réingestion même si chunks existent")

    args = parser.parse_args()

    if args.all:
        results = ingest_all(force=args.force)
        for r in results:
            print(f"  {r['product_id']:30s} → {r['chunks_created']:3d} chunks  [{r['status']}]")
    else:
        result = ingest_product(args.product, force=args.force)
        print(f"  {result['product_id']} → {result['chunks_created']} chunks [{result['status']}]")