"""Fixtures comunes para la batería de tests de los casos públicos (P01–P10).

En simple:
- Cada test empieza con la máquina "de fábrica": se borra la SQLite local
  y se reinician los dos mocks remotos (reservas y IA) con los tokens de
  tester. Cada combinación (candidato, escenario) tiene su propio estado
  remoto, así el reset usa el MISMO escenario que luego usará la app.
- Los escenarios se fijan por variables de entorno ANTES de lanzar pytest
  (load_dotenv no sobreescribe variables que ya existen, ver config.py):
      $env:RESERVATIONS_SCENARIO = "response-lost"
      $env:LLM_SCENARIO = "transient-503"
      python -m pytest
- Los tests que necesitan un escenario concreto llaman a require_scenario(...)
  y se SKIPEAN con un mensaje claro si la variable no coincide: un `pytest`
  plano (sin variables) corre los casos normales y nunca da falsos fallos.
"""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.store import store as local_store

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "data" / "eventos.jsonl"
CASES_PATH = ROOT / "data" / "casos-publicos.jsonl"


# ------------------------------------------------- controles de los mocks remotos
def _tester_headers(tester_token: str, scenario: str) -> dict:
    """Las dos cabeceras que piden todas las rutas /test/... del kit."""
    return {
        "Authorization": f"Bearer {tester_token}",
        "X-Test-Scenario": scenario,
    }


def _remote_reset(base_url: str, tester_token: str, scenario: str) -> None:
    """POST /test/reset a un mock. Falla el test si el mock no responde."""
    with httpx.Client(timeout=10) as http:
        r = http.post(
            f"{base_url}/test/reset",
            json={},
            headers=_tester_headers(tester_token, scenario),
        )
    assert r.status_code == 200, f"reset remoto falló: {r.status_code} {r.text}"


def remote_state() -> dict:
    """GET /test/state del mock de reservas: estancias, operaciones y contadores.

    Es la "ventana al interior" del mock: con sus contadores
    (applied_changes, idempotent_replays, conflicts, ...) comprobamos
    cuántos cambios reales hubo, sin fiarnos de lo que la app dice.
    """
    with httpx.Client(timeout=10) as http:
        r = http.get(
            f"{settings.reservations_base_url}/test/state",
            headers=_tester_headers(
                settings.reservations_tester_token,
                settings.reservations_scenario,
            ),
        )
    assert r.status_code == 200, f"no se pudo leer /test/state: {r.status_code} {r.text}"
    return r.json()


# ------------------------------------------------------------------- fixture de test
def _reset_local_db() -> None:
    """Vacía todas las tablas de late_checkout.db (estado de fábrica).

    Usa store.reset() y NO borra el archivo: en Windows no se puede
    borrar una BD con conexiones abiertas (y las conexiones de sqlite3
    no se cierran con `with conn`, que solo hace commit). Borrar el
    archivo era una receta para el PermissionError del probe anterior.
    """
    local_store.reset()


@pytest.fixture()
def client():
    """TestClient nueva por test, con el estado local y remoto limpios.

    TestClient es el mock del navegador: ejecuta la app en el mismo
    proceso, sin abrir puerto ni navegador (el mismo que usan los probes).
    """
    _reset_local_db()
    _remote_reset(
        settings.reservations_base_url,
        settings.reservations_tester_token,
        settings.reservations_scenario,
    )
    _remote_reset(
        settings.llm_base_url,
        settings.llm_tester_token,
        settings.llm_scenario,
    )
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- guardián de escenario
def require_scenario(kind: str, name: str) -> None:
    """Skipea el test si el escenario que necesita no es el actual.

    kind = "reservations" o "llm". El escenario se leyó de la variable de
    entorno al arrancar pytest (config.py), así basta comparar con settings.
    """
    if kind == "reservations":
        actual = settings.reservations_scenario
        var = "RESERVATIONS_SCENARIO"
    else:
        actual = settings.llm_scenario
        var = "LLM_SCENARIO"
    if actual != name:
        pytest.skip(
            f"este test necesita {var}={name} (la actual es {actual}). "
            f"Correr con: ${var}=\"{name}\"; python -m pytest -k <nombre-del-test>"
        )


# ----------------------------------------------------------- datos de los casos públicos
def load_case(case_id: str) -> dict:
    """Un caso de data/casos-publicos.jsonl (setup, assertions, escenario)."""
    with open(CASES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            if c["case_id"] == case_id:
                return c
    raise AssertionError(f"caso {case_id} no encontrado en casos-publicos.jsonl")


def load_events(case_id: str) -> list[dict]:
    """Todos los eventos de un caso, en el orden en que aparecen en
    data/eventos.jsonl (cada uno con su token, event_id, conversation_id,
    stay_id y text)."""
    events = []
    with open(EVENTS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e["case_id"] == case_id:
                events.append(e)
    return events


# ----------------------------------------------------------------- helpers de peticiones
def send_event(client: TestClient, event: dict):
    """Envía un evento por POST /api/messages con el token del propio evento.

    El cuerpo lleva EXACTAMENTE los 4 campos del contrato (app-api.md); el
    rol, el hotel y la reserva los resuelve el servidor a partir del token.
    Devuelve la respuesta (para inspeccionar status_code y .json()).
    """
    return client.post(
        "/api/messages",
        json={
            "event_id": event["event_id"],
            "conversation_id": event["conversation_id"],
            "stay_id": event["stay_id"],
            "text": event["text"],
        },
        headers={"Authorization": f"Bearer {event['token']}"},
    )


def auth(token: str) -> dict:
    """Cabecera Authorization para llamadas a /api/proposals y afines."""
    return {"Authorization": f"Bearer {token}"}
