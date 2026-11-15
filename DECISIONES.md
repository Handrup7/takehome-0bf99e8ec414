# DECISIONES

Según el enunciado: tres decisiones técnicas, la ayuda de IA utilizada y
dos fallos reproducidos y corregidos.

## Decisiones técnicas

### 1. SQLite plano sin ORM, una conexión por operación

**Decisión.** Persistencia en un único archivo `late_checkout.db` con
`sqlite3` de la stdlib (sin SQLAlchemy ni otro ORM); cada método de
`Store` (`app/store.py`) abre su propia conexión y la usa solo para esa
operación.

**Por qué.**

- El alcance es pequeño: 6 tablas y decenas de filas por conversación. Un
  motor con servidor propio (Postgres/MySQL) añadiría coste de operación
  sin beneficio; un solo archivo sobrevive reinicios y se resetea fácil
  (el enunciado pide instrucciones para restablecer el entorno).
- El SQL directo es más legible y más fácil de defender que una capa de
  ORM: cada consulta se ve tal cual en el código y en las trazas.
- Una conexión por operación evita gestionar hilos y pools: a este
  volumen el coste de abrir conexiones SQLite locales es despreciable.

**Coste aceptado.** Si la concurrencia creciera, bastaría sustituir
`Store` por un cliente con pool o un motor con servidor; es el único
módulo que toca la persistencia.

### 2. La IA nunca actúa: interpretación pura + validación en el servidor

**Decisión.** La IA (simulada o real) solo *interpreta* los mensajes. Su
salida se valida en `app/llm/interpretation.py` (función pura, sin red) y
los datos de negocio **nunca salen de lo que el modelo "crea"**:

- rol, hotel y estancia salen del token de sesión (`auth.py`), no del body;
- fee y política salen únicamente de documentos aprobados
  (`app/policies.py`, con cita de evidencia), no de lo que diga el modelo;
- la disponibilidad la da el servicio de reservas, no el modelo.

Un fallo de la IA no es un 500: es un **resultado de negocio** —
`needs_input` (hora inválida: se pide al huésped una hora nueva) o
`handoff` (salida malformada, inyección, conflicto, etc.).

**Por qué.** Un LLM puede alucinar: inventar horas, añadir campos o
"conceder" privilegios (los casos P05 y P10 del kit lo simulan
exactamente). El servidor trata a la IA como un servicio externo no
confiable y valida su salida como validaría el JSON de una API de un
tercero.

**Coste aceptado.** Rigidez: si el mock cambia el formato de salida hay
que actualizar `interpretation.py`. A cambio, determinismo: la validación
se puede probar sin red.

### 3. Concurrencia optimista: revalidar antes de escribir + idempotencia con `operation_key` estable

**Decisión.** `approve` (`app/proposals.py`) no escribe a ciegas. Antes
del `POST /changes` revalida, en orden:

1. **versión de la estancia** — ¿ha cambiado la estancia desde la propuesta?
2. **disponibilidad** — ¿la sala sigue libre?
3. **política vigente** — ¿el documento aprobado es la misma
   `(document_id, version)` que cuando se propuso?

Si algo cambió → `409 stale`: estado terminal pero **esperado**, no un
fallo; la propuesta caducó y el huésped puede volver a pedirla.

La escritura usa una **`operation_key` estable por propuesta**: si el
servicio de reservas recibe la misma clave con el mismo contenido,
devuelve el receipt original sin reescribir; si el contenido difiere es
conflicto. Un fallo de escritura que deja el resultado incierto
(`ambiguous`) **nunca se reescribe a ciegas**: la propuesta pasa a
`pending_verification` y se resuelve con `reconcile`, que consulta la
operación.

**Por qué.** El contrato exige que el cambio real se escriba **una única
vez** incluso con carreras y respuestas perdidas. Revalidar en el momento
de escribir es un bloqueo optimista: en Java equivale al `@Version` de
JPA y lanzar `OptimisticLockingFailureException` — se comprueba al
escribir que la versión que leí sigue siendo la vigente.

**Coste aceptado.** Una llamada remota extra por aprobación; a cambio,
no hay escrituras duplicadas ni aprobaciones sobre datos obsoletos.

## Ayuda de IA utilizada

Usé un agente de programación (OpenCode con un modelo local) como
*pair programmer*, con un flujo disciplinado:

1. **Yo defino** el alcance de cada módulo, los contratos, la máquina de
   estados y las decisiones de arquitectura (las de este archivo).
2. La IA **redacta el borrador** del código y de los probes.
3. **Yo repaso línea a línea** y solo commiteo lo que entiendo
   (8 módulos, 8 commits, cada uno precedido por su probe en verde).
4. **Los probes son míos** (`scripts/*_probe.py`): verifican cada módulo
   contra los servicios remotos reales; el kit no trae tests.
5. Los tests automatizados (`tests/`, 17 tests sobre los 10 grupos de
   casos públicos) y el script de ejecución `scripts/run_tests_publicos.ps1`
   se escribieron para reproducir los casos del kit; los resultados,
   incluidos los fallos, están guardados en
   [`docs/resultados-casos-publicos.md`](docs/resultados-casos-publicos.md).

Además ayudó a: explicar Python viniendo de Java/Kotlin (analogías con
`CompletableFuture`, pools de hilos, `MDC`), depurar bugs concretos
(`request.json()` asíncrona en un endpoint síncrono; orden de parámetros
`Response`/`Header` en FastAPI) y documentar el proyecto.

El criterio final —qué se commitea, cómo se diseña la máquina de estados,
cómo se gestionan las escrituras inciertas— es mío; la IA es el
instrumento, no la fuente de las decisiones.

## Fallos reproducidos y corregidos

### Fallo 1 — Resetear la base de datos borrando el archivo

**Qué hacía.** Al desarrollar probes y tests, intentaba resetear la base
local borrando `late_checkout.db`.

**Reproducción (salida exacta):**

```text
1) antes de borrar, traza tr-1: 1 eventos
2) ERROR A (archivo en uso): PermissionError WinError 32
2b) archivo borrado (handles ya liberados por el GC)
3) ERROR B (esquema perdido): no such table: traces
4) tras reinicio, traza tr-2: 1 eventos, esquema OK
```

**Qué pasaba.**

- **A.** En Windows, con la app viva, borrar el archivo falla con
  `PermissionError [WinError 32]`: cada método de `Store` abre una
  conexión que no cierra explícitamente; el handle lo libera el
  *garbage collector*, y hasta entonces el archivo queda bloqueado.
- **B.** Si el archivo se borra de todas formas (otro terminal, otro SO,
  o tras liberar los handles), el objeto `Store` sigue vivo y cada
  `sqlite3.connect()` **recrea en silencio un archivo vacío, sin
  esquema**: la siguiente consulta falla con
  `sqlite3.OperationalError: no such table: traces`.

**Corrección.**

- `Store.reset()`: vacía todas las tablas **in situ**, sin tocar el
  archivo. El fixture de tests (`tests/conftest.py`) y los probes lo usan
  en lugar de borrar el archivo.
- `Store.__init__` crea el esquema con `CREATE TABLE IF NOT EXISTS`: un
  reinicio real de la app siempre arranca limpio.
- README: para resetear la base local a mano, primero detener el
  servidor (en Windows el archivo queda abierto) y después borrar
  `late_checkout.db`.

### Fallo 2 — Parchear la política para los tests (recursión infinita)

**Qué hacía.** Las variantes de P04 y P09 sustituyen el corpus
(`data/documentos.jsonl`) en memoria mediante monkeypatch de
`policies.load_documents`. La primera versión del parche hacía:

```python
def load_documents_rotos():
    return policies.load_documents()   # BUG

policies.load_documents = load_documents_rotos
```

**Reproducción (salida exacta):**

```text
ERROR reproducido: maximum recursion depth exceeded
```

**Qué pasaba.** En Python el nombre se resuelve **en el momento de la
llamada**: tras el parche, `policies.load_documents` apunta al propio
parche, así que el "wrapper" se llama a sí mismo en bucle infinito.

**Corrección.** Guardar la referencia original **antes** de parchear y
que el wrapper llame a esa referencia (patrón final en
`tests/test_casos_publicos.py`):

```python
original_load = policies.load_documents          # ANTES de parchear

def load_documents_con_conflicto():
    docs = original_load()                       # llama al original
    docs.append({...})                           # variante del corpus
    return docs

monkeypatch.setattr(policies, "load_documents", load_documents_con_conflicto)
```

`monkeypatch.setattr` garantiza además la restauración al terminar el
test.
