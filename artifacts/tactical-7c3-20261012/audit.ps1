$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$repo = [IO.Path]::GetFullPath((Join-Path $root '../..'))
function Read-Json($path) { Get-Content -Raw -LiteralPath $path | ConvertFrom-Json -AsHashtable }
function Require($condition, $message) { if (-not $condition) { throw $message } }
function Tape-Hash($rows) {
    $canonicalRows = @()
    foreach ($row in $rows) {
        $keys = [string[]]@($row.Keys)
        [Array]::Sort($keys, [StringComparer]::Ordinal)
        $orderedRow = [ordered]@{}
        foreach ($key in $keys) { $orderedRow[$key] = $row[$key] }
        $canonicalRows += $orderedRow
    }
    $canonical = ($canonicalRows | ConvertTo-Json -Depth 8).Replace("`r`n", "`n") + "`n"
    [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($canonical))).ToLowerInvariant()
}
function Delta($before, $after) { 100 * ($after / $before - 1) }
$validated = Read-Json (Join-Path $root 'validated-source.json')
foreach ($entry in $validated.GetEnumerator()) {
    Require ((Get-FileHash -LiteralPath (Join-Path $repo $entry.Key)).Hash.ToLowerInvariant() -eq $entry.Value) "Validated source changed: $($entry.Key)"
}
$sources = @(Get-ChildItem -LiteralPath $root -Directory | Where-Object { $_.Name -like 'verify-*' -or $_.Name -like 'measure-*' })
Require ($sources.Count -eq 8) 'Expected six verification and two measurement sources'
$baselineSource = Read-Json (Join-Path $sources[0].FullName 'source.json')
foreach ($source in $sources) {
    $data = Read-Json (Join-Path $source.FullName 'source.json')
    Require ($data.reference_methods_ast_verified -and $data.reference_head -eq '3a4dc2c24db47af18f408ab6c6b0e29dccafe928') 'Wrong reference'
    foreach ($section in @('files', 'tools_7c')) {
        Require ($data[$section].Count -eq $baselineSource[$section].Count) "Source catalog changed: $($source.Name)"
        foreach ($entry in $data[$section].GetEnumerator()) {
            Require ($entry.Value -eq $baselineSource[$section][$entry.Key]) "Run source mismatch: $($entry.Key)"
        }
    }
}
foreach ($section in @('files', 'tools_7c')) {
    foreach ($entry in $baselineSource[$section].GetEnumerator()) {
        Require ((Get-FileHash -LiteralPath (Join-Path $repo $entry.Key)).Hash.ToLowerInvariant() -eq $entry.Value) "Recorded source changed: $($entry.Key)"
    }
}
$cases = @('guns-4v4','mixed-4v4','mixed-4v9','mixed-4v9-reversed','guns-9v9','mixed-9v9')
$verified = @()
foreach ($case in $cases) {
    $report = Read-Json (Join-Path $root "verify-$case/verification.json")
    $tape = Read-Json (Join-Path $root "verify-$case/expanded-tape.json")
    $previous = Read-Json (Join-Path $repo "artifacts/tactical-7c2-20261012/verify-$case/verification.json")
    $hash = Tape-Hash $tape
    Require ($report.steps -eq 900 -and $tape.Count -eq 900 -and $report.sections.Count -eq 18) "Incomplete comparison: $case"
    Require ($hash -eq $report.expanded_tape_sha256 -and $hash -eq $previous.expanded_tape_sha256) "Tape differs: $case"
    Require ($report.fixture_sha256 -eq $previous.fixture_sha256) "Fixture changed: $case"
    $verified += [ordered]@{ case=$case; steps=900; sections=18; expanded_tape_sha256=$hash; matches_7c2=$true; counters=$report.counters; late_601_660=$report.late_601_660 }
}
$timings = @()
foreach ($case in @('guns-9v9','mixed-9v9')) {
    $rows = Read-Json (Join-Path $root "measure-$case/measurements.json")
    $tape = Read-Json (Join-Path $root "verify-$case/expanded-tape.json")
    $verifiedCase = Read-Json (Join-Path $root "verify-$case/verification.json")
    $previousMeasured = Read-Json (Join-Path $repo "artifacts/tactical-7c2-20261012/measure-$case/1-candidate.json")
    Require ($rows.Count -eq 6) "Incomplete measurement: $case"
    foreach ($row in $rows) {
        Require ($row.steps -eq 900 -and $row.physics_caches_cleared) "Incomplete measured run: $case"
        Require ($row.fixture_sha256 -eq $verifiedCase.fixture_sha256 -and $row.authority_tape_sha256 -eq $rows[0].authority_tape_sha256) "Measurement input/state differs: $case"
        Require ($row.authority_tape_sha256 -eq $previousMeasured.authority_tape_sha256) "Measured authority differs from 7c.2: $case"
        foreach ($entry in $tape[-1].GetEnumerator()) { Require ($row.expanded_final[$entry.Key] -eq $entry.Value) "Measured final state differs: $case/$($entry.Key)" }
        foreach ($entry in $verifiedCase.final_load.GetEnumerator()) { Require ($row.final_load[$entry.Key] -eq $entry.Value) "Measured load differs: $case/$($entry.Key)" }
    }
    foreach ($pair in 1..3) {
        $a = $rows | Where-Object { $_.pair -eq $pair -and $_.variant -eq 'reference' }
        $b = $rows | Where-Object { $_.pair -eq $pair -and $_.variant -eq 'candidate' }
        $timings += [ordered]@{ case=$case; pair=$pair; reference_step=$a.step; candidate_step=$b.step; mean_change_pct=(Delta $a.step.mean_ms $b.step.mean_ms); p95_change_pct=(Delta $a.step.p95_ms $b.step.p95_ms); service_wall_change_pct=(Delta $a.service_wall_s $b.service_wall_s); service_cpu_change_pct=(Delta $a.service_cpu_s $b.service_cpu_s); reference_service_wall_s=$a.service_wall_s; candidate_service_wall_s=$b.service_wall_s; reference_publish=$a.publish; candidate_publish=$b.publish; reference_read=$a.read; candidate_read=$b.read }
    }
}
$before = Read-Json (Join-Path $root 'before-hotspots-mixed-9v9/hotspots.json')
$after = Read-Json (Join-Path $root 'after-hotspots-mixed-9v9/hotspots.json')
Require ($before.matched_steps -eq 900 -and $after.matched_steps -eq 900 -and $before.authority_tape_sha256 -eq $after.authority_tape_sha256) 'Hotspot replay mismatch'
$afterSource = Read-Json (Join-Path $root 'after-hotspots-mixed-9v9/source.json')
foreach ($entry in $afterSource.files.GetEnumerator()) { Require ($entry.Value -eq $baselineSource.files[$entry.Key]) "Final profiler source differs: $($entry.Key)" }
Require ($afterSource.attribution_tool_sha256 -eq $validated['tools/profile_tactical_7c_hotspots.py']) 'Profiler tool differs'
$supplement = Read-Json (Join-Path $root 'interleaved-mixed-9v9/interleaved.json')
$supplementSource = Read-Json (Join-Path $root 'interleaved-mixed-9v9/source.json')
Require ($supplement.steps -eq 900 -and $supplement.fixture_sha256 -eq $after.fixture_sha256) 'Supplement input/count differs'
Require ($supplementSource.runner_sha256 -eq (Get-FileHash -LiteralPath (Join-Path $root 'supplement_interleaved.py')).Hash.ToLowerInvariant()) 'Supplement runner changed'
Require ($supplementSource.reference_methods_ast_verified -and $supplementSource.reference_head -eq $baselineSource.reference_head) 'Supplement reference differs'
foreach ($section in @('files', 'tools_7c')) {
    foreach ($entry in $supplementSource[$section].GetEnumerator()) { Require ($entry.Value -eq $baselineSource[$section][$entry.Key]) "Supplement source differs: $($entry.Key)" }
}
$supplement['mean_change_pct'] = Delta $supplement.variants.reference.step.mean_ms $supplement.variants.candidate.step.mean_ms
$supplement['p95_change_pct'] = Delta $supplement.variants.reference.step.p95_ms $supplement.variants.candidate.step.p95_ms
$summary = [ordered]@{ date='2026-10-12'; reference_head=$baselineSource.reference_head; validated_source_files=$validated.Count; current_source_catalog_files=$baselineSource.files.Count; current_tools_7c=$baselineSource.tools_7c.Count; identical_run_sources=$sources.Count; all_recorded_source_hashes_match=$true; verification=$verified; measurements=$timings; supplementary_interleaved=$supplement; final_profile_matches_900_steps=$true; unique_tests=134; scope='Final missile-only production change. Collision trial archived separately. Positive percent means slower. Formal measurements ran serially after diagnostics finished. No real-time/native/full-battle acceptance.' }
$summary | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath (Join-Path $root 'summary.json') -Encoding utf8
$timings | ForEach-Object { [pscustomobject]$_ } | Select-Object case,pair,mean_change_pct,p95_change_pct,service_wall_change_pct | Format-Table
"Audit passed: $($cases.Count) cases, $($sources.Count) run sources, $($baselineSource.files.Count) source files, $($baselineSource.tools_7c.Count) tools"
