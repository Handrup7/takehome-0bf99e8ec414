"""Guion de prueba de la validación de la IA (no es parte de la app).

Uso (dentro del venv):
    python scripts/llm_probe.py

Hace dos cosas:
  1) Llama al servicio real (escenario normal) con un mensaje de ejemplo y
     muestra el contenido crudo + la validación.
  2) Pasa por validate_interpretation las 6 salidas locales del kit
     (datos/salidas-modelo.jsonl) para demostrar que la validación cubre
     todos los fallos reproducibles SIN tocar la red.

Esto cumple lo que pide el contrato: "Puedes usar respuestas locales en
tests… la demo llama al servicio remoto."
"""

import json
import sys
from pathlib import Path

# Permite importar app.* aunque el script esté en scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm import (  # noqa: E402
    LlmClientError,
    RemoteLlmClient,
    LlmValidationError,
    validate_interpretation,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def probe_live() -> None:
    _banner("1) Servicio real (escenario normal)")
    client = RemoteLlmClient()
    messages = [{"role": "user", "content": "Quiero salir a las 14:00"}]
    try:
        resp = client.interpret(messages)
    except LlmClientError as err:
        print(f"ERROR del servicio: {err.code} - {err.message}")
        return
    print(f"X-Request-ID : {resp.request_id}")
    print(f"Duración     : {resp.duration_ms} ms")
    print(f"Usage        : {resp.usage}")
    print(f"Contenido    : {resp.content!r}")
    _show_validation(resp.content)


def _show_validation(content: str) -> None:
    try:
        interp = validate_interpretation(content)
        print(f"VALIDACIÓN   : OK -> {interp.as_dict()}")
    except LlmValidationError as err:
        print(f"VALIDACIÓN   : RECHAZADA ({err.code}) -> {err.message}")


def probe_local_outputs() -> None:
    _banner("2) Salidas locales del kit (sin red)")
    path = DATA_DIR / "salidas-modelo.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        # Cada entrada tiene "output" (objeto) o "output_raw" (texto roto).
        if "output_raw" in item:
            content = item["output_raw"]
        else:
            content = json.dumps(item["output"], ensure_ascii=False)
        print(f"\n[{item['id']}] contenido={content!r}")
        _show_validation(content)


if __name__ == "__main__":
    probe_local_outputs()
    probe_live()
