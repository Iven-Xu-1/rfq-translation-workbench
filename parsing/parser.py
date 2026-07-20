"""Stable public entry point for the packaged text parser.

The implementation remains in ``rfq_text_parser``.  This wrapper preserves the
component path expected by the public pipeline without copying implementation
code or relying on an internal repository layout.
"""

from __future__ import annotations

import sys
from pathlib import Path


PARSING_ROOT = Path(__file__).resolve().paren
if str(PARSING_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSING_ROOT))

from rfq_text_parser.parser import *  # noqa: F401,F403,E402
