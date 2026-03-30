#!/usr/bin/env bash
# Build PyHelios from source (submodule at pyhelios/).
# Usage: scripts/build_pyhelios.sh [--debug] [--gpu]
#
# Prerequisites: cmake, a C++ compiler, Python 3.10+

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYHELIOS_DIR="$PROJECT_ROOT/pyhelios"

# Parse arguments
BUILD_MODE="release"
GPU_FLAG="--nogpu"
for arg in "$@"; do
    case "$arg" in
        --debug)   BUILD_MODE="debug" ;;
        --gpu)     GPU_FLAG="" ;;
        --help|-h)
            echo "Usage: $0 [--debug] [--gpu]"
            echo "  --debug   Build in debug mode (default: release)"
            echo "  --gpu     Enable GPU plugins (default: --nogpu)"
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg"
            exit 1
            ;;
    esac
done

# Check submodule is initialized
if [ ! -f "$PYHELIOS_DIR/build_scripts/build_helios.py" ]; then
    echo "ERROR: PyHelios submodule not initialized."
    echo "Run: git submodule update --init --recursive"
    exit 1
fi

# Check helios-core sub-submodule
if [ ! -d "$PYHELIOS_DIR/helios-core/core" ]; then
    echo "ERROR: helios-core sub-submodule not initialized."
    echo "Run: git submodule update --init --recursive"
    exit 1
fi

echo "Building PyHelios from source..."
echo "  Source:     $PYHELIOS_DIR"
echo "  Build mode: $BUILD_MODE"
echo "  GPU:        ${GPU_FLAG:-enabled}"

# Run the PyHelios build script
cd "$PYHELIOS_DIR"
python build_scripts/build_helios.py --buildmode "$BUILD_MODE" $GPU_FLAG --verbose

# Install in editable mode so Python can find it
echo ""
echo "Installing PyHelios in editable mode..."
pip install -e "$PYHELIOS_DIR"

# Verify the build
echo ""
echo "Verifying build..."
PLATFORM="$(uname -s)"
case "$PLATFORM" in
    Darwin) LIB_NAME="libhelios.dylib" ;;
    Linux)  LIB_NAME="libhelios.so" ;;
    MINGW*|MSYS*|CYGWIN*) LIB_NAME="libhelios.dll" ;;
    *)      LIB_NAME="libhelios.so" ;;
esac

LIB_PATH="$PYHELIOS_DIR/pyhelios_build/build/lib/$LIB_NAME"
if [ -f "$LIB_PATH" ]; then
    echo "SUCCESS: Built $LIB_PATH"
    echo "  Size: $(du -h "$LIB_PATH" | cut -f1)"
    echo "  Date: $(stat -f '%Sm' "$LIB_PATH" 2>/dev/null || stat -c '%y' "$LIB_PATH" 2>/dev/null)"
else
    echo "WARNING: Expected library not found at $LIB_PATH"
    echo "  Check build output above for errors."
    exit 1
fi

echo ""
echo "PyHelios is ready. Start the backend with:"
echo "  ./backend-api/run.sh --source"
