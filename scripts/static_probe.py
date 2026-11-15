"""Probe de las vistas web (app/static) montadas en app/main.py.

Uso (dentro del venv, desde la raíz del proyecto):
    python scripts/static_probe.py

Recorrido:
  1. Las 4 páginas/estilos se sirven en la raíz con 200 y el contenido
     esperado (sin navegador: TestClient ASGI).
  2. Las rutas API siguen teniendo prioridad sobre el montaje estático:
     /health, /docs y /api/me responden JSON como siempre.
  3. Las vistas contienen lo que prometen: selector de sesiones, mapa
     token->reserva, llamada a /api/messages, botones approve/reject/
     reconcile y visor de conversaciones con trazas.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

client = TestClient(app)

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def get(path: str, token: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.get(path, headers=headers)
    try:
        body = r.json()
    except ValueError:
        body = None
    return {"status_code": r.status_code, "text": r.text, "body": body}


section("1) Páginas y estilos servidos en la raíz")
r = get("/")
check("GET / -> 200 (index.html)", r["status_code"] == 200, str(r["status_code"]))
check("index: elige sesión demo",
      "Sesión de demostración" in r["text"] and "guest-a1" in r["text"]
      and "manager-a" in r["text"], r["text"][:200])

r = get("/guest.html")
check("GET /guest.html -> 200", r["status_code"] == 200, str(r["status_code"]))
check("guest: mapa token->reserva",
      '"guest-a1": "STAY-A1"' in r["text"] and '"guest-b1": "STAY-B1"' in r["text"])
check("guest: llama a /api/messages", "/api/messages" in r["text"])
check("guest: historial /api/conversations",
      "/api/conversations/" in r["text"])

r = get("/manager.html")
check("GET /manager.html -> 200", r["status_code"] == 200, str(r["status_code"]))
check("manager: lista /api/proposals", "/api/proposals" in r["text"])
check("manager: botones approve/reject/reconcile",
      'act(p.id, "approve")' in r["text"] and 'act(p.id, "reject")' in r["text"]
      and 'act(p.id, "reconcile")' in r["text"])
check("manager: visor de conversaciones con trazas",
      "Trazas internas" in r["text"] and "traces" in r["text"])

r = get("/style.css")
check("GET /style.css -> 200", r["status_code"] == 200, str(r["status_code"]))
check("style.css no es JSON", r["body"] is None)

section("2) Las rutas API tienen prioridad sobre el montaje estático")
r = get("/health")
check("/health -> 200 JSON", r["status_code"] == 200
      and r["body"] == {"status": "ok", "service": "late-checkout-app"}, str(r))

r = get("/docs")
check("/docs -> 200 (OpenAPI)", r["status_code"] == 200 and "openapi" in r["text"])

r = get("/api/me", token="guest-a1")
check("/api/me guest-a1 -> 200", r["status_code"] == 200
      and r["body"].get("token") == "guest-a1"
      and r["body"].get("stay_id") == "STAY-A1", str(r["body"]))

r = get("/api/me")
check("/api/me sin token -> 401", r["status_code"] == 401, str(r["status_code"]))
check("/api/me 401 con formato de error",
      r["body"] and "error" in r["body"] and "code" in r["body"]["error"],
      str(r["body"]))

r = get("/api/proposals", token="guest-a1")
check("huésped en /api/proposals -> 403 (API, no estático)",
      r["status_code"] == 403, str(r["status_code"]))

section("3) Sin token remoto filtrado al navegador")
for path in ("/", "/guest.html", "/manager.html", "/style.css"):
    r = get(path)
    # Las claves remotas viven en .env/servidor; el cliente solo conoce
    # los tokens de sesión del kit.
    check(f"{path}: sin claves remotas en el HTML",
          "RESERVATIONS_" not in r["text"] and "LLM_API_KEY" not in r["text"])

print("\n" + "=" * 72)
print(f"RESULTADO: {PASS} pasaron, {FAIL} fallaron")
if FAILURES:
    print("Fallas:")
    for name in FAILURES:
        print(f"  - {name}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
