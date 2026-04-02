#!/usr/bin/env bash
################################################################################
# Build the HeliosGUI backend executable using PyInstaller
#
# This script:
#   1. Creates a virtual environment if needed
#   2. Installs dependencies from requirements.txt
#   3. Installs PyInstaller
#   4. Builds a standalone executable from backend_wrapper.py
#   5. Outputs to: dist/heliosgui_backend (or .exe on Windows)
#
# Requirements:
#   - Python 3.10+ with venv support
#   - Ability to run pip install
#
# Usage:
#   ./scripts/build_binary.sh
#
################################################################################

set -eu

# Change to backend-api directory
cd "$(dirname "$0")/.."

echo "=========================================="
echo "Building HeliosGUI Backend Executable"
echo "=========================================="

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="mac"
    BINARY_NAME="heliosgui_backend"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    OS="linux"
    BINARY_NAME="heliosgui_backend"
elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    OS="win"
    BINARY_NAME="heliosgui_backend.exe"
else
    echo "Error: Unsupported OS: $OSTYPE"
    exit 1
fi

echo "[*] Platform: $OS"

# Step 1: Create or use existing virtual environment
# Step 1: Find a supported Python version (3.10+)
if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="python3.11"
elif command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="python3.10"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
else
    echo "Error: Python 3.10+ is required but no python3 executable was found."
    exit 1
fi

PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
    3.10|3.11|3.12|3.13) ;;
    *)
        echo "Error: Python 3.10+ is required. Found: $PYTHON_VERSION"
        exit 1
        ;;
esac

echo "[*] Using Python: $PYTHON_BIN ($PYTHON_VERSION)"

# Step 2: Create or use existing virtual environment
if [ ! -d "venv" ]; then
    echo "[*] Creating virtual environment..."
    "$PYTHON_BIN" -m venv venv
else
    echo "[*] Using existing virtual environment..."
fi


# Activate venv
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

# Step 2: Upgrade pip
echo "[*] Upgrading pip..."
pip install --upgrade pip >/dev/null

# Step 3: Install dependencies
echo "[*] Installing dependencies from requirements.txt..."
pip install -r requirements.txt >/dev/null

# Step 4: Install PyInstaller
echo "[*] Installing PyInstaller..."
pip install pyinstaller >/dev/null

# Step 5: Clean old dist directory
if [ -d "dist" ]; then
    echo "[*] Removing old build artifacts..."
    rm -rf dist
fi

# Step 6: Build with PyInstaller
echo "[*] Building executable with PyInstaller..."

# Hidden imports needed for the backend stack
HIDDEN_IMPORTS=(
    "uvicorn"
    "fastapi"
    "pydantic"
    "sqlalchemy"
    "app.main"
    "app.core"
    "app.routers"
    "app.db"
    "app.schemas"
)


# Build hidden imports string
HIDDEN_IMPORTS_STR=""
for import in "${HIDDEN_IMPORTS[@]}"; do
    HIDDEN_IMPORTS_STR="$HIDDEN_IMPORTS_STR --hidden-import=$import"
done

# Run PyInstaller
# NOTE: Using --onedir instead of --onefile for significantly faster startup:
#   --onefile: Extracts entire binary to temp directory on each run (5-30s overhead)
#   --onedir:  Directory structure, no extraction needed on subsequent runs (~0.5s startup)
pyinstaller \
    --onedir \
    --name "$BINARY_NAME" \
    --distpath dist \
    --workpath build \
    --specpath build \
    --noconfirm \
    --collect-submodules app \
    --collect-data app \
    --collect-all fastapi \
    --collect-all pydantic \
    --collect-all sqlalchemy \
    $HIDDEN_IMPORTS_STR \
    backend_wrapper.py

# Verify build succeeded
# With --onedir, the executable is in dist/$BINARY_NAME/$BINARY_NAME
DIST_DIR="dist/$BINARY_NAME"
if [ ! -d "$DIST_DIR" ]; then
    echo "❌ Error: Build failed - dist directory not found at $DIST_DIR"
    echo "   Check the PyInstaller output above for details."
    exit 1
fi

EXECUTABLE="$DIST_DIR/$BINARY_NAME"
if [ ! -f "$EXECUTABLE" ]; then
    echo "❌ Error: Build failed - executable not found at $EXECUTABLE"
    echo "   Check the PyInstaller output above for details."
    exit 1
fi

# Set executable permissions on Unix-like systems
if [[ "$OSTYPE" != "msys" && "$OSTYPE" != "win32" ]]; then
    chmod +x "$EXECUTABLE"
    echo "[*] Set executable permissions"
fi

echo "=========================================="
echo "✓ Build successful!"
echo "  Output: dist/$BINARY_NAME"
echo "=========================================="
