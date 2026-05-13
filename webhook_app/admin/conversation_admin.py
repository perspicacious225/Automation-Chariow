"""
admin/conversation_admin.py — Interface admin conversations CHARIOW v2
=======================================================================
Routes Flask pour :
  - Lister et consulter les conversations
  - Basculer ai_active (humain ↔ IA)
  - Forcer un état conversationnel
  - Consulter les stats de la knowledge base
  - Déclencher l'ingestion KB

Toutes les routes sont protégées par @login_required (même système que dashboard).
"""

import logging
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required

from webhook_app.database_conv import (
    list_conversations,
    get_conversation_by_id,
    get_conversation_by_phone,
    toggle_ai,
    update_conversation_state,
    fetch_history,
    count_chunks_by_product,
    CONV_STATES,
)

logger = logging.getLogger(__name__)

conv_admin_bp = Blueprint(
    "conv_admin",
    __name__,
    url_prefix="/admin/conversations",
    template_folder="templates",
)


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS — LISTE
# ══════════════════════════════════════════════════════════════════════════════

@conv_admin_bp.get("/")
@login_required
def list_conv():
    """
    Liste les conversations avec filtres optionnels.
    GET /admin/conversations/?state=support&ai_active=false&limit=20&offset=0
    """
    state = request.args.get("state") or None
    ai_active_param = request.args.get("ai_active")
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = int(request.args.get("offset", 0))

    # Conversion du paramètre ai_active
    ai_active = None
    if ai_active_param is not None:
        ai_active = ai_active_param.lower() not in ("false", "0", "no")

    conversations = list_conversations(
        state=state,
        ai_active=ai_active,
        limit=limit,
        offset=offset,
    )

    # Sérialisation des champs datetime
    for conv in conversations:
        _serialize_datetimes(conv)

    return jsonify({
        "conversations": conversations,
        "count": len(conversations),
        "filters": {
            "state": state,
            "ai_active": ai_active,
            "limit": limit,
            "offset": offset,
        },
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS — DÉTAIL
# ══════════════════════════════════════════════════════════════════════════════

@conv_admin_bp.get("/<conv_id>")
@login_required
def get_conv(conv_id: str):
    """
    Retourne le détail d'une conversation avec son historique.
    GET /admin/conversations/<uuid>
    """
    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    history = fetch_history(conv_id, limit=50)

    _serialize_datetimes(conversation)
    for msg in history:
        _serialize_datetimes(msg)

    return jsonify({
        "conversation": conversation,
        "history": history,
        "message_count": len(history),
    }), 200


@conv_admin_bp.get("/by-phone/<path:phone>")
@login_required
def get_conv_by_phone(phone: str):
    """
    Récupère une conversation par numéro de téléphone.
    GET /admin/conversations/by-phone/+2250789333113
    """
    conversation = get_conversation_by_phone(phone)
    if not conversation:
        return jsonify({"error": "Aucune conversation pour ce numéro"}), 404

    history = fetch_history(str(conversation["id"]), limit=50)

    _serialize_datetimes(conversation)
    for msg in history:
        _serialize_datetimes(msg)

    return jsonify({
        "conversation": conversation,
        "history": history,
        "message_count": len(history),
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# TOGGLE AI — HUMAIN ↔ IA
# ══════════════════════════════════════════════════════════════════════════════

@conv_admin_bp.post("/<conv_id>/toggle-ai")
@login_required
def toggle_ai_route(conv_id: str):
    """
    Active ou désactive l'IA sur une conversation.
    POST /admin/conversations/<uuid>/toggle-ai
    Body : { "ai_active": true | false }

    ai_active = false → l'humain prend la main
    ai_active = true  → l'IA reprend (avec tout le contexte conservé)
    """
    body = request.get_json(silent=True) or {}

    if "ai_active" not in body:
        return jsonify({"error": "Champ 'ai_active' requis (true/false)"}), 400

    ai_active = bool(body["ai_active"])

    conversation = get_conversation_by_id(conv_id)
    if not conversation:
        return jsonify({"error": "Conversation introuvable"}), 404

    success = toggle_ai(conv_id, ai_active)
    if not success:
        return jsonify({"error": "Mise à jour échouée"}), 500

    action = "IA activée — reprise automatique" if ai_active else "IA désactivée — humain en main"
    logger.info("toggle_ai : conv=%s | ai_active=%s", conv_id, ai_active)

    return jsonify({
        "status": "ok",
        "conv_id": conv_id,
        "ai_active": ai_active,
        "message": action,
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# FORCER UN ÉTAT
# ══════════════════════════════════════════════════════════════════════════════

@conv_admin_bp.post("/<conv_id>/state")
@login_required
def set_state(conv_id: str):
    """
    Force l'état d'une conversation.
    POST /admin/conversations/<uuid>/state
    Body : { "state": "support" }

    Utile pour corriger manuellement un état mal détecté.
    """
    body = request.get_json(silent=True) or {}
    new_state = body.get("state", "").strip()

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

    logger.info(
        "Forçage état admin : conv=%s | %s → %s",
        conv_id, old_state, new_state
    )

    return jsonify({
        "status": "ok",
        "conv_id": conv_id,
        "old_state": old_state,
        "new_state": new_state,
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE — STATS ET INGESTION
# ══════════════════════════════════════════════════════════════════════════════

@conv_admin_bp.get("/kb/stats")
@login_required
def kb_stats():
    """
    Retourne les statistiques de la knowledge base.
    GET /admin/conversations/kb/stats
    """
    stats = count_chunks_by_product()
    for row in stats:
        _serialize_datetimes(row)

    total_chunks = sum(r.get("chunk_count", 0) for r in stats)

    return jsonify({
        "products": stats,
        "total_chunks": total_chunks,
        "product_count": len(stats),
    }), 200


@conv_admin_bp.post("/kb/ingest")
@login_required
def kb_ingest():
    """
    Déclenche l'ingestion d'un produit dans la KB.
    POST /admin/conversations/kb/ingest
    Body : { "product_id": "prd_k3eyyy" }
    """
    body = request.get_json(silent=True) or {}
    product_id = (body.get("product_id") or "").strip()

    if not product_id:
        return jsonify({"error": "Champ 'product_id' requis"}), 400

    try:
        from webhook_app.rag.ingestion import ingest_product
        result = ingest_product(product_id, force=True)
    except Exception as e:
        logger.exception("Erreur ingestion KB pour %s : %s", product_id, e)
        return jsonify({"error": str(e)}), 500

    return jsonify(result), 200


@conv_admin_bp.post("/kb/ingest/all")
@login_required
def kb_ingest_all():
    """
    Déclenche l'ingestion de tous les produits de la KB.
    POST /admin/conversations/kb/ingest/all
    ⚠️  Opération longue si beaucoup de produits — à lancer hors heure de pointe.
    """
    try:
        from webhook_app.rag.ingestion import ingest_all
        results = ingest_all(force=True)
    except Exception as e:
        logger.exception("Erreur ingestion KB globale : %s", e)
        return jsonify({"error": str(e)}), 500

    total = sum(r.get("chunks_created", 0) for r in results)

    return jsonify({
        "status": "ok",
        "results": results,
        "total_chunks_created": total,
        "products_ingested": len(results),
    }), 200


# ══════════════════════════════════════════════════════════════════════════════
# UTILITAIRES
# ══════════════════════════════════════════════════════════════════════════════

def _serialize_datetimes(d: dict) -> None:
    """
    Convertit les objets datetime en strings ISO pour la sérialisation JSON.
    Modifie le dict en place.
    """
    import datetime
    for key, value in d.items():
        if isinstance(value, (datetime.datetime, datetime.date)):
            d[key] = value.isoformat()