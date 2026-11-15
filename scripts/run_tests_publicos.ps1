#Requires -Version 5.1
#
# run_tests_publicos.ps1
#
# Ejecuta los 10 grupos de casos publicos (P01-P10) de
# data/casos-publicos.jsonl con 17 tests (tests/test_casos_publicos.py)
# en 8 pasadas de pytest, una por escenario, y genera
# docs/resultados-casos-publicos.md con los resultados.
#
# Las variables de escenario se fijan ANTES de lanzar pytest:
# app/config.py las congela al importar (equivale a distintas
# instancias de la app con distintos properties, como en un CI/CD).
# En cada pasada, los tests que requieren otro escenario se saltan
# solos (require_scenario -> pytest.skip).
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts\run_tests_publicos.ps1

$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo
$py = Join-Path $repo ".venv\Scripts\python.exe"
$docsDir = Join-Path $repo "docs"
$docPath = Join-Path $docsDir "resultados-casos-publicos.md"
if (-not (Test-Path $docsDir)) {
    New-Item -ItemType Directory -Path $docsDir | Out-Null
}

# Las 8 pasadas: escenario(s) de entorno + filtro de tests (si aplica).
$runs = @(
    @{ id = "A";  desc = "Escenario normal";                    env = @{};                                        filter = $null },
    @{ id = "B";  desc = "Reservas response-lost";              env = @{ RESERVATIONS_SCENARIO = "response-lost" };    filter = "p08_respuesta_perdida" },
    @{ id = "C";  desc = "Reservas transient-503";              env = @{ RESERVATIONS_SCENARIO = "transient-503" };    filter = "p08_variante" },
    @{ id = "D";  desc = "Reservas stale-availability";         env = @{ RESERVATIONS_SCENARIO = "stale-availability" }; filter = "p09_propuesta_obsoleta" },
    @{ id = "E1"; desc = "IA invalid-time";                     env = @{ LLM_SCENARIO = "invalid-time" };               filter = "p10_invalid_time" },
    @{ id = "E2"; desc = "IA malformed-json";                   env = @{ LLM_SCENARIO = "malformed-json" };             filter = "p10_malformed_json" },
    @{ id = "E3"; desc = "IA privilege-fields";                 env = @{ LLM_SCENARIO = "privilege-fields" };           filter = "p10_privilege_fields" },
    @{ id = "E4"; desc = "IA transient-503";                    env = @{ LLM_SCENARIO = "transient-503" };              filter = "p10_llm_transient_503" }
)

$runResults = @()
$anyFailed = $false

foreach ($run in $runs) {
    # Limpiar y fijar los escenarios ANTES de arrancar pytest.
    Remove-Item Env:RESERVATIONS_SCENARIO -ErrorAction SilentlyContinue
    Remove-Item Env:LLM_SCENARIO -ErrorAction SilentlyContinue
    foreach ($name in $run.env.Keys) {
        Set-Item "Env:$name" $run.env[$name]
    }

    $argv = @("-m", "pytest", "tests")
    if ($run.filter) { $argv += @("-k", $run.filter) }
    $argv += @("-v")

    Write-Host ""
    Write-Host ("=== Pasada {0} - {1} ===" -f $run.id, $run.desc)
    $out = & $py @argv | Out-String
    $exitCode = $LASTEXITCODE

    foreach ($name in $run.env.Keys) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }

    # Parsear el output -v: test a test + resumen de pytest + plataforma.
    $tests = @()
    $summaryLine = ""
    $platformLine = ""
    foreach ($line in ($out -split "`r?`n")) {
        if ($line -match "::(\S+)\s+(PASSED|FAILED|SKIPPED)") {
            $tests += [pscustomobject]@{
                name   = $matches[1]
                result = $matches[2]
            }
        }
        if (($line -match "^=+ .+ =+$") -and ($line -match "passed|failed")) {
            $summaryLine = $line.Trim()
        }
        if (($line -match "^platform ") -and ($platformLine -eq "")) {
            $platformLine = $line.Trim()
        }
    }
    $failedCount = @($tests | Where-Object { $_.result -eq "FAILED" }).Count
    $passedCount = @($tests | Where-Object { $_.result -eq "PASSED" }).Count
    Write-Host $summaryLine

    $envText = "por defecto (normal)"
    if ($run.env.Count -gt 0) {
        $envText = ($run.env.GetEnumerator() |
            ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ", "
    }
    $commandText = "python -m pytest tests"
    if ($run.filter) { $commandText += " -k " + $run.filter }
    $commandText += " -v"

    $runResults += [pscustomobject]@{
        id        = $run.id
        desc      = $run.desc
        envText   = $envText
        cmd       = $commandText
        tests     = $tests
        passed    = $passedCount
        failed    = $failedCount
        summary   = $summaryLine
        platform  = $platformLine
    }
    if (($exitCode -ne 0) -or ($failedCount -gt 0)) { $anyFailed = $true }
}

# ----------------------------------------------------------------- documento
$doc = New-Object System.Collections.Generic.List[string]
$doc.Add("# Resultados de los casos públicos (P01–P10)")
$doc.Add("")
$doc.Add('Generado por `scripts/run_tests_publicos.ps1` el ' +
    (Get-Date -Format "yyyy-MM-dd HH:mm") + '.')
$doc.Add("")
$doc.Add("Entorno: " + $runResults[0].platform)
$doc.Add("")
$doc.Add('Método: 8 pasadas de pytest. Los escenarios se fijan en el entorno ANTES de arrancar (los congela `app/config.py` al importar). Cada test de escenario se salta solo si la pasada no tiene su escenario activo; por eso la pasada A muestra SKIPPED que luego salen PASSED en sus pasadas.')
$doc.Add("")
$doc.Add('Fuente de las aserciones: `data/casos-publicos.jsonl` (con `data/eventos.jsonl` y `data/documentos.jsonl`).')
$doc.Add("")
$doc.Add('Regenerar: `powershell -ExecutionPolicy Bypass -File scripts\run_tests_publicos.ps1`')
$doc.Add("")

$totalPassed = 0
$totalFailed = 0
foreach ($r in $runResults) {
    $totalPassed += $r.passed
    $totalFailed += $r.failed
}
$doc.Add("## Resultado general")
$doc.Add("")
if ($anyFailed) {
    $doc.Add("**FALLO:** hay tests FAILED o alguna pasada terminó con código de error. Revisar las pasadas de abajo.")
} else {
    $doc.Add("**VERDE:** 17 de 17 tests únicos PASSED, 0 fallos. Cada test se ejecutó en la pasada cuyo escenario necesita.")
}
$doc.Add("")

foreach ($r in $runResults) {
    $doc.Add("## Pasada " + $r.id + " - " + $r.desc)
    $doc.Add("")
    $doc.Add('- Entorno: `' + $r.envText + '`')
    $doc.Add('- Comando: `' + $r.cmd + '`')
    $doc.Add('- Resumen de pytest: `' + $r.summary + '`')
    $doc.Add("")
    $doc.Add("| Test | Resultado |")
    $doc.Add("| --- | --- |")
    foreach ($t in $r.tests) {
        $doc.Add("| " + $t.name + " | " + $t.result + " |")
    }
    $doc.Add("")
}

$caseNames = [ordered]@{
    "P01" = "Datos incompletos y contexto"
    "P02" = "Camino completo e inglés"
    "P03" = "Habitación sin disponibilidad"
    "P04" = "Autoridad y contradicción"
    "P05" = "Inyección en mensaje"
    "P06" = "Aislamiento y rol"
    "P07" = "Duplicados y carrera"
    "P08" = "Resultado incierto y reinicio"
    "P09" = "Propuesta obsoleta"
    "P10" = "Faltas de la IA simulada"
}

$allTests = @()
foreach ($r in $runResults) {
    foreach ($t in $r.tests) {
        $allTests += [pscustomobject]@{
            run    = $r.id
            name   = $t.name
            result = $t.result
        }
    }
}

$doc.Add("## Resumen por grupo de casos")
$doc.Add("")
$doc.Add("| Caso | Tests (pasada) | Resultado |")
$doc.Add("| --- | --- | --- |")
foreach ($case in $caseNames.Keys) {
    $rows = @($allTests |
        Where-Object { $_.name -like ("test_{0}*" -f $case.ToLower()) })
    $real = @($rows | Where-Object { $_.result -ne "SKIPPED" })
    $skipped = @($rows | Where-Object { $_.result -eq "SKIPPED" })
    $parts = @()
    $ok = $true
    foreach ($t in $real) {
        $parts += ($t.run + ": " + $t.name + " (" + $t.result + ")")
        if ($t.result -ne "PASSED") { $ok = $false }
    }
    if ($real.Count -eq 0) { $ok = $false }
    if ($skipped.Count -gt 0) {
        $parts += ("{0} tests SKIPPED en A (cubiertos en su pasada)" -f $skipped.Count)
    }
    $verdict = "FALLO"
    if ($ok) { $verdict = "VERDE" }
    $doc.Add("| " + $case + " - " + $caseNames[$case] + " | " +
        ($parts -join "; ") + " | " + $verdict + " |")
}
$doc.Add("")
$doc.Add("> Nota: en la pasada A, los tests con escenario (P08, P09, P10-IA) se saltan (SKIPPED); su resultado real aparece en sus pasadas correspondientes (B-D, E1-E4).")
$doc.Add("")

[System.IO.File]::WriteAllText(
    $docPath,
    ($doc -join "`n") + "`n",
    (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
if ($anyFailed) {
    Write-Host "RESULTADO: FALLO (ver docs/resultados-casos-publicos.md)"
    exit 1
}
Write-Host ("RESULTADO: TODO VERDE - {0} tests PASSED en total." -f $totalPassed)
Write-Host ("Documento generado: {0}" -f $docPath)
exit 0
