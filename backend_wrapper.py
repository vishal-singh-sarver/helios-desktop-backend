"""
Entry point for the packaged HeliosGUI backend executable.

PyInstaller bundles this script as the standalone executable.
The Electron backend-manager spawns it with: --port=<port>
"""

import argparse
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="HeliosGUI Backend Server")
    parser.add_argument("--port", type=int, default=8008, help="Port to listen on")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host to bind to")
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
