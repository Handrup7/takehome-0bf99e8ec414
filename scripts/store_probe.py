"""Verificación rápida de store.py (sanity check, no es parte de la app).

Uso (dentro del venv):
    python scripts/store_probe.py

Comprueba: conversaciones, deduplicación de mensajes, ciclo de vida de
una propuesta, trazas y llm_calls. Termina con reset() para dejar la BD
limpia.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.store import Store, new_id  # noqa: E402

# Usa una BD de prueba separada para no tocar la BD de la app.
store = Store(db_path=Path(__file__).resolve().parent.parent / "probe.db")


def show(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


store.reset()

# 1) Conversación ligada a su propietario
show("1) Conversación ligada a propietario/hotel/reserva")
conv = store.create_conversation("conv-p01", "guest-a1", "hotel-alba", "STAY-A1")
print(conv)

# 2) Mensaje guardado con su resultado; duplicado se localiza por clave
show("2) Mensaje y clave de deduplicación")
store.save_message(
    conversation_id="conv-p01", event_id="evt-p01-1", actor_token="guest-a1",
    stay_id="STAY-A1", text="¿Puedo salir más tarde?", status="needs_input",
    response_message="¿A qué hora quieres salir?", proposal_id=None,
    trace_id=new_id("tr"),
)
dup = store.find_message_event("guest-a1", "conv-p01", "evt-p01-1")
print(f"mismo actor+conv+event -> {dup['status']} (esperado: needs_input)")
otro = store.find_message_event("guest-a2", "conv-p01", "evt-p01-1")
print(f"otro actor, mismo event -> {otro} (esperado: None, no hay fugas)")

# 3) Propuesta: ciclo de vida completo
show("3) Propuesta: awaiting_approval -> processing -> pending_verification -> completed")
prop = store.add_proposal(
    conversation_id="conv-p01", actor_token="guest-a1",
    org_id="org-coral", hotel_id="hotel-alba", stay_id="STAY-A1",
    requested_checkout_at="2026-11-10T14:00:00Z",
    current_checkout_at="2026-11-10T11:00:00Z", stay_version=1, fee_eur=25,
    policy_document_id="coral-alba-policy", policy_version=2,
    evidence={"document_id": "coral-alba-policy", "version": 2,
              "quote": "Puede solicitarse salida tardía hasta las 14:00 inclusive."},
    operation_key="op-probe-0001",
    status="awaiting_approval",
)
prop_id = prop["id"]
store.set_proposal_status(prop_id, "processing")
store.set_proposal_status(prop_id, "pending_verification")
receipt = {"stay_id": "STAY-A1", "checkout_at": "2026-11-10T14:00:00Z",
           "version": 2, "operation_key": prop["operation_key"],
           "change_id": "chg_demo"}
store.set_proposal_receipt(prop_id, receipt)
store.set_proposal_status(prop_id, "completed")
final = store.get_proposal(prop_id)
print(f"status={final['status']} operation_key={final['operation_key']}")
print(f"receipt={final['receipt']}")
print(f"evidence={final['evidence']}")

# 4) Aislamiento por hotel en el listado
show("4) Listado por hotel (los responsables solo ven el suyo)")
print("hotel-alba   ->", [p["id"] for p in store.list_proposals("hotel-alba")])
print("hotel-alba-b ->", [p["id"] for p in store.list_proposals("hotel-alba-b")],
      "(esperado: vacío)")

# 5) Traza y llamada a la IA
show("5) Traza y llm_calls (tokens marcados como simulados)")
trace_id = new_id("tr")
store.begin_trace(trace_id)
store.add_trace_event(trace_id, "message_received", {"event_id": "evt-p01-2"})
store.add_trace_event(trace_id, "llm_call", {"request_id": "req-demo"})
store.add_llm_call(
    trace_id=trace_id, request_id="req-demo", scenario="normal",
    status="ok", duration_ms=280,
    usage={"prompt_tokens": 6, "completion_tokens": 17, "total_tokens": 23},
    validated=True, validation_code=None,
)
for ev in store.get_trace(trace_id):
    print(f"  {ev['event']} -> {ev['detail']}")
import sqlite3  # noqa: E402
with store._conn() as conn:
    row = conn.execute("SELECT usage FROM llm_calls LIMIT 1").fetchone()
print(f"usage guardado = {row[0]}  (incluye 'simulated': true)")

# Limpieza final: la BD queda vacía (probe.db no se sube a Git, está en .gitignore)
store.reset()
print("\nBD de prueba vaciada con reset().")
