"""
======================================================================
Gère le téléchargement, l'encodage et l'extraction de contenu
pour les images et documents reçus via Green API.

Types supportés :
  imageMessage       → téléchargement + encodage base64 pour Claude vision
  documentMessage PDF → téléchargement + extraction texte 
  documentMessage autre → contexte texte générique pour le LLM

Barrières :
  Barrière 1 — Taille via HEAD request (max 10MB)
  Barrière 2 — Pages via pypdf 
"""

import io
import base64
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ── Limites ────────────────────────────────────────────────────────────────
MAX_SIZE_MB      = 10     
MAX_PDF_PAGES    = 5     
MAX_TEXT_CHARS   = 3000   
DOWNLOAD_TIMEOUT = 15     

# MIME types image supportés 
IMAGE_MIME_TYPES = {
    "image/jpeg", "image/jpg",
    "image/png", "image/webp",
}


# ══════════════════════════════════════
# INTERNALS
# ══════════════════════════════════════

def _check_size(url: str, max_mb: int = MAX_SIZE_MB) -> tuple[bool, int]:
    """
    Barrière 1 — Vérifie la taille du fichier via HEAD request.
    Retourne (autorisé, taille_en_bytes).
    Si HEAD échoue → autorise par défaut (le GET limitera).
    """
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        content_length = int(resp.headers.get("Content-Length", 0))
        if content_length == 0:
            # Content-Length absent → on laisse passer, GET sera limité
            return True, 0
        max_bytes = max_mb * 1024 * 1024
        return content_length <= max_bytes, content_length
    except Exception as e:
        logger.warning("HEAD request échouée pour vérif taille : %s", e)
        return True, 0  # fail open — le GET prendra le relais


def _download(url: str) -> Optional[bytes]:
    """Télécharge le fichier depuis l'URL Green API."""
    try:
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error("Téléchargement média échoué (%s) : %s", url[:80], e)
        return None


def _encode_image(data: bytes, mime_type: str) -> str:
    """Encode l'image en base64 pour Claude vision."""
    return base64.standard_b64encode(data).decode("utf-8")


def _extract_pdf_text(data: bytes) -> tuple[str, int, str | None]:
    """
    Barrière 2 — Extrait le texte d'un PDF.
    Retourne (texte_extrait, nb_pages, raison_blocage_ou_None).
    pypdf lit le header pour le compte de pages — pas besoin de parser tout le fichier.
    """
    try:
        import pypdf
    except ImportError:
        logger.error("pypdf non installé")
        return "", 0, "module_manquant"

    try:
        reader     = pypdf.PdfReader(io.BytesIO(data))
        total_pages = len(reader.pages)

        if total_pages > MAX_PDF_PAGES:
            return "", total_pages, "too_long"

        text_parts = []
        for i in range(total_pages):
            page_text = reader.pages[i].extract_text() or ""
            if page_text.strip():
                text_parts.append(page_text.strip())

        full_text = "\n\n".join(text_parts).strip()
        return full_text, total_pages, None

    except Exception as e:
        logger.error("Extraction texte PDF échouée : %s", e)
        return "", 0, "extraction_error"


# ═════════════════════════════════
# POINT D'ENTRÉE PRINCIPAL
# ═════════════════════════════════

def process_media(media: dict) -> dict:
    """
    Traite un média reçu via WhatsApp.

    Entrée (depuis _extract_message) :
        {
            "type"     : "image" | "document",
            "url"      : str,
            "caption"  : str,
            "filename" : str,
            "mime_type": str,
        }

    Sortie possible :
        {"status": "image_ok",     "data": base64, "mime_type": str, ...}
        {"status": "pdf_ok",       "text": str,    "pages": int,   ...}
        {"status": "pdf_too_long", "pages": int,   "reason": str,  ...}
        {"status": "too_large",    "reason": str,                  ...}
        {"status": "unsupported",  "mime_type": str,"reason": str, ...}
        {"status": "error",        "reason": str,                  ...}
    """
    url       = media.get("url", "")
    caption   = (media.get("caption") or "").strip()
    filename  = media.get("filename", "fichier")
    mime_type = (media.get("mime_type") or "").lower()

    if not url:
        return {"status": "error", "filename": filename, "caption": caption, "reason": "url manquante"}

    # ── Barrière 1 — Taille 
    allowed, size_bytes = _check_size(url)
    if not allowed:
        size_mb = size_bytes / (1024 * 1024)
        logger.warning(
            "Média rejeté (taille) : %s — %.1fMB > %dMB max",
            filename, size_mb, MAX_SIZE_MB,
        )
        return {
            "status":   "too_large",
            "filename": filename,
            "caption":  caption,
            "reason":   f"fichier trop volumineux ({size_mb:.1f}MB)",
        }

    # ── Téléchargement 
    data = _download(url)
    if not data:
        return {"status": "error", "filename": filename, "caption": caption, "reason": "téléchargement échoué"}

    logger.info(
        "Média téléchargé : %s | type=%s | taille=%dKB",
        filename, mime_type, len(data) // 1024,
    )

    # ── IMAGE ─────────
    is_image = (
        media.get("type") == "image"
        or mime_type in IMAGE_MIME_TYPES
        or any(filename.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))
    )
    if is_image:
        if mime_type not in IMAGE_MIME_TYPES:
            mime_type = "image/jpeg"   # fallback sécurisé
        logger.info("Traitement image : %s (%s)", filename, mime_type)
        return {
            "status":    "image_ok",
            "data":      _encode_image(data, mime_type),
            "mime_type": mime_type,
            "caption":   caption,
            "filename":  filename,
        }

    # ── DOCUMENT PDF ──
    is_pdf = mime_type == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        text, pages, reason = _extract_pdf_text(data)

        if reason == "too_long":
            logger.warning("PDF rejeté (pages) : %s — %d pages > %d max", filename, pages, MAX_PDF_PAGES)
            return {
                "status":   "pdf_too_long",
                "filename": filename,
                "caption":  caption,
                "pages":    pages,
                "reason":   f"document trop long ({pages} pages, max {MAX_PDF_PAGES})",
            }
        if reason:
            return {"status": "error", "filename": filename, "caption": caption, "reason": reason}

        logger.info("PDF extrait : %s — %d page(s) | %d chars", filename, pages, len(text))
        return {
            "status":   "pdf_ok",
            "text":     text[:MAX_TEXT_CHARS],
            "pages":    pages,
            "caption":  caption,
            "filename": filename,
        }

    # ── DOCUMENT NON SUPPORTÉ ─────────────────────────────────────────────
    logger.info("Type document non supporté : %s (%s)", filename, mime_type)
    return {
        "status":    "unsupported",
        "filename":  filename,
        "mime_type": mime_type,
        "caption":   caption,
        "reason":    f"format non supporté ({mime_type or 'inconnu'})",
    }


# ══════════════════════════════════════
# INJECTION CONTEXTE LLM (cas non-vision)
# ══════════════════════════════════════

def build_media_context_note(processed: dict) -> str:
    """
    Construit la note de contexte à injecter dans le prompt LLM
    pour les cas non-vision (PDF, bloqués, non supportés).
    Le LLM génère une réponse adaptée à l'état de la conversation.
    """
    status   = processed.get("status", "")
    filename = processed.get("filename", "fichier")
    caption  = processed.get("caption", "")
    caption_part = f'\nLégende du client : "{caption}"' if caption else ""

    if status == "pdf_ok":
        text  = processed.get("text", "")
        pages = processed.get("pages", "?")
        return (
            f"\n[DOCUMENT REÇU : {filename} — {pages} page(s)]{caption_part}\n"
            f"Contenu extrait :\n{text}\n"
            f"[FIN DOCUMENT]\n"
            f"Analyse ce document dans le contexte de la conversation et réponds de façon appropriée."
        )

    if status == "pdf_too_long":
        pages = processed.get("pages", "?")
        return (
            f"\n[DOCUMENT REÇU : {filename} — {pages} pages — trop long pour traitement (max {MAX_PDF_PAGES} pages)]{caption_part}\n"
            f"Informe le client que ce document est trop volumineux et demande-lui de reformuler "
            f"sa demande en texte ou d'envoyer un document plus court."
        )

    if status == "too_large":
        return (
            f"\n[DOCUMENT REÇU : {filename} — fichier trop volumineux (max {MAX_SIZE_MB}MB)]{caption_part}\n"
            f"Informe le client que ce fichier est trop volumineux et demande-lui "
            f"d'envoyer un fichier plus léger ou de reformuler en texte."
        )

    if status == "unsupported":
        mime = processed.get("mime_type", "inconnu")
        return (
            f"\n[DOCUMENT REÇU : {filename} — format {mime} non supporté]{caption_part}\n"
            f"Informe le client que ce type de fichier n'est pas supporté et "
            f"demande-lui de reformuler sa demande en texte ou d'envoyer un PDF ou une image."
        )

    if status == "error":
        reason = processed.get("reason", "erreur inconnue")
        return (
            f"\n[DOCUMENT REÇU : {filename} — erreur de traitement : {reason}]{caption_part}\n"
            f"Informe le client qu'il y a eu un problème avec son fichier et "
            f"demande-lui de réessayer ou de reformuler en texte."
        )

    return ""