"""Stable public command entry point for the packaged report exporter."""

from __future__ import annotations

import sys
from pathlib import Path


REPORTS_ROOT = Path(__file__).resolve().paren
if str(REPORTS_ROOT) not in sys.path:
    sys.path.insert(0, str(REPORTS_ROOT))

from f_exporter.run_export_d3 import main, run_export  # noqa: E402,F401


if __name__ == "__main__":
    main()
