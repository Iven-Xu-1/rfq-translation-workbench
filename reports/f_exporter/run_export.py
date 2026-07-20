"""Deprecated historical D2 exporter.

J and K production processing call ``run_export_d3.py`` with an explicit D3
manifest.  This entry intentionally has no default package, no report output,
and no compatibility fallback so it cannot reintroduce old D2/C2 data.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "run_export.py is deprecated. Use run_export_d3.py <d3_thread_manifest.json> instead."
    )


if __name__ == "__main__":
    main()
