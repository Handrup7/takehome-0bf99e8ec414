# Late checkout — prueba técnica WorkFactory

Aplicación local (Python + FastAPI + SQLite) que gestiona solicitudes de
**salida tardía** de un huésped: la IA simulada interpreta la conversación,
la política del hotel sale de los documentos aprobados, la disponibilidad la
da el servicio de reservas, y **solo el responsable del hotel puede aprobar
o rechazar** el cambio real de checkout.

## Qué hace la aplicación

```
 Huésped                  Servidor (esta app)                Servicios remotos
    │  POST /api/messages      │   ┌─ auth: token → identidad  │
    │──────────────────────────>│   ├─ IA simulada: interpreta  │──> LLM mock
    │                           │   │   (salida VALIDADA,       │    (la IA nunca
    │  200: estado + fee       │   │   nunca actúa)             │     actúa)
    │<──────────────────────────│   ├─ políticas (documentos    │
    │                           │   │   aprobados, con cita)    │
    │                           │   ├─ disponibilidad ──────────>──> Reservas mock
    │                           │   └─ guarda propuesta          │    (checkout,
    │                           │       awaiting_approval        │     version,
    │                           │                                │     idempotencia)
    │  GET /api/conversations/{id}  (historial; trazas solo para el responsable)
    │
 Responsable              Servidor
    │  GET /api/proposals   │
    │  POST /{id}/approve   │──> re-comprueba version + disponibilidad
    │  POST /{id}/reject    │    y POST /changes con operation_key estable
    │  POST /{id}/reconcile │    (idempotente; escribe una única vez)
```

- **El huésped** solicita por `POST /api/messages` (multi-turno, deduplicación
  por `event_id`, reutilización de propuestas activas).
- **La IA simulada** solo interpreta; su salida se valida en el servidor
  (`app/llm/interpretation.py`). Un fallo de la IA es un resultado de negocio
  (`needs_input` o `handoff`), nunca un 500.
- **La política y el fee** salen únicamente de `data/documentos.jsonl`
  (política activa y aprobada del hotel, de la versión más alta, con cita de
  evidencia).
- **El cambio real** solo lo escribe el servidor, tras la aprobación del
  responsable, con `operation_key` estable e idempotente. Una escritura incierta
  no se reescribe: se resuelve con `reconcile` consultando la operación.

## Requisitos y arranque

- Python **3.13**
- Los valores de `.env` (tokens del kit) **no se suben al repositorio**.

```bash
# 1. Entorno virtual y dependencias
python -m venv .venv
.venv\Scripts\activate            # Windows  ( Linux/macOS: source .venv/bin/activate )
pip install -r requirements.txt

# 2. Configuración: crea .env en la raíz del proyecto con estas variables
#    (los valores provienen del kit; nunca se versionan)
#      RESERVATIONS_BASE_URL        URL del servicio de reservas (mock)
#      RESERVATIONS_SCENARIO        (opcional, por defecto "normal")
#      RESERVATIONS_GUEST_A1_TOKEN  claves remotas de cada sesión demo:
#      RESERVATIONS_GUEST_A2_TOKEN     guest-a1, guest-a2, guest-b1,
#      RESERVATIONS_GUEST_B1_TOKEN     manager-a, manager-b
#      RESERVATIONS_MANAGER_A_TOKEN
#      RESERVATIONS_MANAGER_B_TOKEN
#      RESERVATIONS_TESTER_TOKEN      solo para los controles de test del kit
#      LLM_BASE_URL                   URL del servicio de IA (mock)
#      LLM_MODEL
#      LLM_SCENARIO                   (opcional, por defecto "normal")
#      LLM_API_KEY
#      LLM_TESTER_TOKEN               solo para los controles de test del kit

# 3. Arranque
python -m uvicorn app.main:app --reload
# Documentación automática: http://127.0.0.1:8000/docs
# Salud:                     http://127.0.0.1:8000/health
```

Tokens de sesión demo (en `data/identidades.json`, provienen del kit):
`guest-a1`, `guest-a2`, `guest-b1`, `manager-a`, `manager-b`. Se usan como
`Authorization: Bearer <token>`.

## Base de datos y reinicio

- SQLite en `late_checkout.db` (raíz del proyecto, fuera del repositorio vía
  `.gitignore`). Una conexión por operación, sin ORM.
- **Reiniciar la base local**: para el servidor y borra `late_checkout.db`
  (en Windows hay que cerrar el servidor antes, el archivo queda abierto).
  Al arrancar, la app la recrea.
- **Reiniciar los servicios remotos**: `POST /test/reset` de cada mock con el
  `RESERVATIONS_TESTER_TOKEN` / `LLM_TESTER_TOKEN` (controles del kit, no se
  exponen a la app). Los probes lo hacen al inicio.

## Verificación (probes propios)

Cada módulo se verificó con un guion propio en `scripts/` contra los
**servicios remotos reales** (no son parte de la app):

```bash
.venv\Scripts\python.exe scripts\llm_probe.py
.venv\Scripts\python.exe scripts\llm_context_probe.py
.venv\Scripts\python.exe scripts\policies_probe.py
.venv\Scripts\python.exe scripts\store_probe.py
.venv\Scripts\python.exe scripts\reservations_probe.py
.venv\Scripts\python.exe scripts\messages_probe.py       # 60/60
.venv\Scripts\python.exe scripts\proposals_probe.py      # 95/95
.venv\Scripts\python.exe scripts\static_probe.py         # 22/22
```

Cada probe empieza con un reinicio limpio (base local + `POST /test/reset`
remoto) y termina imprimiendo un resumen `PASS/FAIL`.

### Resultados de los casos públicos (P01–P10)

> Pendiente: se completará esta sección con la batería de tests automatizados
> (`pytest`) sobre `data/casos-publicos.jsonl` y sus resultados guardados.

## Estructura del proyecto

```
app/
├── main.py            # servidor FastAPI: routers, formato de errores, /health
├── config.py          # carga .env una vez; settings inmutable; USER_AGENT
├── auth.py            # token de sesión -> identidad (rol, hotel, estancia)
├── store.py           # SQLite: conversaciones, mensajes, propuestas, trazas
├── policies.py        # política aprobada activa del hotel + evidencia
├── llm/
│   ├── client.py      # cliente del mock de IA (reintentos, Retry-After)
│   └── interpretation.py  # validación PURA de la salida de la IA (sin red)
├── reservations.py    # cliente del mock de reservas: estancias, disponibilidad,
│                      # cambios idempotentes, operaciones, escrituras inciertas
├── messages.py        # POST /api/messages: dedupe, IA, política, disponibilidad,
│                      # propuesta (reutilización/creación)
├── proposals.py       # GET /api/proposals, approve/reject/reconcile
├── conversations.py   # GET /api/conversations/{id}: historial (+trazas p/ manager)
└── static/            # vistas web demo: elegir sesión, huésped, responsable
data/                  # datos del kit (identidades, documentos, estancias, casos)
scripts/               # probes de verificación (no son la app)
requirements.txt       # FastAPI, uvicorn, httpx, python-dotenv, pytest
```

## Estado del proyecto

- **Completado**: backend completo (identidad, mensajes, propuestas,
  conversaciones, clientes remotos con idempotencia, persistencia) y vistas
  web demo (`app/static/`), con probes propios en verde contra los servicios
  reales.
- **En curso**: batería de tests automatizados P01–P10 con resultados
  guardados, `DECISIONES.md` y guion de la demo de 10 min.

## Tiempo empleado

Máximo permitido: 8 h distribuidas en 5 días.

| Día | Fecha | Tiempo del día | Acumulado | Trabajo |
|---|---|---|---|---|
| 1 | 2026-11-15 | 7 h 20 min | 7 h 20 min | Lectura del enunciado y del kit; módulos setup, auth, LLM, policies, store; repaso y cliente de reservas; flujo completo de `POST /api/messages`; `approve`/`reject`/`reconcile` + historial de conversación (8 commits, todos con probes en verde) |

### Desglose del día 1

- **14:56** – Inicio: lectura del enunciado y del kit (contratos y datos).
- **15:26** – Commit *Setup*: esqueleto FastAPI, dependencias, `.gitignore`.
- **15:59** – Commit *Auth*: identidades demo y formato de errores del contrato.
- **16:16** – Commit *LLM*: cliente de IA y validación de la interpretación.
- **16:24** – Commit *Policies*: resolución de la política aprobada activa.
- **16:34** – Commit *Store*: persistencia SQLite (conversaciones, mensajes,
  propuestas, trazas).
- **16:53–19:16** – Repaso de lo escrito y commit *Reservations*: cliente del
  servicio de reservas con idempotencia y manejo de escrituras inciertas.
- Descanso.
- **22:00–00:00** – Commit *Messages*: flujo completo de `POST /api/messages`
  (identidad, dedupe, IA con historial, validación, política, disponibilidad,
  propuesta).
- **00:00–01:00** – Commit *Proposals*: `approve`/`reject`/`reconcile` con
  revalidación y clave idempotente, + historial de conversación con trazas.

> Esta tabla se actualiza al terminar cada día de trabajo.

## Uso de la IA

La aplicación usa una **IA simulada** (mock del kit) para interpretar los
mensajes; es un componente del sistema, no una herramienta de desarrollo.

Para el **desarrollo** usé un asistente de programación por IA (agente
OpenCode con un modelo local) como *pair programmer*, con un flujo de trabajo
disciplinado:

1. **Yo defino** el alcance de cada módulo (contratos, reglas de negocio,
   estados) y las decisiones de arquitectura.
2. La IA **redacta el borrador** del código y de los probes.
3. **Yo repaso línea a línea** el código y entiendo cada línea antes de
   aceptarlo (sin entenderlo, no se commitea).
4. **Los probes son míos**: los escribí yo para verificar cada módulo contra
   los servicios remotos reales; el kit no incluye tests.
5. Solo con el probe en verde **hago commit y push** (visible en el historial:
   8 módulos, 8 commits, cada uno verificado).

La IA me ayudó además a:

- **Explicar Python** viniendo de Java/Kotlin (analogías con
  `CompletableFuture`, pools de hilos, `MDC`/audit logs).
- **Depurar bugs** (p. ej., un bug silencioso de `request.json()` asíncrona en
  un endpoint síncrono; el orden de parámetros `Response`/`Header` en FastAPI).
- **Documentar**: guion de repaso línea a línea y registro de decisiones para
  la defensa.

El criterio final —qué se commitea, cómo se diseña la máquina de estados, cómo
se gestionan las escrituras inciertas— es mío; la IA es el instrumento, no la
fuente de las decisiones.
