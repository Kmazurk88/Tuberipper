"""Default settings. Override per-run with `main.py --out <path>` (CLI) or the
TUBERIPPER_OUTPUT_DIR environment variable (server/Docker)."""

import os

# Where the music library lives. In Docker this is the in-container mount (/music).
DEFAULT_OUTPUT_DIR = os.environ.get("TUBERIPPER_OUTPUT_DIR", "/music")
