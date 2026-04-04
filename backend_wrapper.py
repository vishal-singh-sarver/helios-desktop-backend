#!/usr/bin/env python3
"""
PyInstaller entrypoint for the HeliosGUI backend.

This script wraps the FastAPI app and runs it via uvicorn. It accepts
a --port argument from the Electron app and defaults to port 8000.

The app is imported from app.main and started with the specified port.
PyInstaller will bundle 'app' as a frozen module, so sys.path setup
handles both dev and packaged scenarios.

Usage:
    python backend_wrapper.py [--port=8008]
    ./heliosgui_backend --port=8008  (when packaged as binary)
"""

import sys
from pathlib import Path

# For direct execution (dev): add parent dir to path for app imports
# For packaged execution: PyInstaller will have already set up the paths
if hasattr(sys, 'frozen'):
    # Running as a PyInstaller bundle
    app_root = sys._MEIPASS
else:
    # Running as a script - add the parent directory (backend-api/) to path
    app_root = str(Path(__file__).resolve().parent)

if app_root not in sys.path:
    sys.path.insert(0, app_root)

import argparse
import uvicorn


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="HeliosGUI Backend Server",
        add_help=False,  # Disable default help to avoid conflicts with uvicorn
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to run the server on (default: 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )

    # Parse only known args to allow any extras to be handled by uvicorn
    args, _ = parser.parse_known_args()
    return args


def main():
    """Run the FastAPI app with uvicorn."""
    args = parse_args()

    # Use string-based app reference for better PyInstaller compatibility
    # This allows PyInstaller to properly discover the module regardless of frozen state
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
