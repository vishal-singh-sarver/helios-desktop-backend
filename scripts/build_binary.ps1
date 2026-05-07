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
  '--collect-all', 'fastapi'
  '--collect-all', 'pydantic'
  '--collect-all', 'sqlalchemy'
)

# Selectively bundle only runtime-needed parts of pyhelios instead of the entire
# directory (~719 MB). Excludes helios-core/ C++ source, build artifacts, docs, tests.
# Windows uses ';' as PyInstaller path separator.

# Selectively bundle only runtime-needed parts of pyhelios instead of the entire
# directory (~719 MB). Excludes helios-core/ C++ source, build artifacts, docs, tests.
# Deep MSBuild .tlog paths under pyhelios_build/build/ also blow Windows' MAX_PATH
# (260 chars) during PyInstaller's COLLECT phase, so we cherry-pick.
# Windows uses ';' as PyInstaller path separator.

# 1. Python package (the actual importable code, ~3.4 MB)
$pyheliosPkg = Join-Path $backendApiDir 'pyhelios\pyhelios'
if (Test-Path $pyheliosPkg) {
  $pyInstallerArgs += @('--add-data', "$pyheliosPkg;pyhelios\pyhelios")
}

# 2. Top-level pyhelios .py files
$pyheliosSrc = Join-Path $backendApiDir 'pyhelios'
foreach ($pyFile in (Get-ChildItem -Path $pyheliosSrc -Filter '*.py' -File -ErrorAction SilentlyContinue)) {
  $pyInstallerArgs += @('--add-data', "$($pyFile.FullName);pyhelios\")
}

# 3. Native libraries — libhelios.dll is required (fail loudly), optix/glew32
# are optional companions kept from the older HEAD logic since they live next
# to / above the lib/ directory and aren't picked up by the bin/ glob below.
$libBuildDir  = Join-Path $backendApiDir 'pyhelios\pyhelios_build\build\lib'
$buildRootDir = Join-Path $backendApiDir 'pyhelios\pyhelios_build\build'

$libheliosPath = Join-Path $libBuildDir 'libhelios.dll'
if (-not (Test-Path $libheliosPath)) {
  throw "libhelios.dll missing at $libheliosPath at PyInstaller stage. The earlier auto-build step should have produced it - this indicates a logic error in this script."
}
$pyInstallerArgs += @('--add-binary', "$libheliosPath;pyhelios\pyhelios_build\build\lib\")

$optionalDlls = @(
  @{ Path = Join-Path $libBuildDir  'optix.6.5.0.dll'; Dest = 'pyhelios\pyhelios_build\build\lib' }
  @{ Path = Join-Path $buildRootDir 'glew32.dll';      Dest = 'pyhelios\pyhelios_build\build'    }
)
foreach ($dll in $optionalDlls) {
  if (Test-Path $dll.Path) {
    $pyInstallerArgs += @('--add-binary', "$($dll.Path);$($dll.Dest)")
  } else {
    Write-Host "[!] optional DLL not found at $($dll.Path) - skipping"
  }
}

# 4. Runtime asset images (textures needed by C++ core)
$imagesDir = Join-Path $libBuildDir 'images'
if (Test-Path $imagesDir) {
  $pyInstallerArgs += @('--add-data', "$imagesDir;pyhelios\pyhelios_build\build\lib\images")
}

# 5. Plugin assets (shaders, textures, spectral data)
$pluginsDir = Join-Path $buildRootDir 'plugins'
if (Test-Path $pluginsDir) {
  $pyInstallerArgs += @('--add-data', "$pluginsDir;pyhelios\pyhelios_build\build\plugins")
}

# 6. Built binaries in bin/ (if any)
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
