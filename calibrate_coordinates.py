#!/usr/bin/env python3
"""Compatibility entry point for live-click coordinate calibration."""

from __future__ import annotations

import argparse

import record_detail_coordinates
import record_search_coordinates


CALIBRATION_TARGETS = (
    "search",
    "detail-link",
    "fwxx-menu",
    "fee-menu",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record CNIPA coordinates in the collection browser"
    )
    parser.add_argument("target", choices=CALIBRATION_TARGETS)
    arguments = parser.parse_args(argv)

    if arguments.target == "search":
        return record_search_coordinates.main()
    return record_detail_coordinates.main([arguments.target])


if __name__ == "__main__":
    raise SystemExit(main())
