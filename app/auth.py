"""Identidad de demostración: token de sesión -> rol, hotel y reserva.

Estas son sesiones fijas del kit (datos/identidades.json), no un sistema de
login real: el enunciado dice explícitamente "Usa las sesiones del kit; no
construyas un inicio de sesión real".

Reglas clave (app-api.md):
- Cada petición autenticada lleva "Authorization: Bearer <token>".
- Sin token o token desconocido: 401.
- El rol, la organización, el hotel y la reserva los decide SIEMPRE el
  servidor a partir del token. Nunca se aceptan en el cuerpo de la petición
  (un huésped no puede "ser" otra reserva escribiéndolo en el mensaje).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Identity:
    """Quién está pidiendo, y a qué alcance pertenece."""

    token: str
    role: str  # "guest" | "manager"
    org_id: str
    hotel_id: str
    stay_id: str | None  # los responsables no tienen reserva propia


def _error(code: str, message: str, status: int) -> HTTPException:
    """Error en el formato exigido por el contrato de la app."""
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message}},
    )


def _load_identities() -> dict[str, Identity]:
    identities: dict[str, Identity] = {}
    raw = json.loads((_DATA_DIR / "identidades.json").read_text(encoding="utf-8"))
    for item in raw["identities"]:
        identities[item["token"]] = Identity(
            token=item["token"],
            role=item["role"],
            org_id=item["org_id"],
            hotel_id=item["hotel_id"],
            stay_id=item["stay_id"],
        )
    return identities


# Se carga una vez al arrancar el módulo
_IDENTITIES = _load_identities()


def resolve_identity(authorization: str | None) -> Identity:
    """Convierte la cabecera Authorization en una Identity.

    Devuelve 401 si falta la cabecera o el token no es una sesión del kit.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise _error(
            "unauthorized",
            "Falta la cabecera Authorization: Bearer <token>.",
            401,
        )
    token = authorization.removeprefix("Bearer ").strip()
    identity = _IDENTITIES.get(token)
    if identity is None:
        # No revelamos qué tokens existen; solo que este no vale.
        raise _error("unauthorized", "Token de sesión no reconocido.", 401)
    return identity
