"""Experimento contra el servicio real de IA: ¿cómo se debe enviar el contexto?

Uso (dentro del venv):
    python scripts/llm_context_probe.py

El contrato dice que el mock "interpreta el último mensaje con role:user"
y que el servicio "no guarda ni completa contexto por ti". Este probe
prueba en el servicio real tres formas de enviar el segundo turno del
caso P01 ("A las 14:00") para decidir cómo debe construir la lista de
mensajes app/messages.py:

    A) solo el primer turno                  (baseline: sin hora)
    B) turno 1 (user) + respuesta (assistant) + turno 2 (user)
    C) solo el segundo turno, sin historial

Salida esperada en el caso B (la que debe usar la app):
    {"intent": "late_checkout", "requested_time": "14:00", "language": "es"}
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.client import LlmClientError, RemoteLlmClient  # noqa: E402

client = RemoteLlmClient()


def show(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


TURN1 = "¿Puedo salir más tarde?"
TURN1_REPLY = "¿A qué hora te gustaría hacer la salida?"
TURN2 = "A las 14:00"

# A) Baseline: primer turno sin hora -> se espera requested_time: null
show("A) Primer turno solo (sin contexto previo)")
try:
    r = client.interpret([{"role": "user", "content": TURN1}])
    print("content:", r.content)
    print("request_id:", r.request_id, "usage:", r.usage)
except LlmClientError as err:
    print(f"ERROR {err.code}: {err.message}")

# B) Historial completo: user -> assistant -> user (el diseño candidato)
show("B) Historial: user + assistant + user (segundo turno con contexto)")
try:
    r = client.interpret([
        {"role": "user", "content": TURN1},
        {"role": "assistant", "content": TURN1_REPLY},
        {"role": "user", "content": TURN2},
    ])
    print("content:", r.content)
    print("request_id:", r.request_id, "usage:", r.usage)
except LlmClientError as err:
    print(f"ERROR {err.code}: {err.message}")

# C) Segundo turno sin historial: ¿reconoce la hora por sí solo?
show("C) Segundo turno SIN historial")
try:
    r = client.interpret([{"role": "user", "content": TURN2}])
    print("content:", r.content)
    print("request_id:", r.request_id, "usage:", r.usage)
except LlmClientError as err:
    print(f"ERROR {err.code}: {err.message}")

# D) Mensaje inglés del caso P02 (idioma y hora de una sola vez)
show("D) Caso P02: mensaje inglés completo")
try:
    r = client.interpret([
        {"role": "user",
         "content": "Tomorrow I would like to check out at 14:00. "
                    "Is that possible?"}
    ])
    print("content:", r.content)
    print("request_id:", r.request_id, "usage:", r.usage)
except LlmClientError as err:
    print(f"ERROR {err.code}: {err.message}")

print("\nProbe terminado (solo lectura: no modifica estado del servicio).")
