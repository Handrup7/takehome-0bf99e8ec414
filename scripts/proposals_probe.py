"""Probe de app/proposals.py y app/conversations.py contra los servicios REMOTOS reales.

Uso (dentro del venv, desde la raíz del proyecto, escenario `normal`):
    python scripts/proposals_probe.py

Recorrido:
  0. Reinicio limpio: base local (store.reset) + POST /test/reset en
     reservas y en la IA (escenario `normal`).
  1. Crea dos propuestas por el flujo del huésped:
     P_A = STAY-A1 14:00 (fee 25, coral-alba-policy v2, op-STAY-A1-1400)
     P_B = STAY-B1 12:00 (fee 15, brisa-alba-policy v2, op-STAY-B1-1200)
  2. GET /api/proposals: 401 sin token, 403 huésped, aislamiento por hotel.
  3. Control de acceso a approve/reject: 403 huésped, 403 responsable ajeno
     (sin efectos), 404 propuesta inexistente.
  4. Stale por versión: bump remoto de STAY-B1 (v1 -> v2) y aprobar P_B
     -> 409 stale (el re-check de versión lo detecta antes de escribir).
  5. Aprobación exitosa de P_A: 200 completed con recibo concordante;
     replay local -> 200 sin nueva escritura (contador no cambia).
  6. Nueva propuesta con sufijo tras estado cerrado (op-STAY-B1-1200-2)
     y flujo de rechazo: 200, 200 idempotente, approve -> 409, ajeno -> 403,
     reject de una completed -> 409.
  7. reconcile: completed/rejected/stale -> 200 sin cambio; 404; 403 huésped.
  8. GET /api/conversations/{id}: 401/403/404, propietario sin trazas,
     responsable con trazas.
  9. Estado remoto final (GET /test/state): operaciones, estancias y
     contadores coherentes.
"""

import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import USER_AGENT, settings  # noqa: E402
from app.main import app  # noqa: E402
from app.store import store  # noqa: E402

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


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def post_message(token: str, body: dict) -> dict:
    r = client.post("/api/messages", json=body, headers=_headers(token))
    return {"status_code": r.status_code, "body": r.json()}


def list_proposals(token: str | None) -> dict:
    headers = _headers(token) if token else {}
    r = client.get("/api/proposals", headers=headers)
    return {"status_code": r.status_code, "body": r.json()}


def propose_action(proposal_id: str, action: str, token: str | None) -> dict:
    headers = _headers(token) if token else {}
    r = client.post(f"/api/proposals/{proposal_id}/{action}", headers=headers)
    return {"status_code": r.status_code, "body": r.json()}


def get_conversation(conv_id: str, token: str | None) -> dict:
    headers = _headers(token) if token else {}
    r = client.get(f"/api/conversations/{conv_id}", headers=headers)
    return {"status_code": r.status_code, "body": r.json()}


def test_reset(base_url: str, token: str, scenario: str) -> None:
    r = httpx.post(
        f"{base_url}/test/reset",
        json={},
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "X-Test-Scenario": scenario,
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )
    if r.status_code != 200:
        raise RuntimeError(f"reset falló: HTTP {r.status_code} {r.text}")


def test_state() -> dict:
    r = httpx.get(
        f"{settings.reservations_base_url}/test/state",
        headers={
            "Authorization": f"Bearer {settings.reservations_tester_token}",
            "User-Agent": USER_AGENT,
            "X-Test-Scenario": settings.reservations_scenario,
        },
        timeout=15.0,
    )
    if r.status_code != 200:
        raise RuntimeError(f"state falló: HTTP {r.status_code} {r.text}")
    return r.json()


def raw_change(session: str, body: dict) -> dict:
    """Escritura directa al remoto (control de pruebas del kit)."""
    r = httpx.post(
        f"{settings.reservations_base_url}/changes",
        json=body,
        headers={
            "Authorization": f"Bearer {settings.reservations_credentials[session]}",
            "User-Agent": USER_AGENT,
            "X-Test-Scenario": settings.reservations_scenario,
            "Content-Type": "application/json",
        },
        timeout=15.0,
    )
    try:
        parsed = r.json()
    except ValueError:
        parsed = None
    return {"status_code": r.status_code, "body": parsed}


# ------------------------------------------------------------------ 0. reinicio
section("0) Reinicio limpio (base local + remotos, escenario normal)")
store.reset()
test_reset(settings.reservations_base_url, settings.reservations_tester_token,
           settings.reservations_scenario)
test_reset(settings.llm_base_url, settings.llm_tester_token, settings.llm_scenario)
print("  base local vaciada; reservas e IA restablecidos en 'normal'")

# ------------------------------------------------- 1) crear propuestas (huésped)
section("1) Crear P_A (STAY-A1 14:00) y P_B (STAY-B1 12:00) por el flujo del huésped")
r = post_message("guest-a1", {
    "event_id": "evt-prop-a",
    "conversation_id": "conv-prop-a",
    "stay_id": "STAY-A1",
    "text": "Quiero salir a las 14:00.",
})
b = r["body"]
check("P_A: HTTP 200", r["status_code"] == 200, str(r))
check("P_A: awaiting_approval", b.get("status") == "awaiting_approval", str(b))
prop_a = b.get("proposal_id")
check("P_A: con proposal_id", bool(prop_a), str(b))

r = post_message("guest-b1", {
    "event_id": "evt-prop-b",
    "conversation_id": "conv-prop-b",
    "stay_id": "STAY-B1",
    "text": "Quiero salir a las 12:00.",
})
b = r["body"]
check("P_B: HTTP 200", r["status_code"] == 200, str(r))
check("P_B: awaiting_approval", b.get("status") == "awaiting_approval", str(b))
prop_b = b.get("proposal_id")
check("P_B: con proposal_id", bool(prop_b), str(b))

db_a = store.get_proposal(prop_a) if prop_a else None
check("P_A: propuesta en BD", db_a is not None)
if db_a:
    check("P_A: fee 25, política coral v2",
          db_a["fee_eur"] == 25 and db_a["policy_document_id"] == "coral-alba-policy"
          and db_a["policy_version"] == 2, str(db_a))
    check("P_A: key op-STAY-A1-1400", db_a["operation_key"] == "op-STAY-A1-1400",
          str(db_a["operation_key"]))
    check("P_A: stay_version 1", db_a["stay_version"] == 1, str(db_a["stay_version"]))
db_b = store.get_proposal(prop_b) if prop_b else None
check("P_B: propuesta en BD", db_b is not None)
if db_b:
    check("P_B: fee 15, política brisa v2",
          db_b["fee_eur"] == 15 and db_b["policy_document_id"] == "brisa-alba-policy"
          and db_b["policy_version"] == 2, str(db_b))
    check("P_B: key op-STAY-B1-1200", db_b["operation_key"] == "op-STAY-B1-1200",
          str(db_b["operation_key"]))

# ------------------------------------------------------------ 2) listado y roles
section("2) GET /api/proposals: 401, 403 huésped, aislamiento por hotel")
r = list_proposals(None)
check("sin token -> 401", r["status_code"] == 401, str(r))

r = list_proposals("guest-a1")
check("huésped -> 403", r["status_code"] == 403, str(r))

r = list_proposals("manager-a")
check("manager-a -> 200", r["status_code"] == 200, str(r))
props_a = r["body"].get("proposals", [])
check("manager-a ve 1 propuesta (la suya)", len(props_a) == 1, str(len(props_a)))
if props_a:
    view = props_a[0]
    check("P_A visible para manager-a", view.get("id") == prop_a, str(view))
    check("campos de contrato presentes",
          set(view) >= {"id", "stay_id", "status", "current_checkout_at",
                        "requested_checkout_at", "fee_eur",
                        "policy_document_id", "policy_version", "evidence",
                        "operation_key", "receipt"}, str(sorted(view)))
    check("evidence con document_id/version/quote",
          set(view.get("evidence", {})) >= {"document_id", "version", "quote"},
          str(view.get("evidence")))
    check("receipt null (aún no comprobada)", view.get("receipt") is None, str(view.get("receipt")))
    check("fee 25 y hora 14:00",
          view.get("fee_eur") == 25
          and view.get("requested_checkout_at") == "2026-11-10T14:00:00Z", str(view))
    check("campos de revalidación (stay_version, hotel_id)",
          view.get("stay_version") == 1 and view.get("hotel_id") == "hotel-alba", str(view))

r = list_proposals("manager-b")
check("manager-b -> 200", r["status_code"] == 200, str(r))
props_b = r["body"].get("proposals", [])
check("manager-b ve solo la suya (P_B)", len(props_b) == 1
      and props_b[0].get("id") == prop_b, str([p.get("id") for p in props_b]))
check("P_A no se filtra al otro hotel", all(p.get("id") != prop_a for p in props_b),
      str([p.get("id") for p in props_b]))

# --------------------------------------------------- 3) control de acceso approve
section("3) Control de acceso a approve/reject: 403 y 404 sin efectos")
r = propose_action(prop_a, "approve", "guest-a1")
check("huésped aprueba -> 403", r["status_code"] == 403, str(r))

r = propose_action(prop_a, "approve", "manager-b")
check("responsable de otro hotel aprueba -> 403", r["status_code"] == 403, str(r))
check("sin efectos: P_A sigue awaiting_approval",
      (store.get_proposal(prop_a) or {}).get("status") == "awaiting_approval",
      str(store.get_proposal(prop_a)))

r = propose_action(prop_a, "reject", "manager-b")
check("responsable de otro hotel rechaza -> 403", r["status_code"] == 403, str(r))
check("sin efectos: P_A sigue awaiting_approval",
      (store.get_proposal(prop_a) or {}).get("status") == "awaiting_approval",
      str(store.get_proposal(prop_a)))

r = propose_action("prop-no-existe", "approve", "manager-a")
check("propuesta inexistente -> 404", r["status_code"] == 404, str(r))
r = propose_action("prop-no-existe", "reject", "manager-a")
check("propuesta inexistente (reject) -> 404", r["status_code"] == 404, str(r))

# ----------------------------------------------------------------- 4) stale por versión
section("4) Stale: la versión de STAY-B1 cambia antes de aprobar P_B")
# Escritura directa del kit: STAY-B1 11:00 con expected_version 1 -> v2.
r = raw_change("manager-b", {
    "stay_id": "STAY-B1",
    "requested_checkout_at": "2026-11-10T11:00:00Z",
    "expected_version": 1,
    "operation_key": "probe-stale-bump",
})
check("bump remoto STAY-B1 -> 201", r["status_code"] == 201, str(r))
check("bump dejó versión 2", (r["body"] or {}).get("version") == 2, str(r["body"]))

r = propose_action(prop_b, "approve", "manager-b")
b = r["body"]
check("aprobar P_B tras el bump -> 409", r["status_code"] == 409, str(r))
check("estado stale", b.get("status") == "stale", str(b))
check("sin recibo", b.get("receipt") is None, str(b))
check("P_B quedó stale en BD", (store.get_proposal(prop_b) or {}).get("status") == "stale",
      str(store.get_proposal(prop_b)))

# ------------------------------------------------------------- 5) approve exitoso
section("5) Aprobación exitosa de P_A: 200 completed + recibo; replay local")
r = propose_action(prop_a, "approve", "manager-a")
b = r["body"]
check("aprobar P_A -> 200", r["status_code"] == 200, str(r))
check("estado completed", b.get("status") == "completed", str(b))
receipt = b.get("receipt") or {}
check("recibo: misma estancia", receipt.get("stay_id") == "STAY-A1", str(receipt))
check("recibo: hora 14:00",
      receipt.get("checkout_at") == "2026-11-10T14:00:00Z", str(receipt))
check("recibo: misma operation_key",
      receipt.get("operation_key") == "op-STAY-A1-1400", str(receipt))
check("recibo: versión 2 (1+1)", receipt.get("version") == 2, str(receipt))
check("P_A completed en BD con recibo",
      (store.get_proposal(prop_a) or {}).get("status") == "completed"
      and (store.get_proposal(prop_a) or {}).get("receipt") is not None,
      str(store.get_proposal(prop_a)))

# Replay: la app ya tiene el recibo -> 200 sin tocar el servicio remoto.
state_before = test_state()
r = propose_action(prop_a, "approve", "manager-a")
b = r["body"]
check("replay de aprobación -> 200", r["status_code"] == 200, str(r))
check("replay: estado completed", b.get("status") == "completed", str(b))
check("replay: mismo recibo", b.get("receipt") == receipt, str(b.get("receipt")))
state_after = test_state()
check("replay no lanzó nueva escritura (applied_changes igual)",
      state_after["counters"]["applied_changes"] == state_before["counters"]["applied_changes"],
      f"{state_before['counters']['applied_changes']} -> {state_after['counters']['applied_changes']}")
check("replay no reenvió la operación (idempotent_replays == 0)",
      state_after["counters"]["idempotent_replays"] == 0,
      str(state_after["counters"]["idempotent_replays"]))

# ------------------------------------------------- 6) reintento con sufijo + reject
section("6) P_C: reintento de 12:00 en STAY-B1 (sufijo -2) y flujo de rechazo")
r = post_message("guest-b1", {
    "event_id": "evt-prop-c",
    "conversation_id": "conv-prop-b",
    "stay_id": "STAY-B1",
    "text": "Vale, quiero las 12:00.",
})
b = r["body"]
check("P_C: HTTP 200", r["status_code"] == 200, str(r))
check("P_C: awaiting_approval", b.get("status") == "awaiting_approval", str(b))
prop_c = b.get("proposal_id")
check("P_C: propuesta nueva (distinta de P_B)", bool(prop_c) and prop_c != prop_b, str(b))
db_c = store.get_proposal(prop_c) if prop_c else None
check("P_C: clave con sufijo op-STAY-B1-1200-2",
      db_c is not None and db_c["operation_key"] == "op-STAY-B1-1200-2",
      str(db_c and db_c["operation_key"]))
check("P_C: fee 15 y stay_version 2 (estado vigente)",
      db_c is not None and db_c["fee_eur"] == 15 and db_c["stay_version"] == 2,
      str(db_c))
check("P_C: current_checkout 11:00 (el del bump)",
      db_c is not None and db_c["current_checkout_at"] == "2026-11-10T11:00:00Z",
      str(db_c and db_c["current_checkout_at"]))

r = propose_action(prop_c, "reject", "manager-b")
check("rechazar P_C -> 200", r["status_code"] == 200, str(r))
check("estado rejected", r["body"].get("status") == "rejected", str(r["body"]))

r = propose_action(prop_c, "reject", "manager-b")
check("rechazo repetido -> 200 (idempotente)", r["status_code"] == 200, str(r))
check("sigue rejected", r["body"].get("status") == "rejected", str(r["body"]))

r = propose_action(prop_c, "approve", "manager-b")
check("aprobar una rechazada -> 409", r["status_code"] == 409, str(r))
check("409: estado rejected", r["body"].get("status") == "rejected", str(r["body"]))

r = propose_action(prop_c, "reject", "manager-a")
check("rechazo de otro hotel -> 403", r["status_code"] == 403, str(r))
check("sin efectos: P_C sigue rejected",
      (store.get_proposal(prop_c) or {}).get("status") == "rejected",
      str(store.get_proposal(prop_c)))

r = propose_action(prop_a, "reject", "manager-a")
check("rechazar una completed -> 409", r["status_code"] == 409, str(r))
check("409: estado completed", r["body"].get("status") == "completed", str(r["body"]))

# ------------------------------------------------------------------ 7) reconcile
section("7) reconcile: estados cerrados -> 200 sin cambio; 404; 403 huésped")
r = propose_action(prop_a, "reconcile", "manager-a")
check("reconcile completed -> 200", r["status_code"] == 200, str(r))
check("estado completed", r["body"].get("status") == "completed", str(r["body"]))
check("recibo presente", r["body"].get("receipt") == receipt, str(r["body"].get("receipt")))

r = propose_action(prop_c, "reconcile", "manager-b")
check("reconcile rejected -> 200", r["status_code"] == 200, str(r))
check("estado rejected", r["body"].get("status") == "rejected", str(r["body"]))

r = propose_action(prop_b, "reconcile", "manager-b")
check("reconcile stale -> 200", r["status_code"] == 200, str(r))
check("estado stale", r["body"].get("status") == "stale", str(r["body"]))

r = propose_action("prop-no-existe", "reconcile", "manager-b")
check("reconcile inexistente -> 404", r["status_code"] == 404, str(r))

r = propose_action(prop_a, "reconcile", "guest-a1")
check("reconcile con huésped -> 403", r["status_code"] == 403, str(r))

# --------------------------------------------------------------- 8) conversación
section("8) GET /api/conversations/{id}: permisos y trazas solo para el responsable")
r = get_conversation("conv-prop-a", None)
check("sin token -> 401", r["status_code"] == 401, str(r))

r = get_conversation("conv-prop-a", "guest-a2")
check("huésped ajeno -> 403", r["status_code"] == 403, str(r))

r = get_conversation("conv-prop-a", "manager-b")
check("responsable de otro hotel -> 403", r["status_code"] == 403, str(r))

r = get_conversation("conv-no-existe", "guest-a1")
check("conversación inexistente -> 404", r["status_code"] == 404, str(r))

r = get_conversation("conv-prop-a", "guest-a1")
b = r["body"]
check("propietario -> 200", r["status_code"] == 200, str(r))
check("metadatos de la conversación",
      b.get("conversation_id") == "conv-prop-a"
      and b.get("hotel_id") == "hotel-alba"
      and b.get("stay_id") == "STAY-A1", str(b))
msgs = b.get("messages", [])
check("historial no vacío", len(msgs) >= 1, str(len(msgs)))
check("mensaje con texto y resultado",
      msgs and msgs[0].get("text") == "Quiero salir a las 14:00."
      and msgs[0].get("status") == "awaiting_approval"
      and bool(msgs[0].get("message")), str(msgs[:1]))
check("al huésped NO se le devuelven trazas", "traces" not in b, str(sorted(b)))

r = get_conversation("conv-prop-a", "manager-a")
b = r["body"]
check("responsable propio -> 200", r["status_code"] == 200, str(r))
traces = b.get("traces")
check("al responsable sí se le devuelven trazas",
      isinstance(traces, dict) and len(traces) >= 1, str(type(traces)))
if isinstance(traces, dict) and traces:
    events = [ev.get("event") for ev in next(iter(traces.values()))]
    check("traza con pasos clave",
          {"identity", "llm_request", "llm_validated", "policy_resolved",
           "proposal_created", "message_saved"} <= set(events),
          str(events))

# --------------------------------------------------------------- 9) estado remoto
section("9) Estado remoto final (GET /test/state)")
state = test_state()
ops = {o.get("operation_key"): o for o in state.get("operations", [])}
check("operación op-STAY-A1-1400 aplicada", "op-STAY-A1-1400" in ops, str(list(ops)))
check("operación probe-stale-bump aplicada", "probe-stale-bump" in ops, str(list(ops)))
check("P_C no generó operación (rechazada antes de escribir)",
      "op-STAY-B1-1200-2" not in ops, str(list(ops)))
stays = {s.get("stay_id"): s for s in state.get("stays", [])}
check("STAY-A1 queda a 14:00 v2",
      stays.get("STAY-A1", {}).get("checkout_at") == "2026-11-10T14:00:00Z"
      and stays.get("STAY-A1", {}).get("version") == 2, str(stays.get("STAY-A1")))
check("STAY-B1 queda a 11:00 v2",
      stays.get("STAY-B1", {}).get("checkout_at") == "2026-11-10T11:00:00Z"
      and stays.get("STAY-B1", {}).get("version") == 2, str(stays.get("STAY-B1")))
counters = state.get("counters", {})
check("applied_changes == 2", counters.get("applied_changes") == 2, str(counters))
check("conflicts == 0", counters.get("conflicts") == 0, str(counters))

# ------------------------------------------------------------------ resumen
print("\n" + "=" * 72)
print(f"RESULTADO: {PASS} pasaron, {FAIL} fallaron")
if FAILURES:
    print("Fallas:")
    for name in FAILURES:
        print(f"  - {name}")
print("=" * 72)
sys.exit(1 if FAIL else 0)
