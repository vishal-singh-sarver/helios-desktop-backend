#!/usr/bin/env pwsh
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendApiDir = Split-Path -Parent $scriptDir
Set-Location $backendApiDir

Write-Host "=========================================="
Write-Host "Building HeliosGUI Backend Executable"
Write-Host "=========================================="
Write-Host "[*] Platform: win"

function Test-SupportedPythonVersion($command, [string[]]$arguments) {
  try {
    $version = & $command @($arguments + @('-c', "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))")) 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    return ($version.Trim() -match '^3\.(10|11|12)$')
  } catch {
    return $false
  }
}

function Find-Python {
  # Prefer specific known-good versions (3.10/3.11/3.12). Avoid 3.13/3.14
  # because backend C-extension deps don't yet publish wheels for them.
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($ver in @('-3.11', '-3.12', '-3.10')) {
      if (Test-SupportedPythonVersion 'py' @($ver)) {
        return @{ Command = 'py'; Arguments = @($ver) }
      }
    }
  }

  foreach ($candidate in @('python3.11', 'python3.12', 'python3.10', 'python', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
      if (Test-SupportedPythonVersion $candidate @()) {
        return @{ Command = $candidate; Arguments = @() }
      }
    }
  }

  throw "Python 3.10/3.11/3.12 is required but no supported interpreter was found. Install via: winget install -e --id Python.Python.3.11"
}

function Get-PythonVersion($python) {
  $version = & $python.Command @($python.Arguments + @('-c', 'import sys; print(str(sys.version_info.major) + ''.'' + str(sys.version_info.minor))'))
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to determine Python version."
  }

  return $version.Trim()
}

function Invoke-Python($python, [string[]]$arguments) {
  & $python.Command @($python.Arguments + $arguments)
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed: $($python.Command) $($python.Arguments + $arguments -join ' ')"
  }
}

function Remove-DirectoryRobust([string]$path) {
  if (-not (Test-Path $path)) {
    return
  }

  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
      return
    } catch [System.IO.DirectoryNotFoundException] {
      return
    } catch [System.IO.FileNotFoundException] {
      return
    } catch {
      if (-not (Test-Path $path)) {
        return
      }

      if ($attempt -eq 3) {
        throw
      }

      Start-Sleep -Milliseconds 250
    }
  }
}

$python = Find-Python
$pythonVersion = Get-PythonVersion $python
if ($pythonVersion -notmatch '^3\.(10|11|12)$') {
  throw "Python 3.10/3.11/3.12 is required (3.13+ has incomplete wheel coverage for backend deps). Found: $pythonVersion"
}

Write-Host "[*] Using Python: $($python.Command) ($pythonVersion)"

if (-not (Test-Path 'venv')) {
  Write-Host "[*] Creating virtual environment..."
  Invoke-Python $python @('-m', 'venv', 'venv')
} else {
  Write-Host "[*] Using existing virtual environment..."
}

$venvPython = Join-Path $backendApiDir 'venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
  throw "Virtual environment Python not found at $venvPython"
}

Write-Host "[*] Upgrading pip..."
& $venvPython -m pip install --upgrade pip | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to upgrade pip."
}

Write-Host "[*] Installing dependencies from requirements.txt..."
& $venvPython -m pip install -r requirements.txt | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to install backend dependencies."
}

Write-Host "[*] Installing PyInstaller..."
& $venvPython -m pip install pyinstaller | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw "Failed to install PyInstaller."
}

# Build pyhelios native library if missing.
# Failures are fatal: a packaged backend without libhelios.dll silently falls
# back to PYHELIOS_AVAILABLE=False at runtime, which is the exact bug we are
# trying to prevent. Better to abort the build than ship a broken installer.
$libheliosPath = Join-Path $backendApiDir 'pyhelios\pyhelios_build\build\lib\libhelios.dll'
if (-not (Test-Path $libheliosPath)) {
  $buildScriptPs1 = Join-Path $backendApiDir 'scripts\build_pyhelios.ps1'
  if (-not (Test-Path $buildScriptPs1)) {
    throw "libhelios.dll not found at $libheliosPath and build_pyhelios.ps1 is missing. Cannot build native library."
  }

  Write-Host "[*] Native library (libhelios.dll) not found - building pyhelios from source..."
  # Pass the venv python and skip the editable install: PyInstaller bundles
  # pyhelios via --add-data below, so installing it into the venv is unneeded
  # and ambient-pip pollution risks breaking the build on machines with
  # multiple Python versions on PATH.
  & powershell -NoProfile -ExecutionPolicy Bypass -File $buildScriptPs1 -PythonExe $venvPython -SkipPipInstall
  if ($LASTEXITCODE -ne 0) {
    throw "build_pyhelios.ps1 failed (exit code $LASTEXITCODE). See output above for details."
  }
  if (-not (Test-Path $libheliosPath)) {
    throw "build_pyhelios.ps1 reported success but libhelios.dll was not produced at $libheliosPath."
  }
  Write-Host "[*] pyhelios native library built successfully"
} else {
  Write-Host "[*] Native library found: $libheliosPath"
}

if (Test-Path 'dist') {
  Write-Host "[*] Removing old build artifacts..."
  Remove-DirectoryRobust 'dist'
}

$hiddenImports = @(
  'uvicorn'
  'fastapi'
  'pydantic'
  'sqlalchemy'
  'app.main'
  'app.core'
  'app.routers'
  'app.db'
  'app.schemas'
)

$pyInstallerArgs = @(
  '-m', 'PyInstaller'
  '--onedir'
  '--name', 'heliosgui_backend.exe'
  '--distpath', 'dist'
  '--workpath', 'build'
  '--specpath', 'build'
  '--noconfirm'
  '--collect-submodules', 'app'
  '--collect-data', 'app'
  '--add-data', "$backendApiDir\app\db\migrations;app\db\migrations"
  '--collect-all', 'fastapi'
  '--collect-all', 'pydantic'
  '--collect-all', 'sqlalchemy'
)

# SQL migration files. run_migrations() reads these at runtime; they are never
# imported and the folder has no __init__.py, so neither --collect-submodules
# nor --collect-data app picks them up. Without bundling them verbatim the
# packaged DB has no tables and every API call fails with "no such table".
$migrationsDir = Join-Path $backendApiDir 'app\db\migrations'
if (-not (Test-Path $migrationsDir)) {
  Write-Host "ERROR: migrations folder not found at $migrationsDir"
  exit 1
}
$pyInstallerArgs += @('--add-data', "$migrationsDir;app\db\migrations")

# Committed default textures (backend-api/assets) — the picker set behind
# /api/textures/defaults. Like the migrations above they are pure data files that
# nothing imports, so --collect-data app does not reach them.
#
# The destination MUST be a bare `assets` at the bundle root: material_service's
# default_textures_dir() resolves them as parents[2] of its own __file__, which
# under PyInstaller is _MEIPASS/app/services/material_service.py — so parents[2]
# is _MEIPASS itself. Nest them any deeper and the lookup silently misses.
#
# Missing them does NOT fail the build or the boot; it fails quietly at runtime,
# twice over: /defaults returns an empty list so the "From Library" grid renders
# blank, AND the absent directory drops out of the serve endpoint's allowlist, so
# every material already pointing at one of these files 403s and renders
# untextured. The mac/linux script has carried this since the start
# (build_binary.sh, --add-data "$BACKEND_DIR/assets:assets"); Windows did not,
# which is why textures were missing in dev too — dev spawns this same bundle
# (main/backend-manager.ts getBackendPath), not the Python source.
$assetsDir = Join-Path $backendApiDir 'assets'
if (-not (Test-Path $assetsDir)) {
  Write-Host "ERROR: default textures folder not found at $assetsDir"
  exit 1
}
$pyInstallerArgs += @('--add-data', "$assetsDir;assets")

# Bundle the pyhelios Python package and its native runtime DLLs only.
# We deliberately do NOT bundle the entire pyhelios/ submodule (helios-core/,
# pyhelios_build/build/, tests/, docs/, build_scripts/) - those aren't needed
# at runtime, and the deep MSBuild .tlog paths under pyhelios_build/build/
# blow Windows' MAX_PATH (260 chars) during PyInstaller's COLLECT phase.
$pyheliosSrc = Join-Path $backendApiDir 'pyhelios'
$pyheliosPkg = Join-Path $pyheliosSrc 'pyhelios'
if (Test-Path $pyheliosPkg) {
  $pyInstallerArgs += @('--add-data', "$pyheliosPkg;pyhelios\pyhelios")
} else {
  Write-Host "[!] WARNING: pyhelios package not found at $pyheliosPkg - pyhelios will not be bundled"
}

# Native runtime DLLs go where the loader expects them:
#   pyhelios/plugins/loader.py:_find_library_directory() searches
#   <pyhelios_root>/pyhelios_build/build/lib/ for libhelios.dll.
$libBuildDir  = Join-Path $pyheliosSrc 'pyhelios_build\build\lib'
$buildRootDir = Join-Path $pyheliosSrc 'pyhelios_build\build'
$runtimeDlls = @(
  @{ Path = Join-Path $libBuildDir  'libhelios.dll';   Required = $true  }
  @{ Path = Join-Path $libBuildDir  'optix.6.5.0.dll'; Required = $false }
  @{ Path = Join-Path $libBuildDir  'optix.51.dll';    Required = $false } 
  @{ Path = Join-Path $buildRootDir 'glew32.dll';      Required = $false }
)
foreach ($dll in $runtimeDlls) {
  if (Test-Path $dll.Path) {
    $pyInstallerArgs += @('--add-binary', "$($dll.Path);pyhelios\pyhelios_build\build\lib")
    Write-Host "[*] Bundling DLL: $($dll.Path)"
  } elseif ($dll.Required) {
    throw "Required DLL missing: $($dll.Path)"
  } else {
    Write-Host "[!] Optional DLL not found, skipping: $($dll.Path)"
  }
}

# Runtime asset images (textures needed by C++ core)
$imagesDir = Join-Path $libBuildDir 'images'
if (Test-Path $imagesDir) {
  $pyInstallerArgs += @('--add-data', "$imagesDir;pyhelios\pyhelios_build\build\lib\images")
}

# Plugin assets (shaders, textures, spectral data).
#
# Only the asset subdirectories are bundled, NOT the whole build/plugins tree.
# CMake writes its artifacts alongside the assets - per-plugin CMakeFiles/,
# <target>.dir/Release/ object trees, MSBuild .tlog logs, and the vendored
# freetype/glew/glfw sources under visualizer/lib. Copying the lot dragged in
# ~138 MB of dead weight and produced staged paths over 260 chars, the same
# MAX_PATH failure the pyhelios comment above warns about.
#
# Do NOT swap this for an extension filter: .obj is ambiguous here. Under
# plantarchitecture/assets it is a Wavefront 3D model that IS needed; under
# <target>.dir/Release/ it is an MSVC object file that is not. Only a
# directory-level split separates them correctly.
#
# Each entry maps <build/plugins>/<rel> to the same <rel> in the bundle, so
# runtime lookups (helios::resolveFilePath("plugins/...")) resolve unchanged.
# A missing asset dir does not fail the build - it fails silently at runtime -
# so when a plugin gains an asset directory, add it here.
$pluginAssetDirs = @(
  'leafoptics\spectral_data'
  'lidar\data'
  'lidar\xml'
  'plantarchitecture\assets'
  'solarposition\lib'
  'solarposition\ssolar_goa'
  'visualizer\fonts'
  'visualizer\shaders'
  'visualizer\textures'
  'weberpenntree\leaves'
  'weberpenntree\wood'
  'weberpenntree\xml'
)
$pluginsDir = Join-Path $buildRootDir 'plugins'
if (Test-Path $pluginsDir) {
  foreach ($rel in $pluginAssetDirs) {
    $assetPath = Join-Path $pluginsDir $rel
    if (Test-Path $assetPath) {
      $pyInstallerArgs += @('--add-data', "$assetPath;pyhelios\pyhelios_build\build\plugins\$rel")
    } else {
      Write-Host "[!] Plugin asset dir not found, skipping: $assetPath"
    }
  }
} else {
  Write-Host "[!] WARNING: plugins build dir not found at $pluginsDir"
}

# Built binaries in bin/ (if any)
$binDir = Join-Path $buildRootDir 'bin'
if (Test-Path $binDir) {
  $pyInstallerArgs += @('--add-data', "$binDir;pyhelios\pyhelios_build\build\bin")
}


foreach ($hiddenImport in $hiddenImports) {
  $pyInstallerArgs += @('--hidden-import', $hiddenImport)
}

$pyInstallerArgs += 'backend_wrapper.py'

Write-Host "[*] Building executable with PyInstaller..."
& $venvPython @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
  throw 'PyInstaller build failed.'
}

$distDir = Join-Path 'dist' 'heliosgui_backend.exe'
if (-not (Test-Path $distDir -PathType Container)) {
  throw "Build failed - dist directory not found at $distDir"
}

$executable = Join-Path $distDir 'heliosgui_backend.exe'
if (-not (Test-Path $executable -PathType Leaf)) {
  throw "Build failed - executable not found at $executable"
}

# Verify the native library actually ended up in the bundled tree. Without
# this, PyInstaller can silently drop --add-binary entries on path errors and
# we would ship an installer that reports PYHELIOS_AVAILABLE=False at runtime.
$bundledLibhelios = Join-Path $distDir '_internal\pyhelios\pyhelios_build\build\lib\libhelios.dll'
if (-not (Test-Path $bundledLibhelios -PathType Leaf)) {
  throw "Build failed - libhelios.dll was not bundled into the PyInstaller output. Expected at: $bundledLibhelios"
}

Write-Host "=========================================="
Write-Host "[*] Build successful!"
Write-Host "  Output:    dist/heliosgui_backend.exe"
Write-Host "  libhelios: $bundledLibhelios"
Write-Host "=========================================="
