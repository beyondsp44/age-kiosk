Param(
    [switch]$ForceCpu,
    [switch]$SkipDiagnose,
    [switch]$SkipApiVerify
)

$ErrorActionPreference = "Stop"

function Get-PythonBootstrap {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            & py -3.10 -c "import sys; print(sys.version)"
            if ($LASTEXITCODE -eq 0) {
                return { param([string[]]$a) & py -3.10 @a }
            }
        } catch {
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            & python -c "import sys; assert sys.version_info[:2]==(3,10); print(sys.version)"
            if ($LASTEXITCODE -eq 0) {
                return { param([string[]]$a) & python @a }
            }
        } catch {
        }
    }

    throw "Python 3.10 not found. Please install Python 3.10 first."
}

function Test-PipPackageInstalled {
    Param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    & .\.venv\Scripts\python.exe -c "import importlib.metadata as m,sys; name='$Name'; m.version(name); sys.exit(0)" 2>$null
    return ($LASTEXITCODE -eq 0)
}

function Install-RuntimeCpu {
    if (Test-PipPackageInstalled -Name "onnxruntime") {
        Write-Host "[Setup] CPU runtime already installed (onnxruntime)."
        return "CPUExecutionProvider"
    }

    Write-Host "[Setup] Installing CPU runtime (onnxruntime==1.23.2)..."
    & .\.venv\Scripts\python.exe -m pip install --upgrade --force-reinstall onnxruntime==1.23.2 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "CPU runtime install failed."
    }
    return "CPUExecutionProvider"
}

function Test-NvidiaGpuReady {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        Write-Warning "[Setup] nvidia-smi not found. This machine may not have NVIDIA driver/GPU."
        return $false
    }

    try {
        $gpuLine = (& nvidia-smi -L 2>$null | Select-Object -First 1)
        if ($LASTEXITCODE -eq 0 -and $gpuLine) {
            Write-Host "[Setup] NVIDIA detected: $gpuLine"
            return $true
        }
    } catch {
    }

    Write-Warning "[Setup] nvidia-smi is present but GPU query failed. Will use CPU runtime."
    return $false
}

function Install-RuntimeGpuOrFallback {
    if (-not (Test-NvidiaGpuReady)) {
        return Install-RuntimeCpu
    }

    $hasGpuRuntime = (Test-PipPackageInstalled -Name "onnxruntime-gpu")
    $hasCudaDeps = (Test-PipPackageInstalled -Name "nvidia-cublas-cu12") -and (Test-PipPackageInstalled -Name "nvidia-cudnn-cu12")
    if ($hasGpuRuntime -and $hasCudaDeps) {
        Write-Host "[Setup] GPU runtime packages already installed."
        return "CUDAExecutionProvider"
    }

    Write-Host "[Setup] Installing GPU runtime (onnxruntime-gpu[cuda,cudnn]==1.23.2)..."
    try {
        & .\.venv\Scripts\python.exe -m pip install --upgrade --no-cache-dir --force-reinstall "onnxruntime-gpu[cuda,cudnn]==1.23.2" | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "GPU runtime install failed."
        }
        Write-Host "[Setup] GPU runtime install completed."
        return "CUDAExecutionProvider"
    } catch {
        Write-Warning "[Setup] GPU runtime install failed. Falling back to CPU runtime. Details: $($_.Exception.Message)"
        return Install-RuntimeCpu
    }
}

function Invoke-GpuDiagnoseCheck {
    Write-Host "[Verify] Running scripts/gpu_diagnose.py ..."
    & .\.venv\Scripts\python.exe .\scripts\gpu_diagnose.py | Out-Host
    $diagCode = $LASTEXITCODE
    if ($diagCode -eq 0) {
        Write-Host "[Verify] gpu_diagnose: PASS"
    } else {
        Write-Warning "[Verify] gpu_diagnose: FAIL (exit=$diagCode)"
    }
    return $diagCode
}

function Invoke-ApiStatusCheck {
    $stdoutPath = ".\_tmp_setup_app_stdout.log"
    $stderrPath = ".\_tmp_setup_app_stderr.log"
    Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue

    $env:AGE_KIOSK_ENABLE_DML = "0"
    $proc = $null

    try {
        $proc = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList ".\app.py" -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

        $status = $null
        for ($i = 0; $i -lt 20; $i++) {
            Start-Sleep -Milliseconds 700
            try {
                $status = Invoke-RestMethod "http://127.0.0.1:5000/api/status" -TimeoutSec 2
                if ($status) { break }
            } catch {
            }
        }

        if ($status -and $status.data) {
            Write-Host "[Verify] /api/status ai_provider=$($status.data.ai_provider), state=$($status.data.state)"
        } else {
            Write-Warning "[Verify] /api/status is unavailable."
        }

        if (Test-Path $stderrPath) {
            $errTail = (Get-Content $stderrPath -Tail 80) -join "`n"
            if ($errTail -match "cublasLt64_12\.dll") {
                Write-Warning "[Verify] Startup log still reports missing cublasLt64_12.dll."
            }
        }
    } finally {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force
        }
        Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
    }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
Set-Location $projectRoot

Write-Host "[Setup] Project root: $projectRoot"

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    $bootstrap = Get-PythonBootstrap
    Write-Host "[Setup] Creating .venv with Python 3.10..."
    & $bootstrap @("-m", "venv", ".venv")
}

Write-Host "[Setup] Upgrading pip..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}

Write-Host "[Setup] Installing requirements..."
& .\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[Setup] requirements install failed (likely network restricted). Continue with existing environment."
}

if ($ForceCpu) {
    Write-Host "[Setup] ForceCpu is set; skipping GPU install."
    $targetProvider = Install-RuntimeCpu
} else {
    $targetProvider = Install-RuntimeGpuOrFallback
}
Write-Host "[Setup] Target provider after install: $targetProvider"

Write-Host "[Setup] Installed runtime-related packages:"
& .\.venv\Scripts\python.exe -m pip list | Select-String -Pattern "onnxruntime|nvidia-cublas-cu12|nvidia-cudnn-cu12|nvidia-cuda-runtime-cu12|nvidia-cuda-nvrtc-cu12|nvidia-cufft-cu12|nvidia-curand-cu12|nvidia-nvjitlink-cu12" -CaseSensitive:$false

Write-Host "[Setup] Verifying ONNX providers..."
& .\.venv\Scripts\python.exe -c "import onnxruntime as ort; print('Providers:', ort.get_available_providers())"
if ($LASTEXITCODE -ne 0) {
    throw "ONNX provider check failed."
}

if (-not $SkipDiagnose) {
    $diagExit = Invoke-GpuDiagnoseCheck
    if ($targetProvider -eq "CUDAExecutionProvider" -and $diagExit -ne 0) {
        Write-Warning "[Setup] GPU diagnose failed after GPU install. Falling back to CPU runtime."
        $targetProvider = Install-RuntimeCpu
        & .\.venv\Scripts\python.exe -c "import onnxruntime as ort; print('Providers:', ort.get_available_providers())"
    }
}

if (-not $SkipApiVerify) {
    Invoke-ApiStatusCheck
}

Write-Host ""
Write-Host "[Done] Setup complete."
Write-Host "Run app:"
Write-Host "  cd `"$projectRoot`""
Write-Host "  .\.venv\Scripts\python.exe .\app.py"
