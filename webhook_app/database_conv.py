"""
database_conv.py — CHARIOW v2 : couche base de données conversationnelle
=========================================================================
Gère les 3 nouvelles tables :
  - conversations  : contexte conversationnel par client
  - messages       : historique des échanges WhatsApp
  - knowledge_chunks : base de connaissance vectorielle (RAG)

Réutilise get_connection et execute_with_retry depuis database_pg.py.
Ne modifie pas les tables existantes de CHARIOW v1.
"""

import logging
import datetime
from typing import Optional

from psycopg2.extras import Json

from webhook_app.database_pg import get_connection, execute_with_retry

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA — Création des tables v2
# ══════════════════════════════════════════════════════════════════════════════

def ensure_conv_schemas():
    """
    Crée les tables v2 si elles n'existent pas.
    Idempotent — sans danger à appeler au démarrage.
    """
    with get_connection() as conn:

        # Extension vectorielle (pgvector)
        execute_with_retry(conn, "CREATE EXTENSION IF NOT EXISTS vector;")

        # ── conversations ─────────────────────────────────────────────────
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS conversations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone           TEXT NOT NULL,
            contact_key     TEXT,
            last_sale_id    TEXT,
            product_id      TEXT,
            state           TEXT NOT NULL DEFAULT 'new_prospect',
            ai_active       BOOLEAN NOT NULL DEFAULT TRUE,
            metadata        JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conv_phone
            ON conversations(phone);
        CREATE INDEX IF NOT EXISTS idx_conv_contact_key
            ON conversations(contact_key);
        CREATE INDEX IF NOT EXISTS idx_conv_state
            ON conversations(state);
        CREATE INDEX IF NOT EXISTS idx_conv_product
            ON conversations(product_id);
        """)

        # ── messages ──────────────────────────────────────────────────────
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS messages (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            conversation_id   UUID NOT NULL REFERENCES conversations(id)
                                  ON DELETE CASCADE,
            role              TEXT NOT NULL,
            content           TEXT NOT NULL,
            wa_message_id     TEXT UNIQUE,
            metadata          JSONB DEFAULT '{}',
            timestamp         TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_msg_conv_time
            ON messages(conversation_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_msg_wa_id
            ON messages(wa_message_id);
        """)

        # ── knowledge_chunks ──────────────────────────────────────────────
        execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            product_id  TEXT NOT NULL,
            source      TEXT NOT NULL,
            section     TEXT,
            chunk_index INTEGER,
            chunk_text  TEXT NOT NULL,
            embedding   vector(1536),
            metadata    JSONB DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_product
            ON knowledge_chunks(product_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_section
            ON knowledge_chunks(section);
        -- Index vectoriel ivfflat : à activer après la première ingestion
        -- CREATE INDEX idx_chunks_embedding
        --     ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
        --     WITH (lists = 100);
        """)

        # ── Enrichissement dim_product (colonnes supplémentaires v2) ──────
        execute_with_retry(conn, """
        ALTER TABLE dim_product
            ADD COLUMN IF NOT EXISTS url        TEXT,
            ADD COLUMN IF NOT EXISTS price_raw  INTEGER,
            ADD COLUMN IF NOT EXISTS currency   TEXT DEFAULT 'XOF',
            ADD COLUMN IF NOT EXISTS active     BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS type       TEXT DEFAULT 'formation';
        """)

        # ── Trigger updated_at sur conversations ──────────────────────────
        execute_with_retry(conn, """
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """)
        execute_with_retry(conn, """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger
                WHERE tgname = 'trg_conversations_updated_at'
            ) THEN
                CREATE TRIGGER trg_conversations_updated_at
                    BEFORE UPDATE ON conversations
                    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
            END IF;
        END $$;
        """)

    logger.info("CHARIOW v2 : schémas conversationnels vérifiés/créés.")


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS
# ══════════════════════════════════════════════════════════════════════════════

# États valides
CONV_STATES = {
    "new_prospect",
    "interested_lead",
    "pre_sale",
    "payment_failed",
    "payment_abandoned",
    "payment_success",
    "post_sale",
    "support",
    "escalation",
}


def get_or_create_conversation(
    phone: str,
    *,
    contact_key: str | None = None,
    product_id: str | None = None,
    last_sale_id: str | None = None,
    initial_state: str = "new_prospect",
) -> dict:
    """
    Récupère la conversation existante pour ce numéro de téléphone,
    ou en crée une nouvelle.
    Retourne le dict de la conversation.
    """
    with get_connection() as conn:
        row = execute_with_retry(
            conn,
            "SELECT * FROM conversations WHERE phone = %s LIMIT 1",
            (phone,),
            fetch="one",
        )
        if row:
            return dict(row)

        execute_with_retry(
            conn,
            """
            INSERT INTO conversations
                (phone, contact_key, product_id, last_sale_id, state)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (phone, contact_key, product_id, last_sale_id, initial_state),
        )
        row = execute_with_retry(
            conn,
            "SELECT * FROM conversations WHERE phone = %s LIMIT 1",
            (phone,),
            fetch="one",
        )
        return dict(row)


def get_conversation_by_phone(phone: str) -> Optional[dict]:
    """Retourne la conversation ou None si inexistante."""
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            "SELECT * FROM conversations WHERE phone = %s LIMIT 1",
            (phone,),
            fetch="one",
        )
        return dict(row) if row else None


def get_conversation_by_id(conv_id: str) -> Optional[dict]:
    """Retourne la conversation par UUID ou None."""
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            "SELECT * FROM conversations WHERE id = %s LIMIT 1",
            (conv_id,),
            fetch="one",
        )
        return dict(row) if row else None


def update_conversation_state(conv_id: str, new_state: str) -> bool:
    """
    Met à jour l'état d'une conversation.
    Retourne True si la mise à jour a réussi.
    """
    if new_state not in CONV_STATES:
        logger.warning("État invalide : %s", new_state)
        return False
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "UPDATE conversations SET state = %s WHERE id = %s",
            (new_state, conv_id),
        )
        return (rc or 0) > 0


def update_conversation_context(
    conv_id: str,
    *,
    product_id: str | None = None, target_product_id: str | None = None,
    last_sale_id: str | None = None,
    contact_key: str | None = None,
    metadata: dict | None = None,
) -> bool:
    """
    Met à jour les champs de contexte d'une conversation.
    Seuls les champs non-None sont mis à jour.
    """

    fields, values = [], []
    if product_id is not None:
        fields.append("product_id = %s"); values.append(product_id)
        
    if target_product_id is not None:
        fields.append("target_product_id = %s") ; values.append(target_product_id)
        
    if last_sale_id is not None:
        fields.append("last_sale_id = %s"); values.append(last_sale_id)
              
    if contact_key is not None:
        fields.append("contact_key = %s"); values.append(contact_key)
          
    if metadata is not None:
        fields.append("metadata = %s"); values.append(Json(metadata))
        
    if not fields:
        return False
        
    values.append(conv_id)
    
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            f"UPDATE conversations SET {', '.join(fields)} WHERE id = %s",
            values,
        )
        return (rc or 0) > 0
    

def get_conversation_summary(conv_id: str) -> dict | None:
    """
    Retourne le résumé conversationnel stocké en DB.
    Retourne None si aucun résumé ou résumé trop ancien.
    """
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            """
            SELECT conversation_summary, summary_updated_at, summary_msg_count
            FROM conversations
            WHERE id = %s
            """,
            (conv_id,),
            fetch="one",
        )
        if not row or not row["conversation_summary"]:
            return None
        return {
            "summary":          row["conversation_summary"],
            "updated_at":       row["summary_updated_at"],
            "msg_count":        row["summary_msg_count"],
        }


def update_conversation_summary(
    conv_id: str,
    summary: str,
    msg_count: int,
) -> bool:
    """
    Met à jour le résumé conversationnel en DB.
    """
    try:
        with get_connection() as conn:
            execute_with_retry(
                conn,
                """
                UPDATE conversations
                SET conversation_summary  = %s,
                    summary_updated_at    = NOW(),
                    summary_msg_count     = %s
                WHERE id = %s
                """,
                (summary, msg_count, conv_id),
            )
            return True
    except Exception as e:
        logger.warning("update_conversation_summary erreur : %s", e)
        return False


def toggle_ai(conv_id: str, active: bool) -> bool:
    """
    Active ou désactive l'IA sur une conversation.
    active=False → l'humain prend la main.
    active=True  → l'IA reprend.
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "UPDATE conversations SET ai_active = %s WHERE id = %s",
            (active, conv_id),
        )
        return (rc or 0) > 0


def list_conversations(
    state: str | None = None,
    ai_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Liste les conversations avec filtres optionnels.
    Utilisé par l'interface admin.
    """
    sql = "SELECT * FROM conversations WHERE TRUE"
    params = []
    if state:
        sql += " AND state = %s"; params.append(state)
    if ai_active is not None:
        sql += " AND ai_active = %s"; params.append(ai_active)
    sql += " ORDER BY updated_at DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, params, fetch="all") or []
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGES
# ══════════════════════════════════════════════════════════════════════════════


def get_mini_catalogue_text() -> str:
    """
    Génère le catalogue dynamique pour le Mode Juge du LLM
    à partir de la table dim_product.
    """
    from webhook_app.database_pg import get_connection, execute_with_retry
    
    try:
        with get_connection(readonly=True) as conn:
            rows = execute_with_retry(
                conn,
                "SELECT product_id, product_name, type FROM dim_product WHERE active = true",
                fetch="all"
            )
            
            if not rows:
                return "[CATALOGUE]\n(Aucun produit disponible actuellement)\n[FIN CATALOGUE]"

            lines = ["[CATALOGUE]"]
            for row in rows:
                
                lines.append(f"- {row['product_id']} : {row['product_name']} (Type: {row['type']})")
            lines.append("[FIN CATALOGUE]")
            
            return "\n".join(lines)
            
    except Exception as e:
        
        return """
        [CATALOGUE]
        - prd_k3eyyy : MICROSOFT 365 LICENCE À VIE
        [FIN CATALOGUE]
        """

def get_chunks_by_section(product_id: str, section: str) -> list[dict]:
    """
    Récupère directement les chunks d'une section spécifique pour un produit donné
    (sans utiliser la recherche vectorielle).
    """
    from webhook_app.database_pg import get_connection, execute_with_retry 
    
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT id, product_id, section, chunk_text, source, chunk_index
            FROM knowledge_chunks
            WHERE product_id = %s AND section = %s
            ORDER BY chunk_index ASC
            """,
            (product_id, section),
            fetch="all"
        )
        
        if not rows:
            return []
            
        # Convertir les lignes de la BDD en dictionnaire
        return [dict(row) for row in rows]

def save_message(
    conversation_id: str,
    role: str,
    content: str,
    *,
    wa_message_id: str | None = None,
    metadata: dict | None = None,
) -> Optional[str]:
    """
    Sauvegarde un message dans l'historique.
    Retourne l'UUID du message créé, ou None si doublon (wa_message_id).
    role : 'user' | 'assistant' | 'system'
    """
    with get_connection() as conn:
        row = execute_with_retry(
            conn,
            """
            INSERT INTO messages
                (conversation_id, role, content, wa_message_id, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (wa_message_id) DO NOTHING
            RETURNING id
            """,
            (
                conversation_id,
                role,
                content,
                wa_message_id,
                Json(metadata or {}),
            ),
            fetch="one",
        )
        return str(row["id"]) if row else None


def fetch_history(conv_id: str, limit: int = 30) -> list[dict]:
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT id, role, content, timestamp, metadata, feedback
            FROM (
                SELECT id, role, content, timestamp, metadata, feedback
                FROM messages
                WHERE conversation_id = %s
                ORDER BY timestamp DESC
                LIMIT %s
            ) sub
            ORDER BY timestamp ASC
            """,
            (conv_id, limit),
            fetch="all",
        ) or []
        result = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("timestamp"), (datetime.datetime, datetime.date)):
                d["timestamp"] = d["timestamp"].isoformat()
            if d.get("id"):
                d["id"] = str(d["id"])
            result.append(d)
        return result

def message_already_exists(wa_message_id: str) -> bool:
    """Vérifie si un message entrant a déjà été traité (idempotence)."""
    if not wa_message_id:
        return False
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            "SELECT 1 FROM messages WHERE wa_message_id = %s LIMIT 1",
            (wa_message_id,),
            fetch="one",
        )
        return row is not None


# ══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE CHUNKS (RAG)
# ══════════════════════════════════════════════════════════════════════════════

def insert_chunk(
    product_id: str,
    source: str,
    chunk_text: str,
    embedding: list[float],
    *,
    section: str | None = None,
    chunk_index: int | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Insère un chunk vectorisé dans la knowledge base.
    Retourne l'UUID du chunk créé.
    """
    with get_connection() as conn:
        row = execute_with_retry(
            conn,
            """
            INSERT INTO knowledge_chunks
                (product_id, source, section, chunk_index, chunk_text, embedding, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
            RETURNING id
            """,
            (
                product_id,
                source,
                section,
                chunk_index,
                chunk_text,
                str(embedding),
                Json(metadata or {}),
            ),
            fetch="one",
        )
        return str(row["id"])


def delete_chunks_for_product(product_id: str) -> int:
    """
    Supprime tous les chunks d'un produit.
    Utilisé avant réingestion complète d'un document.
    """
    with get_connection() as conn:
        rc = execute_with_retry(
            conn,
            "DELETE FROM knowledge_chunks WHERE product_id = %s",
            (product_id,),
        )
        return rc or 0


def search_chunks(
    query_embedding: list[float],
    *,
    product_id: str | None = None,
    top_k: int = 5,
    min_score: float = 0.70,
) -> list[dict]:
    """
    Recherche vectorielle cosine dans la knowledge base.
    Si product_id est fourni, la recherche est filtrée sur ce produit.
    Retourne les chunks les plus pertinents avec leur score de similarité.
    """
    embedding_str = str(query_embedding)
    if product_id:
        sql = """
            SELECT
                id,
                product_id,
                section,
                chunk_text,
                metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM knowledge_chunks
            WHERE product_id = %s
              AND embedding IS NOT NULL
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY score DESC
            LIMIT %s
        """
        params = (embedding_str, product_id, embedding_str, min_score, top_k)
    else:
        sql = """
            SELECT
                id,
                product_id,
                section,
                chunk_text,
                metadata,
                1 - (embedding <=> %s::vector) AS score
            FROM knowledge_chunks
            WHERE embedding IS NOT NULL
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY score DESC
            LIMIT %s
        """
        params = (embedding_str, embedding_str, min_score, top_k)

    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, params, fetch="all") or []
        return [dict(r) for r in rows]


def count_chunks_by_product() -> list[dict]:
    """
    Retourne le nombre de chunks par produit.
    Utilisé par l'endpoint admin /admin/kb/stats.
    """
    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(
            conn,
            """
            SELECT product_id, COUNT(*) AS chunk_count, MAX(created_at) AS last_ingested
            FROM knowledge_chunks
            GROUP BY product_id
            ORDER BY product_id
            """,
            fetch="all",
        ) or []
        return [dict(r) for r in rows]
    
def get_or_set_lid(conv_id: str, phone_raw: str, resolver_fn) -> str:
    """
    Retourne le LID depuis la DB si déjà résolu,
    sinon appelle resolver_fn(phone_raw), sauvegarde et retourne.
    """
    with get_connection(readonly=True) as conn:
        row = execute_with_retry(
            conn,
            "SELECT lid FROM conversations WHERE id = %s LIMIT 1",
            (conv_id,),
            fetch="one",
        )
        logger.info("LID en cache : %s", row)
        if row and row.get("lid"):
            return row["lid"]

    # Pas encore résolu — appeler CheckWhatsapp
    lid = resolver_fn(phone_raw)
    if lid:
        logger.info("Sauvegarde LID — conv_id=%s | lid=%s", conv_id, lid)
        with get_connection() as conn:
            execute_with_retry(
                conn,
                "UPDATE conversations SET lid = %s WHERE id = %s",
                (lid, conv_id),
            )
    return lid or phone_raw



def list_conversations_with_last_message(
    state: str | None = None,
    ai_active: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Liste les conversations avec le dernier message inclus —
    une seule requête SQL avec LEFT JOIN au lieu de N+1 requêtes.
    """
    sql = """
        SELECT
            c.*,
            m.content   AS last_message,
            m.role      AS last_message_role,
            m.timestamp AS last_message_time
        FROM conversations c
        LEFT JOIN LATERAL (
            SELECT content, role, timestamp
            FROM messages
            WHERE conversation_id = c.id
            ORDER BY timestamp DESC
            LIMIT 1
        ) m ON TRUE
        WHERE TRUE
    """
    params = []
    if state:
        sql += " AND c.state = %s"; params.append(state)
    if ai_active is not None:
        sql += " AND c.ai_active = %s"; params.append(ai_active)
    sql += " ORDER BY COALESCE(m.timestamp, c.updated_at) DESC LIMIT %s OFFSET %s"
    params += [limit, offset]

    with get_connection(readonly=True) as conn:
        rows = execute_with_retry(conn, sql, params, fetch="all") or []
        return [dict(r) for r in rows]

# ══════════════════════════════════════════════════════════════════════════════
# INIT — point d'entrée appelé au démarrage de l'app
# ══════════════════════════════════════════════════════════════════════════════

class ConvDatabase:
    """
    Équivalent de Database() dans database_pg.py.
    Instancier une fois dans create_app() pour initialiser les schémas v2.
    """
    def __init__(self):
        ensure_conv_schemas()
        logger.info("ConvDatabase initialisé.")