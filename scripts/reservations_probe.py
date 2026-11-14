"""Verificación rápida de reservations.py (sanity check, no es parte de la app).

Uso (dentro del venv):
    python scripts/reservations_probe.py

Habla contra el SERVICIO REAL de reservas. Primero hace POST /test/reset
(con el token de tester y el escenario de .env) para que el probe sea
determinista. Comprueba: lectura de estancia, disponibilidad, escritura
idempotente (misma clave + contenido -> mismo recibo) y reconciliación
con GET /operations/{key}.

NOTA: el probe MODIFICA el estado del servicio (cambia STAY-A1 a 14:00).
Para restaurarlo: POST /test/reset con el tester token.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import USER_AGENT, settings  # noqa: E402
from app.reservations import (  # noqa: E402
    ReservationClientError,
    ReservationsClient,
    full_checkout_at,
)


def show(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


client = ReservationsClient()
base = settings.reservations_base_url
scenario = settings.reservations_scenario


def tester_call(method: str, path: str, body: dict | None = None):
    return httpx.request(
        method,
        f"{base}{path}",
        headers={
            "Authorization": f"Bearer {settings.reservations_tester_token}",
            "User-Agent": USER_AGENT,
            "X-Test-Scenario": scenario,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15.0,
    )


# 0) Reset del estado para que el probe sea determinista
show("0) Reset del estado de prueba (tester, escenario del .env)")
r = tester_call("POST", "/test/reset", {})
print(f"HTTP {r.status_code} {r.text}")

# 1) Estado inicial de la estancia
show("1) Estado de la estancia (guest-a1 / STAY-A1)")
stay = client.get_stay("guest-a1", "STAY-A1")
print(stay)
print("esperado: checkout_at 2026-11-10T11:00:00Z, version 1")

# 2) Disponibilidad: 14:00 válida, 11:00 igual a la salida actual
show("2) Disponibilidad (14:00 valida / 11:00 no posterior a la actual)")
ok = client.check_availability("guest-a1", "STAY-A1", full_checkout_at("14:00"))
print("14:00 ->", ok)
print("esperado: available=true")
no = client.check_availability("guest-a1", "STAY-A1", full_checkout_at("11:00"))
print("11:00 ->", no)
print("esperado: available=false, reason=not_later_than_current_checkout")

# 3) Escritura con manager-a + idempotencia (misma clave y contenido)
show("3) Escritura (manager-a) y repetición idempotente con la misma clave")
key = "probe-0001"
receipt = client.apply_change(
    "manager-a", "STAY-A1", full_checkout_at("14:00"),
    expected_version=1, operation_key=key,
)
print("1a llamada ->", receipt)
print("esperado: version 2, operation_key probe-0001, change_id chg_...")
replay = client.apply_change(
    "manager-a", "STAY-A1", full_checkout_at("14:00"),
    expected_version=1, operation_key=key,
)
print("2a llamada (misma clave+contenido) ->", replay)
print("esperado: mismo recibo (repetición de un cambio ya aplicado)")

# 4) Reconciliación: consulta de la operación
show("4) Reconciliación GET /operations/{key}")
op = client.get_operation("manager-a", key)
print(op)
print("esperado: status=applied con el mismo receipt")

# 5) Estado de la estancia tras el cambio
show("5) Estancia tras el cambio (guest-a1 / STAY-A1)")
stay2 = client.get_stay("guest-a1", "STAY-A1")
print(stay2)
print("esperado: checkout_at 2026-11-10T14:00:00Z, version 2")

# 6) Operación inexistente: 404 determinista (NO incierto)
show("6) Operación inexistente (404 determinista, no incierto)")
try:
    client.get_operation("manager-a", "probe-never-used")
    print("ERROR: debería haber fallado con 404")
except ReservationClientError as err:
    print(f"code={err.code} status={err.status} ambiguous={err.ambiguous}")
    print("esperado: code=operation_not_found, status=404, ambiguous=False")

# 7) Aislamiento: guest-a1 NO puede leer STAY-B1 (otro hotel)
show("7) Aislamiento: guest-a1 intenta leer STAY-B1")
try:
    client.get_stay("guest-a1", "STAY-B1")
    print("ERROR: debería haber fallado con 404/403")
except ReservationClientError as err:
    print(f"code={err.code} status={err.status} (esperado: 404 o 403, sin datos ajenos)")

print("\nProbe terminado. El estado del servicio quedó MODIFICADO "
      "(STAY-A1 a 14:00); usa POST /test/reset para restaurarlo.")
