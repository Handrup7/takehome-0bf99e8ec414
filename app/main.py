"""Aplicación de la prueba técnica: salida tardía con IA.

Arranque:  python -m uvicorn app.main:app --reload
Documentación automática: http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth import Identity, resolve_identity
from .messages import router as messages_router

app = FastAPI(title="Late checkout")
app.include_router(messages_router)


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    """Todo error de la app sale en el formato del contrato:
    {"error": {"code": "...", "message": "..."}}
    """
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        payload = detail
    else:
        payload = {"error": {"code": "error", "message": str(detail)}}
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.get("/health")
def health():
    return {"status": "ok", "service": "late-checkout-app"}


@app.get("/api/me")
def me(authorization: str | None = Header(default=None)) -> dict:
    """Endpoint de demostración: muestra la identidad resuelta.

    (No es obligatorio por el contrato; se añade para poder probar
    la autenticación sin construir el resto de la app.)
    """
    identity: Identity = resolve_identity(authorization)
    return {
        "token": identity.token,
        "role": identity.role,
        "org_id": identity.org_id,
        "hotel_id": identity.hotel_id,
        "stay_id": identity.stay_id,
    }
