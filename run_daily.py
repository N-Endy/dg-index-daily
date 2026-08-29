#!/usr/bin/env python3
"""Thin daily entrypoint — orchestrates dg.cli run."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root on path when invoked as script
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dg.cli import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0].startswith("-"):
        argv = ["run", *argv]
    sys.exit(main(argv))
