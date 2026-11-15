"""Historial de conversación (app-api.md, sección «Mensajes»).

`GET /api/conversations/{conversation_id}` devuelve historial y estado,
con el token del huésped propietario o del responsable de ese hotel.
Las trazas internas solo se devuelven al responsable.
"""

from fastapi import APIRouter, Header, HTTPException

from .auth import resolve_identity
from .store import store as local_store

router = APIRouter()


def _error(code: str, message: str, status: int) -> HTTPException:
    """Error en el formato exigido por el contrato de la app."""
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


def _message_view(m: dict) -> dict:
    """Un mensaje del historial, con su resultado guardado."""
    return {
        "event_id": m["event_id"],
        "text": m["text"],
        "status": m["status"],
        "message": m["response_message"],
        "proposal_id": m["proposal_id"],
        "trace_id": m["trace_id"],
        "created_at": m["created_at"],
    }


@router.get("/api/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    identity = resolve_identity(authorization)  # 401 si el token no vale
    conversation = local_store.get_conversation(conversation_id)
    if conversation is None:
        raise _error("not_found", "No existe la conversación indicada.", 404)

    is_owner = identity.token == conversation["actor_token"]
    is_hotel_manager = (
        identity.role == "manager"
        and identity.hotel_id == conversation["hotel_id"]
    )
    if not (is_owner or is_hotel_manager):
        # Ni el huésped de otra reserva ni el responsable de otro hotel:
        # 403 sin revelar contenido.
        raise _error("forbidden", "No puedes ver esta conversación.", 403)

    messages = [
        _message_view(m) for m in local_store.list_messages(conversation_id)
    ]
    last = messages[-1] if messages else None
    result = {
        "conversation_id": conversation_id,
        "hotel_id": conversation["hotel_id"],
        "stay_id": conversation["stay_id"],
        "messages": messages,
        "status": last["status"] if last else None,
        "proposal_id": last["proposal_id"] if last else None,
    }

    # Trazas internas: solo para el responsable de ese hotel.
    if identity.role == "manager":
        trace_ids = sorted({m["trace_id"] for m in messages})
        result["traces"] = {
            trace_id: local_store.get_trace(trace_id) for trace_id in trace_ids
        }
    return result
