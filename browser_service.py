#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器服务 - 统一处理浏览器启动和登录流程
"""

import os
import sys
import time

from browser_utils import (
    load_credentials, auto_fill_login, create_driver_with_retry,
)


class BrowserService:
    """统一管理浏览器创建和登录"""

    @staticmethod
    def launch_and_login(url: str, page_load_wait: float = 5.0) -> object:
        """
        创建浏览器、打开 URL、自动填写账密、等待用户完成验证码

        Args:
            url: 目标 URL
            page_load_wait: 打开页面后的等待秒数（默认 5s）

        Returns:
            已完成登录的 WebDriver 实例
        """
        driver = create_driver_with_retry()
        driver.get(url)
        time.sleep(page_load_wait)
        print("\n✓ 浏览器已打开")

        BrowserService._do_login(driver)
        return driver

    @staticmethod
    def _do_login(driver) -> None:
        """自动填写账密，等待用户完成验证码后按 Enter"""
        username, password = load_credentials()
        if username and password:
            filled = auto_fill_login(driver, username, password)
            if filled:
                print("\n" + "="*60)
                print("请在浏览器中完成验证码，然后点击【登录】按钮")
                print("登录成功后，回到这里按 Enter 继续...")
                print("="*60)
            else:
                print("\n⚠️  自动填写失败，请手动登录")
        else:
            print("\n⚠️  未找到登录凭证，请手动登录")
            print("提示：在 .env 文件中填写 CNIPA_USERNAME 和 CNIPA_PASSWORD 可自动填写账密")

        if sys.stdin.isatty():
            input("登录完成后按 Enter 继续...")
        else:
            wait_seconds = float(os.getenv('CNIPA_LOGIN_WAIT_SECONDS', '0') or '0')
            if wait_seconds > 0:
                print(f"⏳ 非交互模式：等待 {wait_seconds:.0f} 秒，请在浏览器中完成验证码并登录...")
                time.sleep(wait_seconds)
            else:
                print("⏭️  跳过登录等待（非交互模式）")
