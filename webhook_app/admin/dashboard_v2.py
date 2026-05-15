"""
admin/dashboard_v2.py — Dashboard CHARIOW v2 Conversationnel
=============================================================
Blueprint Flask pour l'interface d'administration du système
conversationnel IA WhatsApp.

Routes :
  GET  /dashboard/v2/                        — liste conversations
  GET  /dashboard/v2/kb/                     — knowledge base
  GET  /dashboard/v2/prompts/                — prompts système

API JSON (appelées par le frontend) :
  GET  /dashboard/v2/api/conversations/      — liste JSON
  GET  /dashboard/v2/api/conversations/<id>/ — détail + historique
  POST /dashboard/v2/api/conversations/<id>/toggle-ai/
  POST /dashboard/v2/api/conversations/<id>/state/
  POST /dashboard/v2/api/conversations/<id>/reply/    — réponse admin
  GET  /dashboard/v2/api/kb/stats/           — stats chunks
  POST /dashboard/v2/api/kb/ingest/          — ingérer un produit
  POST /dashboard/v2/api/kb/ingest-all/      — ingérer tous
  POST /dashboard/v2/api/kb/upload/          — upload + ingestion
  GET  /dashboard/v2/api/prompts/            — liste prompts
  PUT  /dashboard/v2/api/prompts/<key>/      — mettre à jour prompt
"""

import logging
import os
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from webhook_app.database_conv import (
    list_conversations_with_last_message,  
    get_conversation_by_id,
    toggle_ai,
    update_conversation_state,
    fetch_history,                          
    count_chunks_by_product,
    CONV_STATES,
)
from webhook_app.database_pg import get_connection, execute_with_retry


logger = logging.getLogger(__name__)

dashboard_v2_bp = Blueprint(
    "dashboard_v2",
    __name__,
    url_prefix="/dashboard/v2",
)

# ── Chemin KB ────────────────────────────────────────────────────────────────
KB_DIR = os.path.join(
    os.path.dirname(__file__),   # webhook_app/admin/
    "..", "..",                  # racine projet
    "knowledge_base", "products"
)
KB_DIR = os.path.normpath(KB_DIR)


# ══════════════════════════════════════════════════════════════════════════════
# PAGES HTML
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/")
@login_required
def index():
    """Page principale — liste des conversations."""
    return render_template("dashboard_v2/index.html")


@dashboard_v2_bp.get("/kb/")
@login_required
def kb():
    """Page Knowledge Base."""
    return render_template("dashboard_v2/kb.html")


@dashboard_v2_bp.get("/prompts/")
@login_required
def prompts():
    """Page Prompts système."""
    return render_template("dashboard_v2/prompts.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — CONVERSATIONS
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/conversations/")
@login_required
def api_list_conversations():
    state = request.args.get("state") or None
    ai_active_param = request.args.get("ai_active")
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))

    ai_active = None
    if ai_active_param is not None:
        ai_active = ai_active_param.lower() not in ("false", "0", "no")

    # Une seule requête SQL — 
    conversations = list_conversations_with_last_message(
        state=state,
        ai_active=ai_active,
        limit=limit,
        offset=offset,
    )

    for conv in conversations:
        _serialize_datetimes(conv)

    stats = _get_conversation_stats()

    return jsonify({
        "conversations": conversations,
        "count": len(conversations),
        "stats": stats,
        "filters": {
            "state": state,
            "ai_active": ai_active,
            "limit": limit,
            "offset": offset,
        },
    })


@dashboard_v2_bp.get("/api/conversations/<conv_id>/")
@login_required
def api_get_conversation(conv_id: str):
    """Retourne le détail d'une conversation avec son historique complet."""
    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    history = fetch_history(conv_id, limit=50)

    _serialize_datetimes(conversation)
    for msg in history:
        _serialize_datetimes(msg)

    # Enrichir avec les données de vente si disponible
    sale_data = None
    if conversation.get("last_sale_id"):
        sale_data = _fetch_sale_data(conversation["last_sale_id"])

    return jsonify({
        "conversation": conversation,
        "history": history,
        "message_count": len(history),
        "sale": sale_data,
    })


@dashboard_v2_bp.post("/api/conversations/<conv_id>/toggle-ai/")
@login_required
def api_toggle_ai(conv_id: str):
    """Active ou désactive l'IA sur une conversation."""
    body = request.get_json(silent=True) or {}

    if "ai_active" not in body:
        return jsonify({"error": "Champ 'ai_active' requis"}), 400

    ai_active = bool(body["ai_active"])

    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    success = toggle_ai(conv_id, ai_active)
    if not success:
        return jsonify({"error": "Mise à jour échouée"}), 500

    action = "IA activée" if ai_active else "IA désactivée — humain en main"
    logger.info("toggle_ai : conv=%s | ai_active=%s", conv_id, ai_active)

    return jsonify({
        "status": "ok",
        "conv_id": conv_id,
        "ai_active": ai_active,
        "message": action,
    })


@dashboard_v2_bp.post("/api/conversations/<conv_id>/state/")
@login_required
def api_set_state(conv_id: str):
    """Force l'état d'une conversation."""
    body = request.get_json(silent=True) or {}
    new_state = (body.get("state") or "").strip()

    if not new_state:
        return jsonify({"error": "Champ 'state' requis"}), 400

    if new_state not in CONV_STATES:
        return jsonify({
            "error": f"État invalide : {new_state}",
            "valid_states": sorted(CONV_STATES),
        }), 400

    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    old_state = conversation["state"]
    success = update_conversation_state(conv_id, new_state)
    if not success:
        return jsonify({"error": "Mise à jour échouée"}), 500

    logger.info("Forçage état : conv=%s | %s → %s", conv_id, old_state, new_state)

    return jsonify({
        "status": "ok",
        "conv_id": conv_id,
        "old_state": old_state,
        "new_state": new_state,
    })


@dashboard_v2_bp.post("/api/conversations/<conv_id>/reply/")
@login_required
def api_reply(conv_id: str):
    """
    Envoie un message admin directement depuis le dashboard.
    Utilise send_message_direct pour bypasser la normalisation.
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()

    if not message:
        return jsonify({"error": "Message vide"}), 400

    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    phone = conversation.get("phone") or ""
    if not phone:
        return jsonify({"error": "Numéro de téléphone introuvable"}), 400

    # Sauvegarder le message admin en DB
    from webhook_app.database_conv import save_message
    save_message(
        conv_id,
        role="assistant",
        content=f"[ADMIN] {message}",
        metadata={"source": "admin_dashboard"},
    )

    # Envoyer via WhatsApp
    try:
        from webhook_app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        ok = wa.send_message_direct(
            chatId=phone,
            message=message,
            conv_id=conv_id,
        )
        if not ok:
            return jsonify({"error": "Envoi WhatsApp échoué"}), 500
    except Exception as e:
        logger.exception("Erreur envoi admin pour %s : %s", conv_id, e)
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "message": "Message envoyé"})


# ══════════════════════════════════════════════════════════════════════════════
# API — KNOWLEDGE BASE
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/kb/stats/")
@login_required
def api_kb_stats():
    """Statistiques de la knowledge base."""
    stats = count_chunks_by_product()
    for row in stats:
        _serialize_datetimes(row)

    total_chunks = sum(r.get("chunk_count", 0) for r in stats)

    # Liste des fichiers .md disponibles dans KB_DIR
    available_files = []
    if os.path.exists(KB_DIR):
        for f in os.listdir(KB_DIR):
            if f.endswith(".md"):
                available_files.append(f.replace(".md", ""))

    return jsonify({
        "products": stats,
        "total_chunks": total_chunks,
        "product_count": len(stats),
        "available_files": available_files,
    })


@dashboard_v2_bp.post("/api/kb/ingest/")
@login_required
def api_kb_ingest():
    """Ingère un produit spécifique."""
    body = request.get_json(silent=True) or {}
    product_id = (body.get("product_id") or "").strip()

    if not product_id:
        return jsonify({"error": "Champ 'product_id' requis"}), 400

    try:
        from webhook_app.rag.ingestion import ingest_product
        result = ingest_product(product_id, force=True)
    except Exception as e:
        logger.exception("Erreur ingestion %s : %s", product_id, e)
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@dashboard_v2_bp.post("/api/kb/ingest-all/")
@login_required
def api_kb_ingest_all():
    """Ingère tous les produits."""
    try:
        from webhook_app.rag.ingestion import ingest_all
        results = ingest_all(force=True)
    except Exception as e:
        logger.exception("Erreur ingestion globale : %s", e)
        return jsonify({"error": str(e)}), 500

    total = sum(r.get("chunks_created", 0) for r in results)

    return jsonify({
        "status": "ok",
        "results": results,
        "total_chunks_created": total,
        "products_ingested": len(results),
    })


@dashboard_v2_bp.post("/api/kb/upload/")
@login_required
def api_kb_upload():
    """
    Upload un ou plusieurs fichiers de documentation produit.
    Extrait le texte selon le type de fichier,
    sauvegarde dans KB_DIR et lance l'ingestion.

    Form data :
      product_id : str
      files[]    : fichiers (pdf / docx / txt / md)
    """
    product_id = (request.form.get("product_id") or "").strip()
    if not product_id:
        return jsonify({"error": "product_id requis"}), 400

    files = request.files.getlist("files[]")
    if not files:
        return jsonify({"error": "Aucun fichier reçu"}), 400

    os.makedirs(KB_DIR, exist_ok=True)

    results = []
    combined_text = ""

    for file in files:
        filename = file.filename or ""
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        try:
            text = _extract_text(file, ext)
            if text:
                combined_text += f"\n\n<!-- Source: {filename} -->\n\n{text}"
                results.append({"file": filename, "status": "ok", "chars": len(text)})
            else:
                results.append({"file": filename, "status": "empty"})
        except Exception as e:
            logger.warning("Extraction échouée pour %s : %s", filename, e)
            results.append({"file": filename, "status": "error", "error": str(e)})

    if not combined_text.strip():
        return jsonify({"error": "Aucun texte extrait des fichiers", "files": results}), 400

    # Sauvegarder le texte combiné dans KB_DIR
    md_path = os.path.join(KB_DIR, f"{product_id}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(combined_text.strip())

    # Lancer l'ingestion
    try:
        from webhook_app.rag.ingestion import ingest_product
        ingestion_result = ingest_product(
            product_id,
            force=True,
            text_override=combined_text.strip(),
        )
    except Exception as e:
        logger.exception("Erreur ingestion après upload : %s", e)
        return jsonify({
            "error": f"Upload OK mais ingestion échouée : {e}",
            "files": results,
        }), 500

    return jsonify({
        "status": "ok",
        "files": results,
        "ingestion": ingestion_result,
    })





# ══════════════════════════════════════════════════════════════════════════════
# PAGES HTML v2.1.0
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/blacklist/")
@login_required
def blacklist():
    return render_template("dashboard_v2/blacklist.html")


@dashboard_v2_bp.get("/hours/")
@login_required
def hours():
    return render_template("dashboard_v2/hours.html")


@dashboard_v2_bp.get("/escalations/")
@login_required
def escalations():
    return render_template("dashboard_v2/escalations.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — BLACKLIST
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/blacklist/")
@login_required
def api_list_blacklist():
    from webhook_app.database_v21 import list_blacklist
    items = list_blacklist()
    return jsonify({"blacklist": items, "count": len(items)})


@dashboard_v2_bp.post("/api/blacklist/")
@login_required
def api_add_blacklist():
    from webhook_app.database_v21 import add_to_blacklist
    body = request.get_json(silent=True) or {}
    phone = (body.get("phone") or "").strip()
    reason = (body.get("reason") or "").strip()
    if not phone:
        return jsonify({"error": "Numéro requis"}), 400
    add_to_blacklist(phone, reason)
    return jsonify({"status": "ok", "phone": phone})


@dashboard_v2_bp.delete("/api/blacklist/<phone>/")
@login_required
def api_remove_blacklist(phone: str):
    from webhook_app.database_v21 import remove_from_blacklist
    ok = remove_from_blacklist(phone)
    if not ok:
        return jsonify({"error": "Numéro non trouvé"}), 404
    return jsonify({"status": "ok"})


# ══════════════════════════════════════════════════════════════════════════════
# API — HEURES D'OUVERTURE
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/hours/")
@login_required
def api_get_hours():
    from webhook_app.database_v21 import get_business_hours, is_business_open
    hours = get_business_hours()
    is_open, msg = is_business_open()
    return jsonify({
        "hours": hours,
        "is_open_now": is_open,
        "closed_message": msg,
    })


@dashboard_v2_bp.put("/api/hours/<int:day>/")
@login_required
def api_update_hours(day: int):
    from webhook_app.database_v21 import update_business_hours
    body = request.get_json(silent=True) or {}
    is_open    = bool(body.get("is_open", True))
    open_time  = (body.get("open_time") or "08:00").strip()
    close_time = (body.get("close_time") or "20:00").strip()
    if day not in range(7):
        return jsonify({"error": "Jour invalide (0-6)"}), 400
    ok = update_business_hours(day, is_open, open_time, close_time)
    return jsonify({"status": "ok" if ok else "no_change"})


# ══════════════════════════════════════════════════════════════════════════════
# API — HISTORIQUE ESCALADES
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/escalations/")
@login_required
def api_get_escalations():
    from webhook_app.database_v21 import get_escalation_history, get_escalation_stats
    limit  = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))
    history = get_escalation_history(limit=limit, offset=offset)
    stats   = get_escalation_stats()
    for row in history:
        _serialize_datetimes(row)
    return jsonify({
        "escalations": history,
        "stats": stats,
        "count": len(history),
    })
# ══════════════════════════════════════════════════════════════════════════════
# API — PROMPTS SYSTÈME
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/prompts/")
@login_required
def api_get_prompts():
    """Retourne tous les prompts système éditables."""
    from webhook_app.llm.prompts import STATE_PROMPTS
    from webhook_app.conversation.context_builder import BASE_SYSTEM_PROMPT

    prompts_data = {
        "base": {
            "key": "base",
            "label": "Prompt système de base",
            "description": "Rôle, ton, règles générales et format des réponses",
            "content": BASE_SYSTEM_PROMPT,
            "editable": True,
        }
    }

    for state, content in STATE_PROMPTS.items():
        prompts_data[state] = {
            "key": state,
            "label": f"État : {state}",
            "description": f"Instructions spécifiques pour l'état {state}",
            "content": content,
            "editable": True,
        }

    return jsonify({"prompts": prompts_data})


@dashboard_v2_bp.put("/api/prompts/<key>/")
@login_required
def api_update_prompt(key: str):
    body = request.get_json(silent=True) or {}
    content = body.get("content", "").strip()

    if not content:
        return jsonify({"error": "Contenu vide"}), 400

    from webhook_app.database_v21 import upsert_prompt
    from webhook_app.conversation.context_builder import BASE_SYSTEM_PROMPT
    from webhook_app.llm.prompts import STATE_PROMPTS

    # Définir le label
    labels = {
        "base": "Prompt système de base",
        "new_prospect": "Nouveau prospect",
        "interested_lead": "Prospect intéressé",
        "pre_sale": "Pre-sale",
        "payment_failed": "Paiement échoué",
        "payment_abandoned": "Paiement abandonné",
        "payment_success": "Achat réussi",
        "post_sale": "Post-sale",
        "support": "Support",
        "escalation": "Escalade",
    }
    label = labels.get(key, key)

    try:
        ok = upsert_prompt(key, label, content)
        if not ok:
            return jsonify({"error": "Sauvegarde DB échouée"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    logger.info("Prompt '%s' sauvegardé en DB", key)

    return jsonify({
        "status": "ok",
        "key": key,
        "message": "Prompt sauvegardé en base de données",
    })


@dashboard_v2_bp.get("/api/conversations/<conv_id>/stream/")
@login_required
def stream_messages(conv_id: str):
    """SSE — pousse les nouveaux messages en temps réel."""
    from flask import Response, stream_with_context
    import json
    import time

    def generate():
        last_count = 0
        while True:
            try:
                history = fetch_history(conv_id, limit=50)
                if len(history) > last_count:
                    last_count = len(history)
                    for msg in history:
                        _serialize_datetimes(msg)
                    data = json.dumps({"history": history})
                    yield f"data: {data}\n\n"
                # Heartbeat toutes les 20s pour garder la connexion vivante
                yield ": heartbeat\n\n"
            except Exception as e:
                logger.warning("SSE stream erreur conv=%s : %s", conv_id, e)
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                break
            time.sleep(3)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES PRIVÉS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_text(file, ext: str) -> str:
    """Extrait le texte d'un fichier selon son extension."""
    if ext in ("md", "txt"):
        return file.read().decode("utf-8", errors="ignore")

    elif ext == "pdf":
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(file.read())) as pdf:
                pages = []
                for page in pdf.pages:
                    text = page.extract_text()
                    if text:
                        pages.append(text)
                return "\n\n".join(pages)
        except ImportError:
            raise RuntimeError("pdfplumber non installé — pip install pdfplumber")

    elif ext == "docx":
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(file.read()))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paragraphs)
        except ImportError:
            raise RuntimeError("python-docx non installé — pip install python-docx")

    else:
        raise ValueError(f"Format non supporté : .{ext}")


def _get_conversation_stats() -> dict:
    """Calcule les stats globales des conversations."""
    try:
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(conn, """
                SELECT
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE ai_active = TRUE)  as ai_active,
                    COUNT(*) FILTER (WHERE ai_active = FALSE) as human_active,
                    COUNT(*) FILTER (WHERE state = 'escalation') as escalations,
                    COUNT(*) FILTER (WHERE state IN ('payment_success','post_sale')) as converted,
                    COUNT(*) FILTER (WHERE state = 'new_prospect') as prospects
                FROM conversations
            """, fetch="one")
            return dict(row) if row else {}
    except Exception as e:
        logger.warning("Stats conversations échouées : %s", e)
        return {}


def _fetch_sale_data(sale_id: str) -> dict | None:
    """Récupère les données de vente depuis fact_sales."""
    try:
        with get_connection(readonly=True) as conn:
            row = execute_with_retry(
                conn,
                """
                SELECT product_id, product_name, amount_value, currency,
                       status, completed_at, failed_at, abandoned_at
                FROM fact_sales WHERE sale_id = %s LIMIT 1
                """,
                (sale_id,),
                fetch="one",
            )
            if row:
                d = dict(row)
                _serialize_datetimes(d)
                return d
    except Exception as e:
        logger.warning("fetch_sale_data échoué pour %s : %s", sale_id, e)
    return None


def _serialize_datetimes(d: dict) -> None:
    """Convertit les datetime en ISO string pour JSON."""
    import datetime
    for key, value in d.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            d[key] = value.isoformat()