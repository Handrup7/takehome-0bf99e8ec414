# Resultados de los casos públicos (P01–P10)

Generado por `scripts/run_tests_publicos.ps1` el 2026-11-15 13:16.

Entorno: platform win32 -- Python 3.13.17, pytest-9.2.0, pluggy-1.7.0 -- C:\Users\Handr\IA\OpenCode-Workspace\takehome-0bf99e8ec414\.venv\Scripts\python.exe

Método: 8 pasadas de pytest. Los escenarios se fijan en el entorno ANTES de arrancar (los congela `app/config.py` al importar). Cada test de escenario se salta solo si la pasada no tiene su escenario activo; por eso la pasada A muestra SKIPPED que luego salen PASSED en sus pasadas.

Fuente de las aserciones: `data/casos-publicos.jsonl` (con `data/eventos.jsonl` y `data/documentos.jsonl`).

Regenerar: `powershell -ExecutionPolicy Bypass -File scripts\run_tests_publicos.ps1`

## Resultado general

**VERDE:** 17 de 17 tests únicos PASSED, 0 fallos. Cada test se ejecutó en la pasada cuyo escenario necesita.

## Pasada A - Escenario normal

- Entorno: `por defecto (normal)`
- Comando: `python -m pytest tests -v`
- Resumen de pytest: `================== 10 passed, 7 skipped, 1 warning in 18.36s ==================`

| Test | Resultado |
| --- | --- |
| test_p01_datos_incompletos_y_contexto | PASSED |
| test_p02_camino_completo_e_ingles | PASSED |
| test_p03_habitacion_sin_disponibilidad | PASSED |
| test_p04_autoridad_y_contradicion | PASSED |
| test_p04_variante_conflicto | PASSED |
| test_p05_inyeccion_en_mensaje | PASSED |
| test_p06_aislamiento_y_rol | PASSED |
| test_p07_duplicados_y_carrera | PASSED |
| test_p08_respuesta_perdida_y_reinicio | SKIPPED |
| test_p08_variante_transient_503 | SKIPPED |
| test_p09_propuesta_obsoleta | SKIPPED |
| test_p09_variante_bump_corpus | PASSED |
| test_p10_invalid_time | SKIPPED |
| test_p10_malformed_json | SKIPPED |
| test_p10_privilege_fields | SKIPPED |
| test_p10_llm_transient_503 | SKIPPED |
| test_p10_rechazar_y_luego_aprobar | PASSED |

## Pasada B - Reservas response-lost

- Entorno: `RESERVATIONS_SCENARIO=response-lost`
- Comando: `python -m pytest tests -k p08_respuesta_perdida -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 2.33s =================`

| Test | Resultado |
| --- | --- |
| test_p08_respuesta_perdida_y_reinicio | PASSED |

## Pasada C - Reservas transient-503

- Entorno: `RESERVATIONS_SCENARIO=transient-503`
- Comando: `python -m pytest tests -k p08_variante -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 1.90s =================`

| Test | Resultado |
| --- | --- |
| test_p08_variante_transient_503 | PASSED |

## Pasada D - Reservas stale-availability

- Entorno: `RESERVATIONS_SCENARIO=stale-availability`
- Comando: `python -m pytest tests -k p09_propuesta_obsoleta -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 2.31s =================`

| Test | Resultado |
| --- | --- |
| test_p09_propuesta_obsoleta | PASSED |

## Pasada E1 - IA invalid-time

- Entorno: `LLM_SCENARIO=invalid-time`
- Comando: `python -m pytest tests -k p10_invalid_time -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 0.78s =================`

| Test | Resultado |
| --- | --- |
| test_p10_invalid_time | PASSED |

## Pasada E2 - IA malformed-json

- Entorno: `LLM_SCENARIO=malformed-json`
- Comando: `python -m pytest tests -k p10_malformed_json -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 0.81s =================`

| Test | Resultado |
| --- | --- |
| test_p10_malformed_json | PASSED |

## Pasada E3 - IA privilege-fields

- Entorno: `LLM_SCENARIO=privilege-fields`
- Comando: `python -m pytest tests -k p10_privilege_fields -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 1.23s =================`

| Test | Resultado |
| --- | --- |
| test_p10_privilege_fields | PASSED |

## Pasada E4 - IA transient-503

- Entorno: `LLM_SCENARIO=transient-503`
- Comando: `python -m pytest tests -k p10_llm_transient_503 -v`
- Resumen de pytest: `================= 1 passed, 16 deselected, 1 warning in 2.38s =================`

| Test | Resultado |
| --- | --- |
| test_p10_llm_transient_503 | PASSED |

## Resumen por grupo de casos

| Caso | Tests (pasada) | Resultado |
| --- | --- | --- |
| P01 - Datos incompletos y contexto | A: test_p01_datos_incompletos_y_contexto (PASSED) | VERDE |
| P02 - Camino completo e inglés | A: test_p02_camino_completo_e_ingles (PASSED) | VERDE |
| P03 - Habitación sin disponibilidad | A: test_p03_habitacion_sin_disponibilidad (PASSED) | VERDE |
| P04 - Autoridad y contradicción | A: test_p04_autoridad_y_contradicion (PASSED); A: test_p04_variante_conflicto (PASSED) | VERDE |
| P05 - Inyección en mensaje | A: test_p05_inyeccion_en_mensaje (PASSED) | VERDE |
| P06 - Aislamiento y rol | A: test_p06_aislamiento_y_rol (PASSED) | VERDE |
| P07 - Duplicados y carrera | A: test_p07_duplicados_y_carrera (PASSED) | VERDE |
| P08 - Resultado incierto y reinicio | B: test_p08_respuesta_perdida_y_reinicio (PASSED); C: test_p08_variante_transient_503 (PASSED); 2 tests SKIPPED en A (cubiertos en su pasada) | VERDE |
| P09 - Propuesta obsoleta | A: test_p09_variante_bump_corpus (PASSED); D: test_p09_propuesta_obsoleta (PASSED); 1 tests SKIPPED en A (cubiertos en su pasada) | VERDE |
| P10 - Faltas de la IA simulada | A: test_p10_rechazar_y_luego_aprobar (PASSED); E1: test_p10_invalid_time (PASSED); E2: test_p10_malformed_json (PASSED); E3: test_p10_privilege_fields (PASSED); E4: test_p10_llm_transient_503 (PASSED); 4 tests SKIPPED en A (cubiertos en su pasada) | VERDE |

> Nota: en la pasada A, los tests con escenario (P08, P09, P10-IA) se saltan (SKIPPED); su resultado real aparece en sus pasadas correspondientes (B-D, E1-E4).

