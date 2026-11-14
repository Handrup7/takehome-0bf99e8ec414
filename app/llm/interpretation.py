"""Validación de la interpretación que devuelve la IA.

La IA devuelve texto que contiene un JSON. Ese JSON NO es de fiar:
puede estar roto, tener una hora imposible o intentar autoconcederse
permisos. Esta función es PURA (sin red): solo convierte ese texto crudo
en una Interpretación válida o lanza un error con código y mensaje útil.

Reglas (contrato llm-api.md):
- La interpretación válida usa SOLO tres campos:
    intent          == "late_checkout"
    requested_time  == hora "HH:MM" válida o null
    language        == "es" o "en"
- Si falta la hora (null) => el huésped debe ser preguntado (no es error).
- Los campos extra NO dan permisos ni cambian el precio: se descartan.
- Un JSON roto o una hora inválida NO deben producir un cambio ni un 500
  sin explicación: se lanza LlmValidationError con código y mensaje.
"""

import json
import re

# HH:MM con horas 00-23 y minutos 00-59.
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_VALID_LANGUAGES = ("es", "en")
_VALID_INTENT = "late_checkout"


class LlmValidationError(Exception):
    """La salida de la IA no es una interpretación válida.

    code:    identificador estable para el registro y los tests
             (no depende de una frase exacta).
    message: texto útil para el huésped / el responsable.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Interpretation:
    """Interpretación validada y lista para usar en la aplicación.

    requested_time == None significa "el huésped no dio hora": la app
    debe preguntarle, no rechazar ni crear una propuesta.
    """

    def __init__(self, intent: str, requested_time: str | None, language: str):
        self.intent = intent
        self.requested_time = requested_time
        self.language = language

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "requested_time": self.requested_time,
            "language": self.language,
        }


def validate_interpretation(content: str) -> Interpretation:
    """Valida el texto crudo de la IA y devuelve una Interpretation.

    Lanza LlmValidationError si algo no encaja. Nunca levanta 500.
    """
    # 1) Debe ser JSON parseable y un objeto.
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        raise LlmValidationError(
            "invalid_json",
            "La IA devolvió un JSON no válido; no se ha aplicado ningún cambio.",
        )
    if not isinstance(data, dict):
        raise LlmValidationError(
            "invalid_json",
            "La respuesta de la IA no es un objeto JSON.",
        )

    # 2) intent: solo se acepta late_checkout.
    intent = data.get("intent")
    if intent != _VALID_INTENT:
        raise LlmValidationError(
            "invalid_intent",
            "La intención no es una solicitud de salida tardía.",
        )

    # 3) requested_time: null (preguntar) o "HH:MM" válido.
    raw_time = data.get("requested_time")
    if raw_time is None:
        requested_time = None
    else:
        if not isinstance(raw_time, str) or not _TIME_RE.match(raw_time):
            raise LlmValidationError(
                "invalid_time",
                "La hora indicada no tiene un formato HH:MM válido.",
            )
        requested_time = raw_time

    # 4) language: solo "es" o "en".
    language = data.get("language")
    if language not in _VALID_LANGUAGES:
        raise LlmValidationError(
            "invalid_language",
            "El idioma de la solicitud no está admitido.",
        )

    # 5) Los campos extra (approved, role, fee, hotel_id, ...) se DESCARTAN
    #    deliberadamente: la IA no puede concederse permisos ni precio.
    return Interpretation(
        intent=intent, requested_time=requested_time, language=language
    )
