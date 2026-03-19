Param(
    [string]$OutputDir = ".\dist",
    [switch]$KeepStage
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$projectName = Split-Path -Leaf $projectRoot
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$variant = "flask"

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $outRoot = $OutputDir
} else {
    $outRoot = Join-Path $projectRoot $OutputDir
}

$stageRoot = Join-Path $outRoot "_stage"
$bundleRoot = Join-Path $stageRoot "${projectName}_portable_${timestamp}"
$stageProject = Join-Path $bundleRoot $projectName
$zipPath = Join-Path $outRoot "${projectName}_portable_${variant}_${timestamp}.zip"

Write-Host "[Bundle] Project root: $projectRoot"
Write-Host "[Bundle] Output dir:   $outRoot"
Write-Host "[Bundle] Variant:      $variant"

if (Test-Path $bundleRoot) { Remove-Item -LiteralPath $bundleRoot -Recurse -Force }
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

New-Item -ItemType Directory -Path $stageProject -Force | Out-Null
New-Item -ItemType Directory -Path $outRoot -Force | Out-Null

$excludeDirs = @(
    ".venv",
    "__pycache__",
    ".cache",
    ".matplotlib",
    ".modelscope",
    ".paddlex",
    "_backups",
    "_tmp_insight",
    "dist",
    "_pack"
)
$excludeFiles = @(
    "*.pyc",
    "*.pyo",
    "_tmp_app_crash.log"
)

$xdArgs = $excludeDirs | ForEach-Object { Join-Path $projectRoot $_ }
$robocopyArgs = @(
    $projectRoot,
    $stageProject,
    "/E",
    "/R:1",
    "/W:1",
    "/NFL",
    "/NDL",
    "/NJH",
    "/NJS",
    "/NC",
    "/NS",
    "/NP",
    "/XD"
) + $xdArgs + @("/XF") + $excludeFiles

& robocopy @robocopyArgs | Out-Null
$rc = $LASTEXITCODE
if ($rc -gt 7) {
    throw "robocopy failed with exit code $rc"
}

$launcherBat = @"
@echo off
setlocal
cd /d %~dp0
echo [Launcher] Setting up environment...
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
if errorlevel 1 (
  echo [Launcher] Setup failed.
  pause
  exit /b 1
)
echo [Launcher] Starting app...
.\.venv\Scripts\python.exe .\app.py
endlocal
"@
Set-Content -LiteralPath (Join-Path $stageProject "RUN_APP_WINDOWS.bat") -Value $launcherBat -Encoding ASCII

$portableReadme = @"
# Portable Bundle Quick Start

1. Extract this ZIP.
2. Open PowerShell inside the extracted `$projectName` folder.
3. Run:

   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
   .\.venv\Scripts\python.exe .\app.py

Or double click:

   RUN_APP_WINDOWS.bat

Notes:
- Python 3.10 is required.
- `.venv` is intentionally excluded from this portable ZIP.
"@
Set-Content -LiteralPath (Join-Path $stageProject "PORTABLE_QUICKSTART.md") -Value $portableReadme -Encoding UTF8

Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal -Force

$zipInfo = Get-Item -LiteralPath $zipPath
$stageSizeBytes = (Get-ChildItem -LiteralPath $bundleRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum

if (-not $KeepStage) {
    Remove-Item -LiteralPath $bundleRoot -Recurse -Force
}

[PSCustomObject]@{
    ZipPath        = $zipInfo.FullName
    ZipSizeMB      = [math]::Round($zipInfo.Length / 1MB, 2)
    StageContentMB = [math]::Round($stageSizeBytes / 1MB, 2)
    RoboCopyCode   = $rc
    Variant        = $variant
}
