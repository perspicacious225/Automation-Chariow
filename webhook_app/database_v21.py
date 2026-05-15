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
    """
    Initialise les prompts par défaut en DB si absents.
    Appelé au démarrage de l'app.
    default_prompts : { key: { label, content } }
    """
    existing = get_all_prompts()
    for key, data in default_prompts.items():
        if key not in existing:
            upsert_prompt(key, data["label"], data["content"])
            logger.info("Prompt initialisé en DB : %s", key)
        else:
            logger.debug("Prompt déjà en DB : %s", key)


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
            ORDER BY product_id, filename
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