"""Cliente HTTP para el servicio de reservas de WorkFactory.

Responsabilidades:
- traducir la sesión de la app (guest-a1, manager-a, ...) a la clave
  remota correspondiente (las claves nunca salen del servidor),
- leer la estancia vigente y comprobar disponibilidad,
- aplicar un cambio de checkout (POST /changes) con su operation_key,
- consultar una operación (GET /operations/{key}) para reconciliar
  escrituras inciertas.

Decisiones documentadas (DECISIONES.md):
- Las lecturas son idempotentes: se reintentan de forma limitada ante un
  fallo transitorio (timeout, red, 503).
- Las escrituras NO se reintentan de forma ciega. Solo el 503
  `simulated_rejection` garantiza que la petición NO escribió, y ese es el
  único caso reintentable con la misma clave y contenido. Cualquier otro
  fallo al escribir (timeout, desconexión, 502, 503 genérico) se declara
  INCERTO (ambiguous) y se resuelve consultando GET /operations/{key},
  nunca con una segunda escritura a ciegas ni con otra clave.
- Las fechas se envían en el formato exacto que exige el contrato:
  YYYY-MM-DDTHH:MM:SSZ.
- El escenario (normal, transient-503, response-lost, stale-availability)
  lo fija el servidor desde .env; el huésped y la IA no pueden elegirlo.
"""

import time

import httpx

from .config import USER_AGENT, settings

# Según el contrato, todas las horas de prueba son del 10 de noviembre
# de 2026, UTC.
CHECKOUT_DATE = "2026-11-10"


def full_checkout_at(time_hhmm: str) -> str:
    """Convierte '14:00' en '2026-11-10T14:00:00Z' (formato exacto del contrato)."""
    return f"{CHECKOUT_DATE}T{time_hhmm}:00Z"


class ReservationClientError(Exception):
    """Fallo del servicio de reservas o de la conexión.

    retryable:   True si el caller puede reintentar: el fallo es transitorio
                 y repetir la petición es seguro.
    ambiguous:   True si es una escritura INCERTA: el cambio pudo o no
                 aplicarse. No se reintenta; se resuelve consultando
                 GET /operations/{key}.
    retry_after: segundos que indica la cabecera Retry-After, si existe.
    """

    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        retryable: bool = False,
        ambiguous: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


class ReservationsClient:
    """Habla con el servicio de reservas remoto.

    La sesión determina la clave: las rutas de negocio no aceptan
    hotel_id, org_id ni role en el cuerpo; todo se deriva de la clave
    elegida por el servidor.
    """

    def __init__(
        self,
        base_url: str | None = None,
        scenario: str | None = None,
        max_retries: int = 3,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or settings.reservations_base_url).rstrip("/")
        self.scenario = scenario or settings.reservations_scenario
        self.max_retries = max_retries
        self.timeout = timeout

    # --------------------------------------------------------- credenciales
    def _credential(self, session: str) -> str:
        token = settings.reservations_credentials.get(session)
        if token is None:
            raise ReservationClientError(
                "invalid_session",
                f"La sesión {session} no tiene clave remota asignada.",
            )
        return token

    def _headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            # El escenario lo fija el servidor, nunca el huésped ni la IA.
            "X-Test-Scenario": self.scenario,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------- negocio
    def get_stay(self, session: str, stay_id: str) -> dict:
        """Estado vigente de la estancia (checkout_at y version)."""
        token = self._credential(session)
        return self._request(token, "GET", f"/stays/{stay_id}", is_write=False)

    def check_availability(
        self, session: str, stay_id: str, requested_checkout_at: str
    ) -> dict:
        """¿Hay disponibilidad para la hora pedida? (consultar no reserva)."""
        token = self._credential(session)
        body = {"stay_id": stay_id, "requested_checkout_at": requested_checkout_at}
        return self._request(
            token, "POST", "/availability/check", body, is_write=False
        )

    def apply_change(
        self,
        session: str,
        stay_id: str,
        requested_checkout_at: str,
        expected_version: int,
        operation_key: str,
    ) -> dict:
        """Aplica el cambio de checkout (solo responsable). Devuelve el recibo.

        No reintenta de forma ciega: solo el 503 `simulated_rejection`
        garantiza que no escribió y es el único caso reintentable.
        """
        token = self._credential(session)
        body = {
            "stay_id": stay_id,
            "requested_checkout_at": requested_checkout_at,
            "expected_version": expected_version,
            "operation_key": operation_key,
        }
        return self._request(token, "POST", "/changes", body, is_write=True)

    def get_operation(self, session: str, operation_key: str) -> dict:
        """Reconciliación: consulta una operación (solo las aplicadas)."""
        token = self._credential(session)
        return self._request(
            token, "GET", f"/operations/{operation_key}", is_write=False
        )

    # ---------------------------------------------------------- transporte
    def _request(
        self,
        token: str,
        method: str,
        path: str,
        body: dict | None = None,
        is_write: bool = False,
    ) -> dict:
        """Bucle de reintentos limitado (máximo max_retries intentos)."""
        last: ReservationClientError | None = None
        for attempt in range(self.max_retries):
            try:
                return self._send_once(token, method, path, body, is_write)
            except ReservationClientError as err:
                last = err
                if not err.retryable:
                    raise
                if attempt == self.max_retries - 1:
                    break
                if err.retry_after is not None:
                    time.sleep(err.retry_after)
        raise last

    def _send_once(
        self,
        token: str,
        method: str,
        path: str,
        body: dict | None = None,
        is_write: bool = False,
    ) -> dict:
        url = f"{self.base_url}{path}"
        headers = self._headers(token)
        try:
            if method == "GET":
                response = httpx.get(url, headers=headers, timeout=self.timeout)
            else:
                response = httpx.post(
                    url, json=body, headers=headers, timeout=self.timeout
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            # No hubo respuesta. Lectura: transitorio (reintentable).
            # Escritura: INCERTA (pudo o no aplicarse).
            raise ReservationClientError(
                "connection_error",
                "No se pudo conectar con el servicio de reservas.",
                retryable=not is_write,
                ambiguous=is_write,
            )

        if response.status_code in (200, 201):
            try:
                return response.json()
            except ValueError:
                raise ReservationClientError(
                    "invalid_response", "cuerpo no-JSON", status=response.status_code
                )

        code, message = self._parse_error(response)
        status = response.status_code

        if is_write:
            if status == 503 and code == "simulated_rejection":
                # Único fallo de escritura que garantiza que NO escribió:
                # se puede reintentar con la misma clave y contenido.
                raise ReservationClientError(
                    code,
                    message or "El servicio rechazó temporalmente la escritura.",
                    status=503,
                    retryable=True,
                    retry_after=self._retry_after(response),
                )
            if status in (502, 503):
                # Un 502 o un 503 genérico no demuestran que no se aplicó:
                # la escritura queda INCERTA. No se reintenta.
                raise ReservationClientError(
                    code,
                    message or f"Escritura incierta: HTTP {status}.",
                    status=status,
                    ambiguous=True,
                )
        else:
            if status == 503:
                # Lectura: cualquier 503 es transitorio y reintentable.
                raise ReservationClientError(
                    code,
                    message or "Servicio temporalmente no disponible.",
                    status=503,
                    retryable=True,
                    retry_after=self._retry_after(response),
                )

        # Errores deterministas (401, 403, 404, 409, 422, ...): no reintentable.
        raise ReservationClientError(
            code,
            message or f"El servicio de reservas respondió HTTP {status}",
            status=status,
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        header = response.headers.get("Retry-After")
        if not header:
            return None
        try:
            return float(header)
        except ValueError:
            return None

    @staticmethod
    def _parse_error(response: httpx.Response) -> tuple[str, str | None]:
        try:
            data = response.json()
            err = data.get("error", {})
            return err.get("code", "unknown"), err.get("message")
        except (ValueError, AttributeError):
            return "unknown", None


# Instancia compartida por la aplicación
reservations = ReservationsClient()
