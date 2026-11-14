"""Cliente HTTP para el servicio de IA simulada de WorkFactory.

El cliente es responsable SOLO del transporte:
- enviar la petición con la clave, el User-Agent y el escenario correctos,
- reintentar de forma LIMITADA ante un fallo temporal (503) respetando
  Retry-After,
- devolver el contenido CRUDO junto con metadatos (X-Request-ID, duración,
  usage).

La validación del contenido NO está aquí: vive en interpretation.py.
Así la app puede probar la validación offline y el cliente se usa solo
en la demo contra el servicio real.
"""

import json
import time

import httpx

from ..config import USER_AGENT, settings


class LlmClientError(Exception):
    """Fallo del servicio de IA o de la conexión.

    retryable:  si es True el caller puede reintentar.
    retry_after: segundos que indica la cabecera Retry-After, si existe.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class LlmResponse:
    """Resultado crudo de una llamada a la IA (sin validar aún)."""

    def __init__(self, content: str, request_id: str | None,
                 duration_ms: int, usage: dict | None):
        self.content = content            # texto crudo (contiene el JSON)
        self.request_id = request_id      # X-Request-ID, para el registro
        self.duration_ms = duration_ms
        self.usage = usage                # tokens simulados (no es coste)


class RemoteLlmClient:
    """Cliente del servicio remoto. En tests se sustituye por un stub
    con el mismo método interpret().
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        scenario: str | None = None,
        max_retries: int = 3,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.model = model or settings.llm_model
        self.api_key = api_key or settings.llm_api_key
        self.scenario = scenario or settings.llm_scenario
        self.max_retries = max_retries
        self.timeout = timeout

    def interpret(self, messages: list[dict]) -> LlmResponse:
        """Envía messages y devuelve el contenido crudo, reintentando
        de forma limitada ante un fallo temporal (503).
        """
        body = {"model": self.model, "messages": messages}
        last: LlmClientError | None = None
        for attempt in range(self.max_retries):
            try:
                return self._call_once(body)
            except LlmClientError as err:
                last = err
                if not err.retryable:
                    raise
                if attempt == self.max_retries - 1:
                    break
                if err.retry_after is not None:
                    time.sleep(err.retry_after)
        raise last  # type: ignore[misc]

    def _call_once(self, body: dict) -> LlmResponse:
        start = time.monotonic()
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                    # El escenario lo fija el servidor, NUNCA el huésped o la IA.
                    "X-Test-Scenario": self.scenario,
                },
                timeout=self.timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError):
            # Desconexión/timeout: es temporal y reintentable.
            raise LlmClientError(
                "connection_error",
                "No se pudo conectar con el servicio de IA.",
                retryable=True,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        request_id = response.headers.get("X-Request-ID")

        if response.status_code == 200:
            try:
                data = response.json()
            except (ValueError, json.JSONDecodeError):
                raise LlmClientError(
                    "invalid_response",
                    "El servicio de IA devolvió un cuerpo no-JSON.",
                    status=200,
                )
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                raise LlmClientError(
                    "invalid_response",
                    "La respuesta de la IA no tiene choices[0].message.content.",
                    status=200,
                )
            return LlmResponse(
                content=content,
                request_id=request_id,
                duration_ms=duration_ms,
                usage=data.get("usage"),
            )

        # Errores: se identifican por HTTP y error.code, no por la frase.
        code, message = self._parse_error(response)

        # 503 "temporarily_unavailable": temporal => reintentar.
        if response.status_code == 503 and code == "temporarily_unavailable":
            retry_after = response.headers.get("Retry-After")
            try:
                retry_after = float(retry_after) if retry_after else None
            except ValueError:
                retry_after = None
            raise LlmClientError(
                code,
                message or "Servicio temporalmente no disponible.",
                status=503,
                retryable=True,
                retry_after=retry_after,
            )

        raise LlmClientError(
            code,
            message or f"El servicio de IA respondió HTTP {response.status_code}.",
            status=response.status_code,
        )

    @staticmethod
    def _parse_error(response: httpx.Response) -> tuple[str, str | None]:
        try:
            data = response.json()
            err = data.get("error", {})
            return err.get("code", "unknown"), err.get("message")
        except (ValueError, json.JSONDecodeError, AttributeError):
            # Una desconexión o un 502 del proxy puede no incluir JSON.
            return "unknown", None
