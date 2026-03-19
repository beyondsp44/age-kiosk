$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir

Set-Location $projectRoot

$env:AGE_KIOSK_ENABLE_DML = "0"
$env:AGE_KIOSK_SHOW_POSITION_GUIDE = "0"
$env:AGE_KIOSK_GUIDE_OVERLAP_GATE = "1"
$env:AGE_KIOSK_FACE_VERTICAL_LOCK = "1"

Write-Host "[Profile] FIXED_POINT"
Write-Host "  AGE_KIOSK_ENABLE_DML=$env:AGE_KIOSK_ENABLE_DML"
Write-Host "  AGE_KIOSK_SHOW_POSITION_GUIDE=$env:AGE_KIOSK_SHOW_POSITION_GUIDE"
Write-Host "  AGE_KIOSK_GUIDE_OVERLAP_GATE=$env:AGE_KIOSK_GUIDE_OVERLAP_GATE"
Write-Host "  AGE_KIOSK_FACE_VERTICAL_LOCK=$env:AGE_KIOSK_FACE_VERTICAL_LOCK"

& ".\.venv\Scripts\python.exe" ".\app.py"
