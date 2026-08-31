[CmdletBinding()]
param(
    [ValidateSet("Audit", "ValidatePlan", "ExpandMatrix", "Scenario", "Headless", "VerifyGolden", "DiagnoseHotPath", "DecideShortDiagnostic")]
    [string]$Action = "Audit",
    [string]$OutputPath,
    [string]$Profile = "functional_6",
    [ValidateSet("motion_only", "ordinary_projectiles", "guided_projectiles", "scripted_damage_and_recompile")]
    [string]$Stage = "motion_only",
    [ValidateRange(1, 3)]
    [int]$Repetition = 1,
    [int]$WarmupSteps = -1,
    [int]$MeasuredSteps = -1,
    [switch]$Resume
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PlanPath = Join-Path $ProjectRoot "contracts\web_bridge\t0-benchmark-plan.v1.json"

Push-Location $ProjectRoot
try {
    switch ($Action) {
        "ValidatePlan" { $Subcommand = "validate-plan" }
        "ExpandMatrix" { $Subcommand = "expand-matrix" }
        "Scenario" { $Subcommand = "scenario-manifest" }
        "Headless" { $Subcommand = "headless" }
        "VerifyGolden" { $Subcommand = "verify-golden" }
        "DiagnoseHotPath" { $Subcommand = "diagnose-hot-path" }
        "DecideShortDiagnostic" { $Subcommand = "decide-short-diagnostic" }
        default { $Subcommand = "audit" }
    }

    $Arguments = @("-X", "utf8", "-m", "benchmarks.t0", "--plan", $PlanPath, $Subcommand)
    if ($Action -in @("Scenario", "Headless")) {
        $Arguments += @("--profile", $Profile, "--stage", $Stage, "--repetition", $Repetition)
    }
    if ($Action -eq "Headless") {
        if ($WarmupSteps -ge 0) { $Arguments += @("--warmup-steps", $WarmupSteps) }
        if ($MeasuredSteps -ge 0) { $Arguments += @("--measured-steps", $MeasuredSteps) }
        if ($Resume) { $Arguments += "--resume" }
    }
    if ($Action -eq "DiagnoseHotPath") {
        if ($PSBoundParameters.ContainsKey("Profile")) { $Arguments += @("--profile", $Profile) }
        if ($PSBoundParameters.ContainsKey("Stage")) { $Arguments += @("--stage", $Stage) }
    }
    if ($OutputPath) {
        if ([System.IO.Path]::IsPathRooted($OutputPath)) {
            $ResolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
        }
        else {
            $ResolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputPath))
        }
        $Arguments += @("--output", $ResolvedOutput)
    }
    elseif ($Action -eq "Audit") {
        $DefaultOutput = Join-Path $ProjectRoot "artifacts\t0-local\t0a-preflight.json"
        $Arguments += @("--output", $DefaultOutput)
    }
    elseif ($Action -eq "Headless") {
        $DefaultOutput = Join-Path $ProjectRoot "artifacts\t0-local\runs\$Profile.$Stage.r$Repetition.headless.json"
        $Arguments += @("--output", $DefaultOutput)
    }
    elseif ($Action -eq "DiagnoseHotPath") {
        $DefaultOutput = Join-Path $ProjectRoot "artifacts\t0-local\t0b1a-hot-path.json"
        $Arguments += @("--output", $DefaultOutput)
    }
    elseif ($Action -eq "DecideShortDiagnostic") {
        $DefaultOutput = Join-Path $ProjectRoot "artifacts\t0-local\t0b1f-short-decision.json"
        $Arguments += @("--output", $DefaultOutput)
    }

    & python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "T0 $Action 失败，退出码 $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
