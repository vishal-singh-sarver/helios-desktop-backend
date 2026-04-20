#!/usr/bin/env pwsh
# Build PyHelios from source (submodule at pyhelios/).
# Usage: scripts/build_pyhelios.ps1 [-Debug] [-Gpu] [-PythonExe <path>] [-SkipPipInstall]
#
# Parameters:
#   -Debug           Build in debug mode (default: release)
#   -Gpu             Enable GPU/OptiX plugins (default: --nogpu)
#   -PythonExe       Explicit python executable to drive build_helios.py.
#                    Defaults to whatever 'python' resolves to on PATH.
#                    Pass the venv python from build_binary.ps1 to keep the
#                    build hermetic.
#   -SkipPipInstall  Do not run 'pip install -e .' after the C++ build.
#                    Used by the packaging pipeline (PyInstaller bundles
#                    pyhelios via --add-data, so the editable install is
#                    unnecessary and would otherwise pollute ambient Python).
#
# Prerequisites: cmake, MSVC Build Tools (Desktop development with C++),
#                Python 3.10/3.11/3.12

param(
    [switch]$Debug,
    [switch]$Gpu,
    [string]$PythonExe = 'python',
    [switch]$SkipPipInstall
)

$ErrorActionPreference = 'Stop'

$scriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptDir
$pyheliosDir = Join-Path $projectRoot 'pyhelios'

$buildMode = if ($Debug) { 'debug' } else { 'release' }
$gpuFlag   = if ($Gpu)   { ''       } else { '--nogpu' }

# Check submodule is initialized
if (-not (Test-Path (Join-Path $pyheliosDir 'build_scripts\build_helios.py'))) {
    Write-Error "PyHelios submodule not initialized. Run: git submodule update --init --recursive"
    exit 1
}

# Check helios-core sub-submodule
if (-not (Test-Path (Join-Path $pyheliosDir 'helios-core\core'))) {
    Write-Error "helios-core sub-submodule not initialized. Run: git submodule update --init --recursive"
    exit 1
}

# Verify cmake is available
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    Write-Error "cmake not found. Install CMake and add it to PATH (winget install -e --id Kitware.CMake)."
    exit 1
}

# Verify the requested Python interpreter is usable (any Python 3.x — build_helios.py only needs stdlib)
try {
    & $PythonExe -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Python interpreter '$PythonExe' is not Python 3.x or could not be invoked."
        exit 1
    }
} catch {
    Write-Error "Python interpreter '$PythonExe' could not be launched: $_"
    exit 1
}

Write-Host "Building PyHelios from source..."
Write-Host "  Source:     $pyheliosDir"
Write-Host "  Build mode: $buildMode"
Write-Host "  GPU:        $(if ($Gpu) { 'enabled' } else { 'disabled' })"
Write-Host "  Python:     $PythonExe"

# Run the PyHelios build script
Set-Location $pyheliosDir

$buildArgs = @('build_scripts/build_helios.py', '--buildmode', $buildMode, '--verbose')
if ($gpuFlag) { $buildArgs += $gpuFlag }

& $PythonExe @buildArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyHelios build failed (build_helios.py exited $LASTEXITCODE)"
    exit 1
}

# Install in editable mode so Python can find it (skipped by the packaging pipeline)
if ($SkipPipInstall) {
    Write-Host ""
    Write-Host "Skipping 'pip install -e' (--SkipPipInstall set)"
} else {
    Write-Host ""
    Write-Host "Installing PyHelios in editable mode..."
    & $PythonExe -m pip install -e $pyheliosDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install -e failed"
        exit 1
    }
}

# Verify the build
Write-Host ""
Write-Host "Verifying build..."
$libPath = Join-Path $pyheliosDir 'pyhelios_build\build\lib\libhelios.dll'
if (Test-Path $libPath) {
    $size = (Get-Item $libPath).Length / 1MB
    Write-Host "SUCCESS: Built $libPath"
    Write-Host "  Size: $([math]::Round($size, 1)) MB"
} else {
    Write-Error "Expected library not found at $libPath. Check build output above for errors."
    exit 1
}

Write-Host ""
Write-Host "PyHelios is ready. Rebuild the backend with: npm run package:win"
