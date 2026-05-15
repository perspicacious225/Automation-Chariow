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
    sauvegarde chaque fichier séparément dans kb_sources (DB),
    puis lance l'ingestion avec le texte combiné.

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

    from webhook_app.database_v21 import save_kb_source, get_kb_sources

    results = []

    # ── Sauvegarder chaque fichier séparément en DB ───────────────
    for file in files:
        filename = file.filename or f"{product_id}_source"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        try:
            text = _extract_text(file, ext)
            if text:
                # Sauvegarder individuellement — ON CONFLICT écrase l'existant
                save_kb_source(
                    product_id=product_id,
                    filename=filename,
                    content=text,
                )
                results.append({"file": filename, "status": "ok", "chars": len(text)})
            else:
                results.append({"file": filename, "status": "empty"})
        except Exception as e:
            logger.warning("Extraction échouée pour %s : %s", filename, e)
            results.append({"file": filename, "status": "error", "error": str(e)})

    # Vérifier qu'au moins un fichier a été extrait
    ok_files = [r for r in results if r["status"] == "ok"]
    if not ok_files:
        return jsonify({"error": "Aucun texte extrait des fichiers", "files": results}), 400

    # ── Récupérer toutes les sources du produit depuis la DB ──────
    # (inclut les fichiers précédents + les nouveaux)
    all_sources = get_kb_sources(product_id)
    combined_text = "\n\n".join(
        s["content"]
        for s in sorted(all_sources, key=lambda x: x["filename"])
    )

    if not combined_text.strip():
        return jsonify({"error": "Texte combiné vide", "files": results}), 400

    # ── Sauvegarder aussi dans KB_DIR pour la CLI ─────────────────
    md_path = os.path.join(KB_DIR, f"{product_id}.md")
    try:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(combined_text.strip())
    except Exception as e:
        logger.warning("Sauvegarde filesystem échouée (non bloquant) : %s", e)

    # ── Lancer l'ingestion avec le texte combiné ──────────────────
    # text_override = True → ingest_product ne sauvegarde PAS en DB
    # (déjà fait individuellement ci-dessus)
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
        "sources_count": len(all_sources),
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
# PAGES HTML v2.1.1
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/analytics/")
@login_required
def analytics():
    return render_template("dashboard_v2/analytics.html")


@dashboard_v2_bp.get("/quick-replies/")
@login_required
def quick_replies():
    return render_template("dashboard_v2/quick_replies.html")


# ══════════════════════════════════════════════════════════════════════════════
# API — QUICK REPLIES
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/quick-replies/")
@login_required
def api_list_quick_replies():
    from webhook_app.database_v21 import get_quick_replies
    category = request.args.get("category")
    items = get_quick_replies(category)
    return jsonify({"quick_replies": items, "count": len(items)})


@dashboard_v2_bp.post("/api/quick-replies/")
@login_required
def api_create_quick_reply():
    from webhook_app.database_v21 import create_quick_reply
    body = request.get_json(silent=True) or {}
    title    = (body.get("title") or "").strip()
    content  = (body.get("content") or "").strip()
    category = (body.get("category") or "general").strip()
    if not title or not content:
        return jsonify({"error": "title et content requis"}), 400
    create_quick_reply(title, content, category)
    return jsonify({"status": "ok"})


@dashboard_v2_bp.put("/api/quick-replies/<reply_id>/")
@login_required
def api_update_quick_reply(reply_id: str):
    from webhook_app.database_v21 import update_quick_reply
    body = request.get_json(silent=True) or {}
    title    = (body.get("title") or "").strip()
    content  = (body.get("content") or "").strip()
    category = (body.get("category") or "general").strip()
    if not title or not content:
        return jsonify({"error": "title et content requis"}), 400
    ok = update_quick_reply(reply_id, title, content, category)
    return jsonify({"status": "ok" if ok else "not_found"})


@dashboard_v2_bp.delete("/api/quick-replies/<reply_id>/")
@login_required
def api_delete_quick_reply(reply_id: str):
    from webhook_app.database_v21 import delete_quick_reply
    ok = delete_quick_reply(reply_id)
    return jsonify({"status": "ok" if ok else "not_found"})


@dashboard_v2_bp.post("/api/quick-replies/<reply_id>/send/")
@login_required
def api_send_quick_reply(reply_id: str):
    """Envoie une réponse rapide à un client depuis le dashboard."""
    from webhook_app.database_v21 import get_quick_replies, increment_quick_reply_usage
    body = request.get_json(silent=True) or {}
    conv_id = (body.get("conv_id") or "").strip()
    if not conv_id:
        return jsonify({"error": "conv_id requis"}), 400

    # Trouver la réponse rapide
    all_replies = get_quick_replies()
    reply = next((r for r in all_replies if r["id"] == reply_id), None)
    if not reply:
        return jsonify({"error": "Réponse rapide introuvable"}), 404

    # Récupérer la conversation
    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    phone = conversation.get("phone") or ""
    message = reply["content"]

    # Envoyer via WhatsApp
    try:
        from webhook_app.services.whatsapp import WhatsAppService
        from webhook_app.database_conv import save_message
        wa = WhatsAppService()
        ok = wa.send_message_direct(chatId=phone, message=message, conv_id=conv_id)
        if not ok:
            return jsonify({"error": "Envoi WhatsApp échoué"}), 500
        save_message(conv_id, role="assistant", content=f"[QUICK] {message}",
                     metadata={"source": "quick_reply", "reply_id": reply_id})
        increment_quick_reply_usage(reply_id)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"status": "ok", "message": message})


# ══════════════════════════════════════════════════════════════════════════════
# API — FEEDBACK
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.post("/api/messages/<message_id>/feedback/")
@login_required
def api_set_feedback(message_id: str):
    from webhook_app.database_v21 import set_message_feedback
    body = request.get_json(silent=True) or {}
    feedback = (body.get("feedback") or "").strip()
    note     = (body.get("note") or "").strip() or None
    if feedback not in ("good", "bad"):
        return jsonify({"error": "feedback doit être 'good' ou 'bad'"}), 400
    ok = set_message_feedback(message_id, feedback, note)
    return jsonify({"status": "ok" if ok else "not_found"})


# ══════════════════════════════════════════════════════════════════════════════
# API — ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@dashboard_v2_bp.get("/api/analytics/")
@login_required
def api_analytics():
    from webhook_app.database_v21 import (
        get_conversion_stats,
        get_conversion_by_state,
        get_hourly_activity,
        get_volume_by_day,
        get_top_products_by_conversations,
        get_feedback_stats,
        get_escalation_stats,
    )
    days = int(request.args.get("days", 30))

    return jsonify({
        "period_days":       days,
        "conversion":        get_conversion_stats(days),
        "by_state":          get_conversion_by_state(days),
        "hourly_activity":   get_hourly_activity(),
        "volume_by_day":     get_volume_by_day(days),
        "top_products":      get_top_products_by_conversations(days),
        "feedback":          get_feedback_stats(),
        "escalation_stats":  get_escalation_stats(),
    })

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