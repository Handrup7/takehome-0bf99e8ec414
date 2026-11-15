"""Flujo de negocio del mensaje del huésped (POST /api/messages).

Encadena los módulos anteriores en el orden del enunciado:

    identidad -> deduplicación -> conversación -> IA -> validación
    -> política -> disponibilidad (servicio de reservas) -> propuesta

Decisiones documentadas (DECISIONES.md):
- El historial de la conversación se reenvía a la IA como pares
  user/assistant, con el mensaje nuevo como último user: el servicio
  de IA no guarda contexto (llm-api.md), así que la conversación
  vive en nuestra base de datos.
- La IA nunca actúa: su salida se valida (llm/interpretation.py) y
  solo se usan los tres campos permitidos; los campos extra
  (approved, role, fee, ...) se descartan y no dan permisos ni precio.
- El precio y la exigencia de aprobación salen SOLO de los documentos
  aprobados (policies.py). La disponibilidad la decide el servicio de
  reservas, nunca la IA.
- La operación de cambio lleva una operation_key estable derivada de
  la reserva y la hora (p. ej. op-STAY-A1-1400): sobrevive reinicios y
  hace idempotente la escritura en el servicio remoto.
- Si ya existe una propuesta activa para la misma reserva y hora, no
  se crea una segunda: se reutiliza (evita duplicados y dobles cargos).
- El deduplicado por (actor, conversación, event_id) se compara con el
  contenido: idéntico => replay; distinto => 409.
"""

import sqlite3

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .auth import Identity, resolve_identity
from .config import settings
from .llm.client import LlmClientError, RemoteLlmClient
from .llm.interpretation import LlmValidationError, validate_interpretation
from .policies import NoActivePolicy, resolve_policy
from .reservations import ReservationClientError, ReservationsClient, full_checkout_at
from .store import Store, new_id, store as local_store

router = APIRouter()

# Instancias compartidas por la app. En los tests se sustituyen por stubs
# con la misma interfaz (interpret / get_stay / check_availability).
llm_client = RemoteLlmClient()
reservations_client = ReservationsClient()

# Estados de propuesta que todavía pueden ejecutarse: mientras una
# propuesta esté en alguno de ellos, no se crea otra para la misma
# reserva y hora.
_ACTIVE_PROPOSAL_STATUSES = (
    "awaiting_approval",
    "processing",
    "pending_verification",
)

# Respuestas al huésped, por idioma. {time}/{fee} se rellenan según el caso.
_REPLIES = {
    "ask_time": {
        "es": "¿A qué hora te gustaría hacer la salida?",
        "en": "What time would you like to check out?",
    },
    "already_at": {
        "es": "Tu salida ya está prevista a las {time}; no hace falta ningún cambio.",
        "en": "Your checkout is already at {time}; no change is needed.",
    },
    "awaiting": {
        "es": "La solicitud de salida a las {time}, con importe de {fee} EUR, está pendiente de aprobación.",
        "en": "Your late checkout request for {time}, for a fee of {fee} EUR, is pending approval.",
    },
    "already_requested": {
        "es": "Ya hay una solicitud de salida a las {time}, con importe de {fee} EUR, pendiente de aprobación.",
        "en": "There is already a pending late checkout request for {time} (fee {fee} EUR).",
    },
    "unavailable": {
        "es": "No hay disponibilidad para la salida a las {time}. Prueba con otra hora o contacta directamente con el hotel.",
        "en": "There is no availability for a checkout at {time}. Try another time or contact the hotel directly.",
    },
    "handoff": {
        "es": "No he podido procesar tu solicitud de forma automática. Un responsable del hotel la revisará; no se ha aplicado ningún cambio.",
        "en": "I could not process your request automatically. A manager will review it; no change has been applied.",
    },
}


def _error(code: str, message: str, status: int) -> HTTPException:
    """Error en el formato exigido por el contrato de la app."""
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


def _reply(language: str, key: str, **kwargs) -> str:
    """Texto de respuesta en el idioma detectado por la IA (es/en)."""
    lang = language if language in ("es", "en") else "es"
    return _REPLIES[key][lang].format(**kwargs)


async def _parse_body(request: Request) -> dict:
    """Valida el cuerpo de POST /api/messages.

    El contrato exige 422 para campos no contemplados o en falta, y no
    admite roles ni aprobaciones en el payload: solo event_id,
    conversation_id, stay_id y text, todos textos no vacíos.

    Nota: en Starlette, request.json() (y también request.body()) es
    ASÍNCRONA: fuera de un contexto async devuelve un coroutine vacío.
    Por eso el endpoint es async def y se hace await del cuerpo aquí.
    """
    try:
        raw = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise _error("invalid_request", "El cuerpo debe ser un objeto JSON.", 422)
    if not isinstance(raw, dict):
        raise _error("invalid_request", "El cuerpo debe ser un objeto JSON.", 422)

    expected = {"event_id", "conversation_id", "stay_id", "text"}
    keys = set(raw)
    if keys != expected:
        extra = sorted(keys - expected)
        missing = sorted(expected - keys)
        detail = []
        if extra:
            detail.append(f"campos no permitidos: {', '.join(extra)}")
        if missing:
            detail.append(f"faltan campos: {', '.join(missing)}")
        raise _error(
            "invalid_request",
            "El cuerpo debe contener exactamente event_id, conversation_id, "
            f"stay_id y text ({'; '.join(detail)}).",
            422,
        )

    for field in ("event_id", "conversation_id", "stay_id", "text"):
        value = raw[field]
        if not isinstance(value, str) or not value.strip():
            raise _error(
                "invalid_request", f"El campo {field} debe ser un texto no vacío.", 422
            )
    if len(raw["text"]) > 8000:
        raise _error(
            "invalid_request", "El texto no puede superar los 8000 caracteres.", 422
        )
    return raw


def process_message(
    identity: Identity,
    payload: dict,
    *,
    store: Store = local_store,
    llm: RemoteLlmClient | None = None,
    reservations: ReservationsClient | None = None,
) -> dict:
    """Procesa el mensaje de un huésped y devuelve el cuerpo de la respuesta.

    Lanza HTTPException (401/403/409/422/503) cuando el mensaje no puede
    procesarse. Nunca lanza 500 por una salida defectuosa de la IA: los
    fallos del modelo se convierten en estados de negocio (needs_input,
    handoff) o en errores explicados.
    """
    llm = llm or llm_client
    reservations = reservations or reservations_client

    conversation_id = payload["conversation_id"]
    event_id = payload["event_id"]
    text = payload["text"]
    stay_id = payload["stay_id"]  # ya validada contra identity en la ruta

    trace_id = new_id("trace")
    store.begin_trace(trace_id)
    store.add_trace_event(
        trace_id, "identity",
        {"token": identity.token, "role": identity.role, "hotel_id": identity.hotel_id},
    )

    # -------------------------------------------------- 1) deduplicación
    # Clave: (actor, conversación, event_id). Si ya existe, se compara el
    # contenido: idéntico => replay; distinto => 409.
    previous = store.find_message_event(identity.token, conversation_id, event_id)
    if previous is not None:
        if previous["stay_id"] == stay_id and previous["text"] == text:
            store.add_trace_event(trace_id, "replayed", {"event_id": event_id})
            return {
                "event_id": event_id,
                "conversation_id": conversation_id,
                "status": previous["status"],
                "message": previous["response_message"],
                "proposal_id": previous["proposal_id"],
                "trace_id": previous["trace_id"],
                "replayed": True,
            }
        store.add_trace_event(trace_id, "event_conflict", {"event_id": event_id})
        raise _error(
            "event_conflict",
            "Ese event_id ya se usó con otro contenido en esta conversación.",
            409,
        )

    # ------------------------------------------ 2) conversación: crear o validar
    # La conversación queda ligada a su propietario, hotel y reserva al
    # crearse; después no puede usarse por nadie más.
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        store.create_conversation(conversation_id, identity.token, identity.hotel_id, stay_id)
        store.add_trace_event(trace_id, "conversation_created", {"conversation_id": conversation_id})
    else:
        if conversation["actor_token"] != identity.token:
            store.add_trace_event(trace_id, "foreign_conversation", {"conversation_id": conversation_id})
            raise _error("forbidden", "Esa conversación no es tuya.", 403)
        store.add_trace_event(trace_id, "conversation_reused", {"conversation_id": conversation_id})

    # ------------------------------------------- 3) llamada a la IA (con contexto)
    messages = _build_llm_messages(store, conversation_id, text)
    store.add_trace_event(trace_id, "llm_request", {"messages": len(messages)})
    try:
        llm_response = llm.interpret(messages)
    except LlmClientError as err:
        store.add_llm_call(
            trace_id, None, settings.llm_scenario, "failed", None, None, False, err.code
        )
        store.add_trace_event(trace_id, "llm_failed", {"code": err.code})
        raise _error(
            "llm_unavailable",
            "El servicio de interpretación no está disponible ahora; no se ha "
            "aplicado ningún cambio. Inténtalo de nuevo en unos segundos.",
            503,
        )

    # La IA respondió: se valida su salida. Un fallo de validación no es
    # un fallo del sistema: se registra y se convierte en estado de negocio.
    try:
        interpretation = validate_interpretation(llm_response.content)
        validated, validation_code = True, None
    except LlmValidationError as err:
        interpretation, validated, validation_code = None, False, err.code

    store.add_llm_call(
        trace_id,
        llm_response.request_id,
        settings.llm_scenario,
        "ok",
        llm_response.duration_ms,
        llm_response.usage,
        validated,
        validation_code,
    )
    store.add_trace_event(
        trace_id, "llm_validated",
        {
            "validated": validated,
            "validation_code": validation_code,
            "interpretation": interpretation.as_dict() if interpretation else None,
        },
    )

    if interpretation is None:
        # La IA no devolvió una interpretación usable: no se actúa.
        if validation_code == "invalid_time":
            # La hora devuelta no existe (p. ej. 29:90): no se usa, se pregunta de nuevo.
            status, reply = "needs_input", _reply("es", "ask_time")
        else:
            # JSON roto, intención no reconocida o idioma no admitido: escalar.
            status, reply = "handoff", _reply("es", "handoff")
        store.add_trace_event(trace_id, "output_rejected", {"code": validation_code})
        return _save_and_return(
            store, trace_id, identity, conversation_id, event_id, stay_id,
            text, status, reply, None,
        )

    language = interpretation.language

    # --------------------------------------- 4) ¿faltan datos? -> preguntar
    if interpretation.requested_time is None:
        reply = _reply(language, "ask_time")
        store.add_trace_event(trace_id, "needs_time", {})
        return _save_and_return(
            store, trace_id, identity, conversation_id, event_id, stay_id,
            text, "needs_input", reply, None,
        )

    time = interpretation.requested_time

    # ----------------------------------------- 5) política aplicable (documento)
    try:
        resolution = resolve_policy(identity.hotel_id)
    except NoActivePolicy as err:
        store.add_trace_event(trace_id, "policy_missing", {"message": str(err)})
        return _save_and_return(
            store, trace_id, identity, conversation_id, event_id, stay_id,
            text, "handoff", _reply(language, "handoff"), None,
        )

    if resolution.status == "conflict":
        store.add_trace_event(
            trace_id, "policy_conflict",
            {"document_id": resolution.document["document_id"]},
        )
        return _save_and_return(
            store, trace_id, identity, conversation_id, event_id, stay_id,
            text, "handoff", _reply(language, "handoff"), None,
        )

    store.add_trace_event(
        trace_id, "policy_resolved",
        {
            "document_id": resolution.document["document_id"],
            "version": resolution.document["version"],
            "fee_eur": resolution.policy["fee_eur"],
        },
    )

    # ----------------------------------- 6) estancia y disponibilidad (servicio)
    # La sesión demo elige la clave remota; el huésped nunca la ve.
    session = identity.token
    try:
        stay = reservations.get_stay(session, stay_id)
    except ReservationClientError as err:
        store.add_trace_event(trace_id, "stay_failed", {"code": err.code})
        raise _error(
            "reservations_unavailable",
            "No se pudo consultar tu reserva ahora; no se ha aplicado ningún "
            "cambio. Inténtalo de nuevo en unos segundos.",
            503,
        )
    store.add_trace_event(
        trace_id, "stay",
        {"checkout_at": stay["checkout_at"], "version": stay["version"]},
    )

    requested_at = full_checkout_at(time)
    try:
        availability = reservations.check_availability(session, stay_id, requested_at)
    except ReservationClientError as err:
        store.add_trace_event(trace_id, "availability_failed", {"code": err.code})
        raise _error(
            "reservations_unavailable",
            "No se pudo comprobar la disponibilidad ahora; no se ha aplicado "
            "ningún cambio. Inténtalo de nuevo en unos segundos.",
            503,
        )
    store.add_trace_event(
        trace_id, "availability",
        {
            "available": availability.get("available"),
            "reason": availability.get("reason"),
            "stay_version": availability.get("stay_version"),
        },
    )

    # ------------------------------- 7) decisión según disponibilidad
    if availability.get("available") is not True:
        reason = availability.get("reason")
        if reason == "not_later_than_current_checkout":
            # Pedir la hora actual (o anterior) es información, no una solicitud.
            current_time = stay["checkout_at"][11:16]
            reply = _reply(language, "already_at", time=current_time)
            status = "information"
        else:
            # after_max_checkout / cleaning_conflict: no se puede hacer.
            reply = _reply(language, "unavailable", time=time)
            status = "declined"
        return _save_and_return(
            store, trace_id, identity, conversation_id, event_id, stay_id,
            text, status, reply, None,
        )

    # ---------------------------------------- 8) propuesta: reutilizar o crear
    existing = _find_active_proposal(store, identity.hotel_id, stay_id, time)
    if existing is not None:
        store.add_trace_event(
            trace_id, "proposal_reused",
            {"proposal_id": existing["id"], "operation_key": existing["operation_key"]},
        )
        reply = _reply(language, "already_requested", time=time, fee=existing["fee_eur"])
        proposal_id = existing["id"]
    else:
        operation_key = _next_operation_key(store, identity.hotel_id, stay_id, time)
        fee = resolution.policy["fee_eur"]
        proposal = store.add_proposal(
            conversation_id=conversation_id,
            actor_token=identity.token,
            org_id=identity.org_id,
            hotel_id=identity.hotel_id,
            stay_id=stay_id,
            requested_checkout_at=requested_at,
            current_checkout_at=stay["checkout_at"],
            stay_version=stay["version"],
            fee_eur=fee,
            policy_document_id=resolution.document["document_id"],
            policy_version=resolution.document["version"],
            evidence=resolution.evidence(),
            operation_key=operation_key,
            status="awaiting_approval",
        )
        store.add_trace_event(
            trace_id, "proposal_created",
            {"proposal_id": proposal["id"], "operation_key": operation_key, "fee_eur": fee},
        )
        reply = _reply(language, "awaiting", time=time, fee=fee)
        proposal_id = proposal["id"]

    return _save_and_return(
        store, trace_id, identity, conversation_id, event_id, stay_id,
        text, "awaiting_approval", reply, proposal_id,
    )


def _save_and_return(
    store: Store,
    trace_id: str,
    identity: Identity,
    conversation_id: str,
    event_id: str,
    stay_id: str,
    text: str,
    status: str,
    reply: str,
    proposal_id: str | None,
) -> dict:
    """Guarda el mensaje con su resultado y devuelve la respuesta al huésped.

    Guardar el resultado (status, mensaje, propuesta) es lo que permite
    repetir un evento idéntico más tarde (replay) sin volver a llamar a
    la IA ni al servicio de reservas.
    """
    store.add_trace_event(
        trace_id, "message_saved", {"status": status, "proposal_id": proposal_id}
    )
    try:
        store.save_message(
            conversation_id, event_id, identity.token, stay_id, text,
            status, reply, proposal_id, trace_id,
        )
    except sqlite3.IntegrityError:
        # Dos peticiones concurrentes con el mismo event_id: gana la primera.
        raise _error(
            "event_conflict",
            "Ese event_id ya se procesó en esta conversación.",
            409,
        )
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "status": status,
        "message": reply,
        "proposal_id": proposal_id,
        "trace_id": trace_id,
        "replayed": False,
    }


# --------------------------------------------------------------------- helpers
def _build_llm_messages(store: Store, conversation_id: str, text: str) -> list[dict]:
    """Historial de la conversación para la IA: pares user/assistant, con el
    mensaje nuevo como último user.

    Límites del contrato (llm-api.md): 1-20 mensajes, 1-8 user, 8000
    caracteres por contenido y 16.000 en total. Se conserva lo más
    reciente y se descarta lo más antiguo si se supera el límite.
    """
    messages: list[dict] = [{"role": "user", "content": text}]
    user_count = 1
    total_chars = len(text)

    for msg in reversed(store.list_messages(conversation_id)):
        user_content = msg["text"]
        assistant_content = msg["response_message"]
        added = len(user_content) + len(assistant_content)
        if (
            len(messages) + 2 > 20
            or user_count + 1 > 8
            or total_chars + added > 16000
        ):
            break
        messages = [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ] + messages
        user_count += 1
        total_chars += added
    return messages


def _base_operation_key(stay_id: str, time: str) -> str:
    """Clave base estable de la operación: op-STAY-A1-1400.

    Derivada de datos de negocio (reserva + hora), no de un ID aleatorio:
    sobrevive reinicios y reintentos, y el servicio remoto la usa para
    devolver el mismo recibo al repetir exactamente la operación.
    """
    return f"op-{stay_id}-{time.replace(':', '')}"


def _find_active_proposal(
    store: Store, hotel_id: str, stay_id: str, time: str
) -> dict | None:
    """Propuesta todavía ejecutable para la misma reserva y hora."""
    wanted = full_checkout_at(time)
    for proposal in store.list_proposals(hotel_id):
        if (
            proposal["stay_id"] == stay_id
            and proposal["requested_checkout_at"] == wanted
            and proposal["status"] in _ACTIVE_PROPOSAL_STATUSES
        ):
            return proposal
    return None


def _next_operation_key(store: Store, hotel_id: str, stay_id: str, time: str) -> str:
    """Clave de operación libre para esta reserva y hora.

    Si la clave base ya la usó una propuesta anterior (p. ej. rechazada),
    se añade un sufijo -2, -3, ... para no chocar con la restricción UNIQUE.
    """
    base = _base_operation_key(stay_id, time)
    used = {p["operation_key"] for p in store.list_proposals(hotel_id)}
    key, suffix = base, 1
    while key in used:
        suffix += 1
        key = f"{base}-{suffix}"
    return key


# ----------------------------------------------------------------------- ruta
@router.post("/api/messages")
async def send_message(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict:
    identity = resolve_identity(authorization)
    if identity.role != "guest":
        raise _error(
            "forbidden", "Solo los huéspedes pueden enviar mensajes a su reserva.", 403
        )
    body = await _parse_body(request)
    # La reserva la decide el token, no el payload: si no coincide, 403.
    if body["stay_id"] != identity.stay_id:
        raise _error("forbidden", "La reserva indicada no es la tuya.", 403)
    # process_message bloquea (SQLite local + httpx síncrono): se ejecuta
    # en el thread pool para no bloquear el event loop de la app.
    return await run_in_threadpool(process_message, identity, body)
