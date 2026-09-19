#!/usr/bin/env python3
"""Record CNIPA search targets directly from the live mouse pointer."""

from __future__ import annotations

import sys

from browser_service import BrowserService
from coordinate_service import CoordinateConfigurationError, CoordinateService
from desktop_collection_lock import reserve_detail_collection_desktop
from settings import CNIPA_URL


def main() -> int:
    driver = None
    try:
        with reserve_detail_collection_desktop("搜索页坐标校准"):
            try:
                driver = BrowserService.launch_and_login(CNIPA_URL)
                coordinates = CoordinateService.record_search_coordinates_from_pointer()
            finally:
                if driver is not None:
                    BrowserService.close_automation_browser(driver)
    except (CoordinateConfigurationError, OSError, RuntimeError) as error:
        print(f"搜索页坐标校准失败: {error}", file=sys.stderr)
        return 2
    else:
        print(f"已保存搜索页坐标: {coordinates}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
