# CapCut Agent - installs the optional parts in one go.
#   subtitles (faster-whisper) + background removal (rembg) + models
#   and pre-downloads the models so the first real run is not stuck waiting.
# Safe to re-run: everything already present is skipped.

$ErrorActionPreference = "Continue"

$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$py = Join-Path $root "runtime\python\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "  [ERROR] Python runtime not found. Run the main setup first."
    exit 1
}

function Pip([string[]]$pkgs) {
    & $py -m pip install --disable-pip-version-check --no-warn-script-location $pkgs
    return ($LASTEXITCODE -eq 0)
}

# ---------- 1) subtitles ----------
Write-Host ""
Write-Host "[1/3] subtitles (faster-whisper) ..."
& $py -c "import faster_whisper" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      already installed - skip"
} elseif (-not (Pip @("faster-whisper"))) {
    Write-Host "  [ERROR] faster-whisper install failed. Check internet and run again."
    exit 1
}

# ---------- 2) (GPU support removed on purpose) ----------
# Older graphics cards made subtitles SLOWER than CPU, so CUDA libraries are no
# longer installed. Recognition always runs on CPU, same as the version that
# worked well.

# ---------- 3) background removal ----------
Write-Host ""
Write-Host "[2/3] background removal (rembg) ..."
& $py -c "import rembg, PIL" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "      already installed - skip"
} elseif (-not (Pip @("pillow", "onnxruntime", "rembg"))) {
    Write-Host "      [WARN] rembg failed - background removal will be unavailable."
}

# ---------- 4) models (so the first real run does not stall) ----------
Write-Host ""
Write-Host "[3/3] downloading models (speech ~3GB, cutout ~176MB) ..."
Write-Host "      this is the long part - please leave it running."
& $py -c "from faster_whisper import WhisperModel; WhisperModel('large-v3', device='cpu', compute_type='int8'); print('      speech model ready')"
if ($LASTEXITCODE -ne 0) { Write-Host "      [WARN] speech model download failed - it will retry on first use." }
& $py -c "from rembg import new_session; new_session('u2net'); print('      cutout model ready')" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "      [WARN] cutout model download failed - it will retry on first use." }

Write-Host ""
Write-Host "  All optional parts are ready."
exit 0
