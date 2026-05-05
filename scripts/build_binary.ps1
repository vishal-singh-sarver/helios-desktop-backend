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

function Find-Python {
  $candidates = @('python3.11', 'python3.10', 'python', 'python3', 'py')

  foreach ($candidate in $candidates) {
    if ($candidate -eq 'py') {
      $command = Get-Command py -ErrorAction SilentlyContinue
      if ($command) {
        try {
          & py -3 -c "import sys; sys.exit(0)"
          if ($LASTEXITCODE -eq 0) {
            return @{ Command = 'py'; Arguments = @('-3') }
          }
        } catch {
          continue
        }
      }

      continue
    }

    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
      try {
        & $candidate -c "import sys; sys.exit(0)"
        if ($LASTEXITCODE -eq 0) {
          return @{ Command = $candidate; Arguments = @() }
        }
      } catch {
        continue
      }
    }
  }

  throw "Python 3.10+ is required but no supported python executable was found."
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
if ($pythonVersion -notmatch '^3\.(\d+)$' -or [int]$Matches[1] -lt 10) {
  throw "Python 3.10+ is required. Found: $pythonVersion"
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
  @{ Path = Join-Path $buildRootDir 'glew32.dll';      Required = $false }
)
$libheliosBundled = $false
foreach ($dll in $runtimeDlls) {
  if (Test-Path $dll.Path) {
    $pyInstallerArgs += @('--add-binary', "$($dll.Path);pyhelios\pyhelios_build\build\lib")
    if ($dll.Path -like '*libhelios.dll') { $libheliosBundled = $true }
  } elseif ($dll.Required) {
    Write-Host "[!] WARNING: required runtime DLL not found at $($dll.Path)"
  }
}
if (-not $libheliosBundled) {
  Write-Host "[!] WARNING: libhelios.dll missing - backend will fail at runtime. Build it first with 'python build_scripts\build_helios.py' in pyhelios/."
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

Write-Host "=========================================="
Write-Host "[*] Build successful!"
Write-Host "  Output: dist/heliosgui_backend.exe"
Write-Host "=========================================="
