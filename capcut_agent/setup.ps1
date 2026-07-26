# CapCut Agent - self-contained bootstrap
# Downloads a local (portable) Python + ffmpeg into .\runtime and installs deps.
# No admin rights, nothing installed system-wide. Safe to re-run (idempotent).

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072 } catch {}

$root    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$runtime = Join-Path $root "runtime"
$pyDir   = Join-Path $runtime "python"
$py      = Join-Path $pyDir "python.exe"
$ffExe   = Join-Path $runtime "ffmpeg\bin\ffmpeg.exe"

$PY_URL  = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
$FF_URL  = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Download($url, $dest) {
    Write-Host "    downloading: $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

try {
    # 1) Portable Python -------------------------------------------------
    if (-not (Test-Path $py)) {
        Write-Host "[1/4] Python (portable) setup ..."
        $zip = Join-Path $runtime "python.zip"
        Download $PY_URL $zip
        if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $pyDir -Force
        Remove-Item $zip -Force

        # enable site-packages / pip in the embeddable build
        $pth = Get-ChildItem $pyDir -Filter "python*._pth" | Select-Object -First 1
        if ($pth) {
            (Get-Content $pth.FullName) -replace '^\s*#\s*import site', 'import site' | Set-Content $pth.FullName
        }
        $getpip = Join-Path $runtime "get-pip.py"
        Download $PIP_URL $getpip
        & $py $getpip --no-warn-script-location
        Remove-Item $getpip -Force
    } else {
        Write-Host "[1/4] Python already installed - skip"
    }

    # 2) Python packages (core; subtitles are optional) ------------------
    Write-Host "[2/4] installing packages (fastapi / uvicorn) ..."
    & $py -m pip install --disable-pip-version-check --no-warn-script-location fastapi uvicorn python-multipart
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

    # 3) ffmpeg (portable) ----------------------------------------------
    if (-not (Test-Path $ffExe)) {
        Write-Host "[3/4] ffmpeg (portable) setup ..."
        $zip = Join-Path $runtime "ffmpeg.zip"
        Download $FF_URL $zip
        $tmp = Join-Path $runtime "ffmpeg_tmp"
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        $sub = Get-ChildItem $tmp -Directory | Select-Object -First 1
        $ffDir = Join-Path $runtime "ffmpeg"
        if (Test-Path $ffDir) { Remove-Item $ffDir -Recurse -Force }
        Move-Item $sub.FullName $ffDir -Force
        Remove-Item $zip -Force
        Remove-Item $tmp -Recurse -Force
    } else {
        Write-Host "[3/4] ffmpeg already installed - skip"
    }

    Write-Host "[4/4] ready."
    exit 0
}
catch {
    Write-Host ""
    Write-Host "SETUP ERROR: $($_.Exception.Message)"
    exit 1
}
