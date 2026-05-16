"""
database_v21.py — CHARIOW v2.1.0 : helpers nouvelles tables
=============================================================
Gère les 5 nouvelles tables du sprint v2.1.0 :
  - system_prompts  : prompts système persistants
  - kb_sources      : fichiers sources KB persistants
  - blacklist       : numéros bloqués
  - business_hours  : heures d'ouverture
  - escalation_log  : historique des escalades

Réutilise get_connection et execute_with_retry depuis database_pg.py.
"""

import logging
import datetime
from typing import Optional

from psycopg2.extras import Json

from webhook_app.database_pg import get_connection, execute_with_retry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_prompts() -> dict[str, dict]:
    """
    Retourne tous les prompts système depuis la DB.
    Format : { key: { key, label, content, is_active } }
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            "SELECT key, label, content, is_active FROM system_prompts ORDER BY key",
            fetch="all",
        ) or []
        return {row["key"]: dict(row) for row in rows}


def get_prompt(key: str) -> Optional[str]:
    """
    Retourne le contenu d'un prompt par sa clé.
    Retourne None si inexistant ou inactif.
    """
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT content FROM system_prompts
            WHERE key = %s AND is_active = TRUE
            LIMIT 1
            """,
            (key,),
            fetch="one",
        )
        return row["content"] if row else None


def upsert_prompt(key: str, label: str, content: str) -> bool:
    """
    Crée ou met à jour un prompt système.
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            INSERT INTO system_prompts (key, label, content)
            VALUES (%s, %s, %s)
            ON CONFLICT (key) DO UPDATE SET
                content    = EXCLUDED.content,
                label      = EXCLUDED.label,
                updated_at = NOW()
            """,
            (key, label, content),
        )
        return (rc or 0) >= 0


def init_default_prompts(default_prompts: dict[str, dict]) -> None:
    existing = get_all_prompts()
    for key, data in default_prompts.items():
        if key not in existing:
            upsert_prompt(key, data["label"], data["content"])
            logger.info("Prompt initialisé en DB : %s", key)
        else:
            # Mettre à jour uniquement si le contenu a changé
            if existing[key]["content"] != data["content"]:
                upsert_prompt(key, data["label"], data["content"])
                logger.info("Prompt mis à jour en DB : %s", key)
            else:
                logger.debug("Prompt inchangé : %s", key)



# =============================
# CHERCHER UNE TRANSACTION D'UN PROSPECT AVEC SON MAIL OU NUMERO DE PAIEMENT
#==============================

def find_sale_by_email(email: str) -> dict | None:
    """
    Recherche une transaction dans fact_sales par email.
    Utilisé quand le numéro de téléphone ne matche pas.
    """
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT sale_id, phone, product_id, product_name,
                   amount_value, currency, status,
                   completed_at, failed_at, abandoned_at
            FROM fact_sales
            WHERE LOWER(email) = LOWER(%s)
            ORDER BY
                CASE status
                    WHEN 'completed' THEN 1
                    WHEN 'failed'    THEN 2
                    WHEN 'abandoned' THEN 3
                    ELSE 4
                END,
                created_at DESC
            LIMIT 1
            """,
            (email.strip(),),
            fetch="one",
        )
        return dict(row) if row else None
    



def find_sale_by_identifier(
    identifier: str,
    product_id: str | None = None,
) -> dict | None:
    """
    Recherche une transaction dans fact_sales par email ou téléphone.
    Si product_id fourni → vérification croisée email/téléphone + produit.

    Retourne la transaction la plus pertinente :
    - completed en priorité
    - pour le produit demandé si fourni
    """
    identifier = identifier.strip().lower()

    # Détecter si c'est un email ou un téléphone
    is_email = "@" in identifier
    phone_clean = identifier.replace("+", "").replace(" ", "").replace("-", "")

    with get_connection(readonly=True) as conn:
        if is_email:
            sql = """
                SELECT
                    sale_id, phone, email, product_id, product_name,
                    amount_value, currency, status,
                    completed_at, failed_at, abandoned_at
                FROM fact_sales
                WHERE LOWER(email) = %s
            """
            params = [identifier]
        else:
            sql = """
                SELECT
                    sale_id, phone, email, product_id, product_name,
                    amount_value, currency, status,
                    completed_at, failed_at, abandoned_at
                FROM fact_sales
                WHERE REPLACE(REPLACE(REPLACE(phone, '+', ''), ' ', ''), '-', '')
                      LIKE %s
            """
            params = [f"%{phone_clean[-8:]}"]

        # Filtrer par produit si fourni
        if product_id:
            sql += " AND product_id = %s"
            params.append(product_id)

        # Prioriser completed
        sql += """
            ORDER BY
                CASE status
                    WHEN 'completed' THEN 1
                    WHEN 'failed'    THEN 2
                    WHEN 'abandoned' THEN 3
                    ELSE 4
                END,
                created_at DESC
            LIMIT 1
        """

        row = execute_with_retry(conn, sql, params, fetch="one")
        if not row:
            return None

        d = dict(row)
        for f in ("completed_at", "failed_at", "abandoned_at"):
            if isinstance(d.get(f), (datetime.datetime, datetime.date)):
                d[f] = d[f].isoformat()
        return d
# ══════════════════════════════════════════════════════════════════════════════
# KB SOURCES
# ══════════════════════════════════════════════════════════════════════════════

def save_kb_source(
    product_id: str,
    filename: str,
    content: str,
) -> bool:
    """
    Sauvegarde ou met à jour un fichier source KB en DB.
    Permet la réingestion sans accès au filesystem.
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            INSERT INTO kb_sources (product_id, filename, content, file_size)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (product_id, filename) DO UPDATE SET
                content    = EXCLUDED.content,
                file_size  = EXCLUDED.file_size,
                updated_at = NOW()
            """,
            (product_id, filename, content, len(content.encode("utf-8"))),
        )
        return (rc or 0) >= 0


def get_kb_sources(product_id: str) -> list[dict]:
    """
    Retourne tous les fichiers sources d'un produit depuis la DB.
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT id, product_id, filename, content, file_size, updated_at
            FROM kb_sources
            WHERE product_id = %s
            ORDER BY filename
            """,
            (product_id,),
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("updated_at"), (datetime.datetime, datetime.date)):
                d["updated_at"] = d["updated_at"].isoformat()
            result.append(d)
        return result


def get_all_kb_sources() -> list[dict]:
    """
    Retourne tous les fichiers sources de tous les produits.
    Utilisé pour la réingestion globale depuis la DB.
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT product_id, filename, content
            FROM kb_sources
            ORDER BY product_id, filename  -- ← déjà là mais vérifier
            """,
            fetch="all",
        ) or []
        return [dict(row) for row in rows]


def delete_kb_sources(product_id: str) -> int:
    """Supprime tous les fichiers sources d'un produit."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "DELETE FROM kb_sources WHERE product_id = %s",
            (product_id,),
        )
        return rc or 0


# ══════════════════════════════════════════════════════════════════════════════
# BLACKLIST
# ══════════════════════════════════════════════════════════════════════════════

def is_blacklisted(phone: str) -> bool:
    """
    Vérifie si un numéro est dans la blacklist.
    Normalise le numéro avant la comparaison.
    """
    phone_clean = phone.replace("@c.us", "").strip()
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT 1 FROM blacklist
            WHERE phone = %s OR phone = %s
            LIMIT 1
            """,
            (phone, phone_clean),
            fetch="one",
        )
        return row is not None


def add_to_blacklist(phone: str, reason: str = "", blocked_by: str = "admin") -> bool:
    """Ajoute un numéro à la blacklist."""
    phone_clean = phone.replace("@c.us", "").strip()
    with get_connection() as conn:
        execute_with_retry(
            conn,
            """
            INSERT INTO blacklist (phone, reason, blocked_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (phone) DO NOTHING
            """,
            (phone_clean, reason, blocked_by),
        )
        return True


def remove_from_blacklist(phone: str) -> bool:
    """Retire un numéro de la blacklist."""
    phone_clean = phone.replace("@c.us", "").strip()
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "DELETE FROM blacklist WHERE phone = %s OR phone = %s",
            (phone_clean, phone),
        )
        return (rc or 0) > 0


def list_blacklist() -> list[dict]:
    """Retourne tous les numéros blacklistés."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            "SELECT * FROM blacklist ORDER BY created_at DESC",
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("created_at"), (datetime.datetime, datetime.date)):
                d["created_at"] = d["created_at"].isoformat()
            result.append(d)
        return result


# ══════════════════════════════════════════════════════════════════════════════
# BUSINESS HOURS
# ══════════════════════════════════════════════════════════════════════════════

def get_business_hours() -> list[dict]:
    """
    Retourne les heures d'ouverture pour tous les jours.
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT day_of_week, is_open, open_time, close_time, timezone
            FROM business_hours
            ORDER BY day_of_week
            """,
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            # Convertir time en string HH:MM
            for field in ("open_time", "close_time"):
                if isinstance(d.get(field), datetime.time):
                    d[field] = d[field].strftime("%H:%M")
            result.append(d)
        return result


def update_business_hours(day_of_week: int, is_open: bool, open_time: str, close_time: str) -> bool:
    """Met à jour les heures d'ouverture d'un jour."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            UPDATE business_hours
            SET is_open = %s, open_time = %s, close_time = %s, updated_at = NOW()
            WHERE day_of_week = %s
            """,
            (is_open, open_time, close_time, day_of_week),
        )
        return (rc or 0) > 0


def is_business_open() -> tuple[bool, str]:
    try:
        import pytz
        tz = pytz.timezone("Africa/Abidjan")
        now = datetime.datetime.now(tz)
    except ImportError:
        now = datetime.datetime.utcnow()

    day_of_week = now.weekday()
    current_time = now.time().replace(second=0, microsecond=0)

    
    hours = get_business_hours()

    logger.info("=== BUSINESS HOURS === hours=%s", hours)  
    
    # ── Table vide → ouvert par défaut ──────────────────────
    if not hours:
        logger.debug("Aucune config business_hours — ouvert par défaut")
        return True, ""

    day_config = next((h for h in hours if h["day_of_week"] == day_of_week), None)

    # ── Jour non configuré → ouvert par défaut ──────────────
    if not day_config:
        logger.debug("Jour %s non configuré — ouvert par défaut", day_of_week)
        return True, ""

    if not day_config["is_open"]:
        day_names = ["Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi","Dimanche"]
        next_open = None
        for i in range(1, 8):
            next_day = (day_of_week + i) % 7
            next_config = next((h for h in hours if h["day_of_week"] == next_day), None)
            if next_config and next_config["is_open"]:
                next_open = f"{day_names[next_day]} à {next_config['open_time']}"
                break
        msg = f"Nous sommes actuellement fermés. Nous rouvrons {next_open or 'bientôt'}. 🙏"
        return False, msg

    open_h, open_m   = map(int, day_config["open_time"].split(":"))
    close_h, close_m = map(int, day_config["close_time"].split(":"))
    open_time  = datetime.time(open_h, open_m)
    close_time = datetime.time(close_h, close_m)

    if open_time <= current_time <= close_time:
        return True, ""
    elif current_time < open_time:
        msg = f"Nous ouvrons à {day_config['open_time']}. À tout à l'heure ! 😊"
        return False, msg
    else:
        msg = f"Nous sommes fermés pour aujourd'hui. Nous revenons demain. 🙏"
        return False, msg


# ══════════════════════════════════════════════════════════════════════════════
# ESCALATION LOG
# ══════════════════════════════════════════════════════════════════════════════

def log_escalation(
    conversation_id: str,
    phone: str,
    trigger_message: str,
    product_id: Optional[str] = None,
) -> Optional[str]:
    """
    Enregistre une nouvelle escalade dans l'historique.
    Retourne l'UUID de l'entrée créée.
    """
    with get_connection() as conn:
        row = execute_with_retry(
            conn,
            """
            INSERT INTO escalation_log
                (conversation_id, phone, trigger_message, product_id, resolution)
            VALUES (%s, %s, %s, %s, 'pending')
            RETURNING id
            """,
            (conversation_id, phone, trigger_message[:500], product_id),
            fetch="one",
        )
        return str(row["id"]) if row else None


def resolve_escalation(
    conversation_id: str,
    resolution: str,
) -> bool:
    """
    Marque une escalade comme résolue.
    resolution : 'reprise' | 'resolu' | 'pause'
    Calcule automatiquement la durée en minutes.
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            UPDATE escalation_log
            SET
                resolution       = %s,
                resolved_at      = NOW(),
                duration_minutes = EXTRACT(EPOCH FROM (NOW() - escalated_at)) / 60
            WHERE conversation_id = %s
              AND resolution = 'pending'
            """,
            (resolution, conversation_id),
        )
        return (rc or 0) > 0


def get_escalation_history(
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Retourne l'historique des escalades avec stats.
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT
                e.*,
                ROUND(e.duration_minutes::numeric, 1) AS duration_minutes
            FROM escalation_log e
            ORDER BY e.escalated_at DESC
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            for field in ("escalated_at", "resolved_at"):
                if isinstance(d.get(field), (datetime.datetime, datetime.date)):
                    d[field] = d[field].isoformat()
            result.append(d)
        return result


def get_escalation_stats() -> dict:
    """
    Retourne les statistiques globales des escalades.
    """
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT
                COUNT(*)                                           AS total,
                COUNT(*) FILTER (WHERE resolution = 'pending')    AS pending,
                COUNT(*) FILTER (WHERE resolution = 'resolu')     AS resolved,
                COUNT(*) FILTER (WHERE resolution = 'reprise')    AS resumed,
                ROUND(AVG(duration_minutes)::numeric, 1)          AS avg_duration_min,
                COUNT(*) FILTER (
                    WHERE escalated_at >= NOW() - INTERVAL '24 hours'
                )                                                  AS last_24h
            FROM escalation_log
            """,
            fetch="one",
        )
        return dict(row) if row else {}
    

# ══════════════════════════════════════════════════════════════════════════════
# QUICK REPLIES
# ══════════════════════════════════════════════════════════════════════════════

def get_quick_replies(category: str | None = None) -> list[dict]:
    """Retourne les réponses rapides actives."""
    sql = "SELECT * FROM quick_replies WHERE is_active = TRUE"
    params = []
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY category, title"
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, params or None, fetch="all") or []
        result = []
        for row in rows:
            d = dict(row)
            for f in ("created_at", "updated_at"):
                if isinstance(d.get(f), (datetime.datetime, datetime.date)):
                    d[f] = d[f].isoformat()
            result.append(d)
        return result


def create_quick_reply(title: str, content: str, category: str = "general") -> bool:
    """Crée une nouvelle réponse rapide."""
    with get_connection() as conn:
        execute_with_retry(
            conn,
            "INSERT INTO quick_replies (title, content, category) VALUES (%s, %s, %s)",
            (title, content, category),
        )
        return True


def update_quick_reply(reply_id: str, title: str, content: str, category: str) -> bool:
    """Met à jour une réponse rapide."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            UPDATE quick_replies
            SET title=%s, content=%s, category=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (title, content, category, reply_id),
        )
        return (rc or 0) > 0


def delete_quick_reply(reply_id: str) -> bool:
    """Supprime une réponse rapide."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "DELETE FROM quick_replies WHERE id = %s",
            (reply_id,),
        )
        return (rc or 0) > 0


def increment_quick_reply_usage(reply_id: str) -> None:
    """Incrémente le compteur d'utilisation."""
    with get_connection() as conn:
        execute_with_retry(
            conn,
            "UPDATE quick_replies SET usage_count = usage_count + 1 WHERE id = %s",
            (reply_id,),
        )


# ══════════════════════════════════════════════════════════════════════════════
# FEEDBACK MESSAGES
# ══════════════════════════════════════════════════════════════════════════════

def set_message_feedback(
    message_id: str,
    feedback: str,
    note: str | None = None,
) -> bool:
    """
    Enregistre le feedback admin sur un message IA.
    feedback : 'good' | 'bad'
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            UPDATE messages
            SET feedback      = %s,
                feedback_at   = NOW(),
                feedback_note = %s
            WHERE id = %s
            """,
            (feedback, note, message_id),
        )
        return (rc or 0) > 0


def get_feedback_stats() -> dict:
    """Stats globales du feedback qualité."""
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT
                COUNT(*) FILTER (WHERE feedback = 'good')  AS good,
                COUNT(*) FILTER (WHERE feedback = 'bad')   AS bad,
                COUNT(*) FILTER (WHERE feedback IS NOT NULL) AS total
            FROM messages
            WHERE role = 'assistant'
            """,
            fetch="one",
        )
        return dict(row) if row else {}


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS CONVERSION
# ══════════════════════════════════════════════════════════════════════════════

def get_conversion_stats(days_back: int = 30) -> dict:
    """Stats globales de conversion IA."""
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            "SELECT * FROM get_conversion_stats(%s)",
            (days_back,),
            fetch="one",
        )
        return dict(row) if row else {}


def get_conversion_by_state(days_back: int = 30) -> list[dict]:
    """Taux de conversion par état de départ."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            "SELECT * FROM get_conversion_by_state(%s)",
            (days_back,),
            fetch="all",
        ) or []
        return [dict(r) for r in rows]


def get_hourly_activity() -> list[dict]:
    """Activité par heure sur 30 jours."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            "SELECT * FROM hourly_activity",
            fetch="all",
        ) or []
        return [dict(r) for r in rows]


def get_volume_by_day(days_back: int = 30) -> list[dict]:
    """Volume de conversations par jour."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT
                DATE(created_at AT TIME ZONE 'Africa/Abidjan') AS day,
                COUNT(*)                                        AS conversations,
                COUNT(*) FILTER (WHERE state IN ('payment_success','post_sale')) AS converted
            FROM conversations
            WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
            GROUP BY 1
            ORDER BY 1
            """,
            (days_back,),
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            if hasattr(d.get("day"), "isoformat"):
                d["day"] = d["day"].isoformat()
            result.append(d)
        return result


def get_top_products_by_conversations(days_back: int = 30) -> list[dict]:
    """Produits les plus demandés en conversation."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT
                COALESCE(product_id, 'inconnu') AS product_id,
                COUNT(*)                         AS conversations,
                COUNT(*) FILTER (WHERE state IN ('payment_success','post_sale')) AS converted
            FROM conversations
            WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
            GROUP BY product_id
            ORDER BY conversations DESC
            LIMIT 10
            """,
            (days_back,),
            fetch="all",
        ) or []
        return [dict(r) for r in rows]
    




# ══════════════════════════════════════════════════════════════════════════════
# MULTI-LANGUE
# ══════════════════════════════════════════════════════════════════════════════

def set_conversation_language(conv_id: str, language: str) -> bool:
    """Met à jour la langue détectée d'une conversation."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "UPDATE conversations SET language = %s WHERE id = %s",
            (language, conv_id),
        )
        return (rc or 0) > 0


# ══════════════════════════════════════════════════════════════════════════════
# ENRICHISSEMENT CONTEXTE CLIENT
# ══════════════════════════════════════════════════════════════════════════════

def get_customer_transactions(phone: str) -> list[dict]:
    """
    Récupère toutes les transactions d'un client depuis fact_sales.
    Normalise le numéro pour matcher les différents formats.
    """
    phone_clean = phone.replace("@c.us", "").strip()

    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT
                phone, product_id, product_name,
                amount_value, currency, status,
                transaction_type, hours_since_created,
                created_at, completed_at, failed_at, abandoned_at
            FROM customer_transaction_context
            WHERE phone LIKE %s
               OR phone = %s
            ORDER BY created_at DESC
            LIMIT 10
            """,
            (f"%{phone_clean[-8:]}", phone_clean),
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            for f in ("created_at","completed_at","failed_at","abandoned_at"):
                if isinstance(d.get(f), (datetime.datetime, datetime.date)):
                    d[f] = d[f].isoformat()
            if d.get("hours_since_created"):
                d["hours_since_created"] = round(float(d["hours_since_created"]), 1)
            result.append(d)
        return result


# ══════════════════════════════════════════════════════════════════════════════
# RÉSUMÉ ESCALADE
# ══════════════════════════════════════════════════════════════════════════════

def save_escalation_summary(conversation_id: str, summary: str) -> bool:
    """Sauvegarde le résumé généré par Claude sur l'escalade."""
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            """
            UPDATE escalation_log
            SET summary = %s
            WHERE conversation_id = %s
              AND resolution = 'pending'
            """,
            (summary, conversation_id),
        )
        return (rc or 0) > 0


# ══════════════════════════════════════════════════════════════════════════════
# A/B TESTING
# ══════════════════════════════════════════════════════════════════════════════

def get_active_experiment() -> dict | None:
    """Retourne l'expérience A/B active si elle existe."""
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT * FROM ab_experiments
            WHERE is_active = TRUE
              AND (ended_at IS NULL OR ended_at > NOW())
            ORDER BY started_at DESC
            LIMIT 1
            """,
            fetch="one",
        )
        return dict(row) if row else None


def get_or_assign_variant(
    experiment_id: str,
    conversation_id: str,
    phone: str,
    split_percent: int = 50,
) -> str:
    """
    Retourne le variant A/B assigné à cette conversation.
    Assignation déterministe par hash du numéro — stable entre les sessions.
    """
    import hashlib
    # Vérifier si déjà assigné
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT variant FROM ab_assignments
            WHERE experiment_id = %s AND conversation_id = %s
            LIMIT 1
            """,
            (experiment_id, conversation_id),
            fetch="one",
        )
        if row:
            return row["variant"]

    # Assigner déterministiquement par hash du numéro
    phone_clean = phone.replace("@c.us", "").strip()
    h = int(hashlib.sha256(phone_clean.encode()).hexdigest()[:8], 16) % 100
    variant = "B" if h < split_percent else "A"

    # Sauvegarder l'assignation
    with get_connection() as conn:
        execute_with_retry(
            conn,
            """
            INSERT INTO ab_assignments
                (experiment_id, conversation_id, phone, variant)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (experiment_id, conversation_id) DO NOTHING
            """,
            (experiment_id, conversation_id, phone_clean, variant),
        )
    return variant


def get_ab_results(experiment_id: str) -> dict:
    """Retourne les résultats comparatifs d'une expérience A/B."""
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT
                aa.variant,
                COUNT(*)                                           AS total,
                COUNT(*) FILTER (WHERE ca.converted)              AS converted,
                ROUND(
                    COUNT(*) FILTER (WHERE ca.converted)::NUMERIC
                    / NULLIF(COUNT(*), 0) * 100, 1
                )                                                  AS conversion_rate,
                COUNT(*) FILTER (WHERE ca.has_escalated)          AS escalated,
                ROUND(AVG(ca.message_count), 1)                   AS avg_messages
            FROM ab_assignments aa
            JOIN conversation_analytics ca ON ca.id = aa.conversation_id
            WHERE aa.experiment_id = %s
            GROUP BY aa.variant
            ORDER BY aa.variant
            """,
            (experiment_id,),
            fetch="all",
        ) or []
        return {
            "experiment_id": experiment_id,
            "results": [dict(r) for r in rows],
        }