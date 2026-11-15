"""Gestión de propuestas por el responsable (app-api.md, sección «Propuestas»).

- GET  /api/proposals                  -> solo las de su hotel.
- POST /api/proposals/{id}/approve     -> 200/202/409 según el resultado.
- POST /api/proposals/{id}/reject      -> 200 (idempotente) o 409.
- POST /api/proposals/{id}/reconcile   -> consulta la operación estable;
                                          nunca crea una operación nueva.

Reglas del contrato que gobiernan este módulo:

- La aprobación cubre estancia+hora+fee+versión de la política y se
  invalida si cambió: se RE-COMPROBAN estancia y disponibilidad justo
  antes del `POST /changes`.
- Tras una escritura incierta, un 404 o un 503 en la consulta de la
  operación NO bastan para declarar fracaso: se conserva
  `pending_verification` y se devuelve 202. Nunca se hace otra escritura
  ni se genera otra clave.
- Un recibo válido y CONCORDANTE (misma estancia, hora y clave) marca la
  propuesta como `completed`; el replay de una aprobación completada
  responde 200 con el mismo recibo.
- Token huésped: 403. Propuesta de otro hotel: 403 (o 404), sin modificar
  la propuesta ni consumir su aprobación.
"""

from fastapi import APIRouter, Header, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from .auth import Identity, resolve_identity
from .reservations import ReservationClientError, ReservationsClient
from .store import Store, store as local_store

router = APIRouter()

# Instancia compartida; en tests se sustituye por un stub con la misma
# interfaz (get_stay / check_availability / apply_change / get_operation).
reservations_client = ReservationsClient()

# Estados en los que YA se intentó una escritura y el resultado puede ser
# incierto: solo se resuelven consultando la operación, nunca reescribiendo.
_WRITE_IN_FLIGHT = ("processing", "pending_verification")


def _error(code: str, message: str, status: int) -> HTTPException:
    """Error en el formato exigido por el contrato de la app."""
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


def _manager(authorization: str | None) -> Identity:
    """401 sin token; 403 si el rol no es responsable."""
    identity = resolve_identity(authorization)
    if identity.role != "manager":
        raise _error(
            "forbidden", "Solo el responsable del hotel gestiona propuestas.", 403
        )
    return identity


def _proposal_for(store: Store, identity: Identity, proposal_id: str) -> dict:
    """Localiza la propuesta y verifica que es de SU hotel.

    404 si no existe; 403 si es de otro hotel (sin revelar contenido).
    """
    proposal = store.get_proposal(proposal_id)
    if proposal is None:
        raise _error("not_found", "No existe la propuesta indicada.", 404)
    if proposal["hotel_id"] != identity.hotel_id:
        raise _error(
            "forbidden", "Esa propuesta no pertenece a tu hotel.", 403
        )
    return proposal


def _view(p: dict) -> dict:
    """Vista pública de la propuesta (contrato app-api.md).

    Incluye `stay_version` y `hotel_id`: campos de versión/estado
    permitidos para revalidación. `receipt` sale `null` si no está comprobada.
    """
    return {
        "id": p["id"],
        "hotel_id": p["hotel_id"],
        "stay_id": p["stay_id"],
        "status": p["status"],
        "current_checkout_at": p["current_checkout_at"],
        "requested_checkout_at": p["requested_checkout_at"],
        "fee_eur": p["fee_eur"],
        "policy_document_id": p["policy_document_id"],
        "policy_version": p["policy_version"],
        "evidence": p["evidence"],
        "operation_key": p["operation_key"],
        "receipt": p["receipt"],
        "stay_version": p["stay_version"],
    }


def _receipt_matches(proposal: dict, receipt: dict) -> bool:
    """El recibo debe concordar con la propuesta (mock-api.md):
    misma estancia, misma hora solicitada, misma clave y la versión
    resultante (la previa incrementada en 1)."""
    return (
        receipt.get("stay_id") == proposal["stay_id"]
        and receipt.get("checkout_at") == proposal["requested_checkout_at"]
        and receipt.get("operation_key") == proposal["operation_key"]
        and receipt.get("version") == proposal["stay_version"] + 1
    )


# --------------------------------------------------------------------- approve
def _approve(
    store: Store,
    reservations: ReservationsClient,
    identity: Identity,
    proposal_id: str,
) -> tuple[int, dict]:
    """Aprueba una propuesta: revalida y escribe, o reconcilia si ya se
    intentó. Devuelve (código HTTP, cuerpo)."""
    proposal = _proposal_for(store, identity, proposal_id)
    pid = proposal["id"]
    status = proposal["status"]

    if status == "completed":
        # Replay de una aprobación ya completada: mismo recibo, sin escritura.
        return 200, {
            "proposal_id": pid,
            "status": "completed",
            "message": "La propuesta ya está completada; el cambio fue aplicado.",
            "receipt": proposal["receipt"],
        }
    if status == "rejected":
        return 409, {
            "proposal_id": pid,
            "status": "rejected",
            "message": "La propuesta está rechazada y no puede aprobarse.",
            "receipt": None,
        }
    if status in ("stale", "failed"):
        return 409, {
            "proposal_id": pid,
            "status": status,
            "message": "La propuesta ya no es ejecutable.",
            "receipt": None,
        }
    if status in _WRITE_IN_FLIGHT:
        # Ya se intentó una escritura: se verifica, NUNCA se reescribe.
        return _reconcile(store, reservations, identity, proposal)

    # status == "awaiting_approval": revalidar y ejecutar.
    session = identity.token

    # 1) Re-comprobar la estancia (¿cambió la versión?).
    try:
        stay = reservations.get_stay(session, proposal["stay_id"])
    except ReservationClientError:
        raise _error(
            "reservations_unavailable",
            "No se pudo comprobar la estancia ahora; la propuesta no cambió. "
            "Inténtalo de nuevo en unos segundos.",
            503,
        )
    if stay["version"] != proposal["stay_version"]:
        store.set_proposal_status(pid, "stale")
        return 409, {
            "proposal_id": pid,
            "status": "stale",
            "message": "La estancia cambió desde la solicitud; la propuesta quedó obsoleta.",
            "receipt": None,
        }

    # 2) Re-comprobar la disponibilidad (stale-availability).
    try:
        availability = reservations.check_availability(
            session, proposal["stay_id"], proposal["requested_checkout_at"]
        )
    except ReservationClientError:
        raise _error(
            "reservations_unavailable",
            "No se pudo comprobar la disponibilidad ahora; la propuesta no "
            "cambió. Inténtalo de nuevo en unos segundos.",
            503,
        )
    if availability.get("available") is not True:
        store.set_proposal_status(pid, "stale")
        return 409, {
            "proposal_id": pid,
            "status": "stale",
            "message": "La hora solicitada ya no está disponible.",
            "receipt": None,
        }

    # 3) Ejecutar: `processing` y luego la escritura con la clave estable.
    store.set_proposal_status(pid, "processing")
    try:
        receipt = reservations.apply_change(
            session,
            proposal["stay_id"],
            proposal["requested_checkout_at"],
            expected_version=stay["version"],
            operation_key=proposal["operation_key"],
        )
    except ReservationClientError as err:
        if err.ambiguous:
            # Escritura incierta: no se sabe si se aplicó.
            store.set_proposal_status(pid, "pending_verification")
            return 202, {
                "proposal_id": pid,
                "status": "pending_verification",
                "message": "No se pudo confirmar el cambio; el resultado de la "
                           "escritura es incierto. Verifícalo con reconcile.",
                "receipt": None,
            }
        if err.retryable:
            # 503 simulated_rejection agotando reintentos: garantiza que NO
            # escribió. La propuesta sigue aprobable.
            store.set_proposal_status(pid, "awaiting_approval")
            raise _error(
                "reservations_unavailable",
                "El servicio de reservas no aceptó el cambio ahora. La "
                "propuesta sigue pendiente; reinténtalo.",
                503,
            )
        # Determinista (409/404/403/401/422): el servicio no escribió.
        store.set_proposal_status(pid, "stale" if err.status == 409 else "failed")
        return 409, {
            "proposal_id": pid,
            "status": "stale" if err.status == 409 else "failed",
            "message": f"El servicio de reservas rechazó el cambio ({err.code}).",
            "receipt": None,
        }

    if not _receipt_matches(proposal, receipt):
        store.set_proposal_status(pid, "failed")
        return 409, {
            "proposal_id": pid,
            "status": "failed",
            "message": "El recibo recibido no concuerda con la propuesta.",
            "receipt": None,
        }

    store.set_proposal_receipt(pid, receipt)
    store.set_proposal_status(pid, "completed")
    return 200, {
        "proposal_id": pid,
        "status": "completed",
        "message": "Cambio aplicado correctamente.",
        "receipt": receipt,
    }


# ------------------------------------------------------------------ reconcile
def _reconcile(
    store: Store,
    reservations: ReservationsClient,
    identity: Identity,
    proposal: dict,
) -> tuple[int, dict]:
    """Consulta la operación estable con `GET /operations/{key}`.

    200 si queda completada (recibo válido y concordante) o ya estaba
    cerrada; 202 si sigue incierta. Nunca crea una operación nueva ni
    genera otra clave: un 404 o 503 no es prueba de fallo definitivo.
    """
    pid = proposal["id"]
    try:
        operation = reservations.get_operation(
            identity.token, proposal["operation_key"]
        )
    except ReservationClientError:
        store.set_proposal_status(pid, "pending_verification")
        return 202, {
            "proposal_id": pid,
            "status": "pending_verification",
            "message": "No se pudo confirmar la operación ahora; la propuesta "
                       "sigue pendiente de verificación. Inténtalo de nuevo.",
            "receipt": None,
        }

    receipt = operation.get("receipt")
    if (
        operation.get("status") == "applied"
        and receipt is not None
        and _receipt_matches(proposal, receipt)
    ):
        store.set_proposal_receipt(pid, receipt)
        store.set_proposal_status(pid, "completed")
        return 200, {
            "proposal_id": pid,
            "status": "completed",
            "message": "Operación confirmada: el cambio fue aplicado.",
            "receipt": receipt,
        }

    # Operación no encontrada o en un estado que no permite confirmar:
    # conservar la incertidumbre, sin declarar fallo ni reescribir.
    store.set_proposal_status(pid, "pending_verification")
    return 202, {
        "proposal_id": pid,
        "status": "pending_verification",
        "message": "La operación aún no se puede confirmar; la propuesta sigue "
                   "pendiente de verificación. Inténtalo de nuevo.",
        "receipt": None,
    }


# --------------------------------------------------------------------- reject
def _reject(
    store: Store,
    identity: Identity,
    proposal_id: str,
) -> tuple[int, dict]:
    """Rechaza una propuesta. 200 si queda rechazada o ya lo estaba; 409 si
    se empezó a ejecutar o ya está completada."""
    proposal = _proposal_for(store, identity, proposal_id)
    pid = proposal["id"]
    status = proposal["status"]

    if status == "rejected":
        return 200, {
            "proposal_id": pid,
            "status": "rejected",
            "message": "La propuesta ya estaba rechazada.",
            "receipt": None,
        }
    if status == "completed":
        return 409, {
            "proposal_id": pid,
            "status": "completed",
            "message": "La propuesta está completada y no puede rechazarse.",
            "receipt": None,
        }
    if status in _WRITE_IN_FLIGHT:
        return 409, {
            "proposal_id": pid,
            "status": status,
            "message": "Ya se intentó ejecutar el cambio; resuélvelo primero "
                       "con reconcile.",
            "receipt": None,
        }
    # awaiting_approval, stale o failed: no llegó a ejecutarse.
    store.set_proposal_status(pid, "rejected")
    return 200, {
        "proposal_id": pid,
        "status": "rejected",
        "message": "Propuesta rechazada; no se ha aplicado ningún cambio.",
        "receipt": None,
    }


# ------------------------------------------------------------------- reconcile
def _reconcile_endpoint(
    store: Store,
    reservations: ReservationsClient,
    identity: Identity,
    proposal_id: str,
) -> tuple[int, dict]:
    """`POST /reconcile`: solo tiene efecto si hay una escritura incierta.

    Completada o rechazada: devuelve su estado sin ejecutar nada. Sin
    escritura incierta: 200 «sin cambio». Nunca crea una operación nueva.
    """
    proposal = _proposal_for(store, identity, proposal_id)
    pid = proposal["id"]
    status = proposal["status"]

    if status in _WRITE_IN_FLIGHT:
        return _reconcile(store, reservations, identity, proposal)
    return 200, {
        "proposal_id": pid,
        "status": status,
        "message": "No hay ninguna escritura incierta en esta propuesta; "
                   "su estado no cambia.",
        "receipt": proposal["receipt"],
    }


# ---------------------------------------------------------------------- rutas
@router.get("/api/proposals")
def list_proposals(authorization: str | None = Header(default=None)) -> dict:
    """Propuestas del hotel del responsable, con sus campos de revalidación."""
    identity = _manager(authorization)
    proposals = local_store.list_proposals(identity.hotel_id)
    return {"proposals": [_view(p) for p in proposals]}


@router.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    identity = _manager(authorization)
    http_status, body = await run_in_threadpool(
        _approve, local_store, reservations_client, identity, proposal_id
    )
    response.status_code = http_status
    return body


@router.post("/api/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    identity = _manager(authorization)
    http_status, body = await run_in_threadpool(
        _reject, local_store, identity, proposal_id
    )
    response.status_code = http_status
    return body


@router.post("/api/proposals/{proposal_id}/reconcile")
async def reconcile_proposal(
    proposal_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    identity = _manager(authorization)
    http_status, body = await run_in_threadpool(
        _reconcile_endpoint, local_store, reservations_client, identity, proposal_id
    )
    response.status_code = http_status
    return body
