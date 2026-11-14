"""Configuración del servidor: carga las variables de .env una sola vez.

Decisiones documentadas:
- Las claves (tokens remotos, claves de IA) viven SOLO en .env y solo en el
  servidor. Nunca se envían al navegador ni a la IA.
- Los escenarios (normal, transient-503, ...) se configuran en el servidor.
  El huésped o la IA no pueden elegirlos.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Raíz del proyecto (directorio que contiene .env)
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    """Toda la configuración de la aplicación, inmutable una vez cargada."""

    # Servicio de reservas (mock de WorkFactory)
    reservations_base_url: str
    reservations_scenario: str
    # Cada sesión demo -> clave remota privada correspondiente
    reservations_credentials: dict[str, str]
    reservations_tester_token: str

    # IA simulada (mock de WorkFactory)
    llm_base_url: str
    llm_model: str
    llm_scenario: str
    llm_api_key: str
    llm_tester_token: str


def _require(name: str) -> str:
    """Lee una variable de .env; si falta, falla al arrancar (no en runtime)."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name} en .env")
    return value


def load_settings() -> Settings:
    return Settings(
        reservations_base_url=_require("RESERVATIONS_BASE_URL").rstrip("/"),
        reservations_scenario=os.environ.get("RESERVATIONS_SCENARIO", "normal"),
        reservations_credentials={
            "guest-a1": _require("RESERVATIONS_GUEST_A1_TOKEN"),
            "guest-a2": _require("RESERVATIONS_GUEST_A2_TOKEN"),
            "guest-b1": _require("RESERVATIONS_GUEST_B1_TOKEN"),
            "manager-a": _require("RESERVATIONS_MANAGER_A_TOKEN"),
            "manager-b": _require("RESERVATIONS_MANAGER_B_TOKEN"),
        },
        reservations_tester_token=_require("RESERVATIONS_TESTER_TOKEN"),
        llm_base_url=_require("LLM_BASE_URL").rstrip("/"),
        llm_model=_require("LLM_MODEL"),
        llm_scenario=os.environ.get("LLM_SCENARIO", "normal"),
        llm_api_key=_require("LLM_API_KEY"),
        llm_tester_token=_require("LLM_TESTER_TOKEN"),
    )


# Instancia única para toda la aplicación
settings = load_settings()

# User-Agent obligatorio: el README del kit avisa que Cloudflare bloquea
# el identificador por defecto de algunas librerías.
USER_AGENT = "WorkFactory-Technical-Test/1.0"
