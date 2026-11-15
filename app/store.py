"""Persistencia local en SQLite.

Todo lo que la aplicación debe conservar tras un reinicio vive aquí:
conversaciones, mensajes (con su resultado para poder repetirlos),
propuestas (con su operation_key estable y su receipt), y el registro
de seguimiento (trazas y llamadas a la IA).

Decisiones documentadas (DECISIONES.md):
- SQLite por el tamaño del problema: un solo archivo, sin servidor,
  sobrevive reinicios. No hay justificación para un motor más pesado.
- SQL plano (sin ORM): a esta escala el SQL directo es más legible y
  más fácil de defender.
- Una conexión por operación: el volumen de datos es pequeño y así no
  hay que gestionar hilos ni cierres explícitos.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

# El archivo de la base de datos vive en la raíz del proyecto y está
# excluido de Git (.gitignore).
DB_PATH = Path(__file__).resolve().parent.parent / "late_checkout.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    actor_token TEXT NOT NULL,
    hotel_id    TEXT NOT NULL,
    stay_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL,
    event_id         TEXT NOT NULL,
    actor_token      TEXT NOT NULL,
    stay_id          TEXT NOT NULL,
    text             TEXT NOT NULL,
    status           TEXT NOT NULL,
    response_message TEXT NOT NULL,
    proposal_id      TEXT,
    trace_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE (actor_token, conversation_id, event_id)
);

CREATE TABLE IF NOT EXISTS proposals (
    id                    TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL,
    actor_token           TEXT NOT NULL,
    org_id                TEXT NOT NULL,
    hotel_id              TEXT NOT NULL,
    stay_id               TEXT NOT NULL,
    requested_checkout_at TEXT NOT NULL,
    current_checkout_at   TEXT NOT NULL,
    stay_version          INTEGER NOT NULL,
    fee_eur               INTEGER NOT NULL,
    policy_document_id    TEXT NOT NULL,
    policy_version        INTEGER NOT NULL,
    evidence              TEXT NOT NULL,
    operation_key         TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL,
    receipt               TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trace_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    event      TEXT NOT NULL,
    detail     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT NOT NULL,
    request_id      TEXT,
    scenario        TEXT,
    status          TEXT,
    duration_ms     INTEGER,
    usage           TEXT,
    validated       INTEGER,
    validation_code TEXT,
    created_at      TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    """Identificador único con prefijo legible, p. ej. 'prop-3fa8c1b2d4e5'."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class Store:
    """Acceso a la base de datos. Cada método abre y cierra su conexión."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------- helpers
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def reset(self) -> None:
        """Vacía todas las tablas (para tests y reinicio limpio)."""
        with self._conn() as conn:
            for table in ("messages", "proposals", "trace_events",
                          "traces", "llm_calls", "conversations"):
                conn.execute(f"DELETE FROM {table}")

    # ------------------------------------------------------- conversations
    def get_conversation(self, conversation_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_conversation(
        self, conversation_id: str, actor_token: str, hotel_id: str, stay_id: str
    ) -> dict:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations"
                " (id, actor_token, hotel_id, stay_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (conversation_id, actor_token, hotel_id, stay_id, _now()),
            )
        return self.get_conversation(conversation_id)

    # ------------------------------------------------------------- messages
    def find_message_event(
        self, actor_token: str, conversation_id: str, event_id: str
    ) -> dict | None:
        """Busca un evento ya procesado por este actor en esta conversación.

        La clave de deduplicación es (actor, conversation, event_id);
        el contenido (stay_id, text) se compara luego en la capa de
        negocio para distinguir replay idéntico de conflicto.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM messages"
                " WHERE actor_token = ? AND conversation_id = ? AND event_id = ?",
                (actor_token, conversation_id, event_id),
            ).fetchone()
        return dict(row) if row else None

    def save_message(
        self,
        conversation_id: str,
        event_id: str,
        actor_token: str,
        stay_id: str,
        text: str,
        status: str,
        response_message: str,
        proposal_id: str | None,
        trace_id: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages"
                " (conversation_id, event_id, actor_token, stay_id, text,"
                "  status, response_message, proposal_id, trace_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, event_id, actor_token, stay_id, text,
                 status, response_message, proposal_id, trace_id, _now()),
            )

    def list_messages(self, conversation_id: str) -> list[dict]:
        """Mensajes de una conversación, del más antiguo al más reciente."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- proposals
    def add_proposal(
        self,
        *,
        conversation_id: str,
        actor_token: str,
        org_id: str,
        hotel_id: str,
        stay_id: str,
        requested_checkout_at: str,
        current_checkout_at: str,
        stay_version: int,
        fee_eur: int,
        policy_document_id: str,
        policy_version: int,
        evidence: dict,
        operation_key: str,
        status: str,
    ) -> dict:
        proposal_id = new_id("prop")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO proposals"
                " (id, conversation_id, actor_token, org_id, hotel_id, stay_id,"
                "  requested_checkout_at, current_checkout_at, stay_version,"
                "  fee_eur, policy_document_id, policy_version, evidence,"
                "  operation_key, status, receipt, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (proposal_id, conversation_id, actor_token, org_id, hotel_id,
                 stay_id, requested_checkout_at, current_checkout_at,
                 stay_version, fee_eur, policy_document_id, policy_version,
                 json.dumps(evidence, ensure_ascii=False), operation_key,
                 status, _now(), _now()),
            )
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        return self._proposal_from_row(row) if row else None

    def list_proposals(self, hotel_id: str) -> list[dict]:
        """Propuestas de UN hotel (los responsables solo ven el suyo)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM proposals WHERE hotel_id = ? ORDER BY created_at",
                (hotel_id,),
            ).fetchall()
        return [self._proposal_from_row(r) for r in rows]

    def set_proposal_status(self, proposal_id: str, status: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), proposal_id),
            )

    def set_proposal_receipt(self, proposal_id: str, receipt: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET receipt = ?, updated_at = ? WHERE id = ?",
                (json.dumps(receipt, ensure_ascii=False), _now(), proposal_id),
            )

    @staticmethod
    def _proposal_from_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["evidence"] = json.loads(data["evidence"])
        data["receipt"] = json.loads(data["receipt"]) if data["receipt"] else None
        return data

    # ------------------------------------------------------------- tracking
    def begin_trace(self, trace_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO traces (trace_id, created_at)"
                " VALUES (?, ?)",
                (trace_id, _now()),
            )

    def add_trace_event(self, trace_id: str, event: str, detail: dict | str) -> None:
        """Añade un paso al registro de seguimiento (solo para el responsable)."""
        with self._conn() as conn:
            seq = conn.execute(
                "SELECT COUNT(*) FROM trace_events WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()[0] + 1
            conn.execute(
                "INSERT INTO trace_events"
                " (trace_id, seq, event, detail, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (trace_id, seq, event,
                 detail if isinstance(detail, str)
                 else json.dumps(detail, ensure_ascii=False),
                 _now()),
            )

    def get_trace(self, trace_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event, detail, created_at FROM trace_events"
                " WHERE trace_id = ? ORDER BY seq",
                (trace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_llm_call(
        self,
        trace_id: str,
        request_id: str | None,
        scenario: str,
        status: str,
        duration_ms: int | None,
        usage: dict | None,
        validated: bool,
        validation_code: str | None,
    ) -> None:
        """Registro de la llamada a la IA.

        usage se marca explícitamente como simulado: son estimaciones del
        mock, no coste real (contrato llm-api.md).
        """
        usage_json = None
        if usage is not None:
            usage_json = json.dumps({**usage, "simulated": True}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO llm_calls"
                " (trace_id, request_id, scenario, status, duration_ms, usage,"
                "  validated, validation_code, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trace_id, request_id, scenario, status, duration_ms, usage_json,
                 int(validated), validation_code, _now()),
            )


# Instancia compartida por la aplicación
store = Store()
