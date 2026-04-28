#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动配置代理的浏览器
- 确保浏览器的所有流量都经过 MITM 代理
- 用户在浏览器中手动进行查询和翻页
"""

import sys
import os
import time
import subprocess
import socket
from pathlib import Path

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
except ImportError:
    print("[!] 缺少依赖: undetected_chromedriver 或 selenium")
    print("[!] 请运行: pip install undetected_chromedriver selenium")
    sys.exit(1)

from browser_utils import check_mitm_proxy


def launch_chrome_with_proxy(proxy_url: str = "http://127.0.0.1:8082"):
    """
    启动 Chrome 浏览器并配置代理

    这样做的好处：
    1. 100% 确保浏览器流量走过代理
    2. 无需手动配置系统代理设置
    3. 支持 HTTPS 拦截（带 --ignore-certificate-errors）
    """
    print("\n" + "=" * 70)
    print("🌐 启动浏览器（配置代理）")
    print("=" * 70)
    print()

    try:
        print(f"[*] 配置参数:")
        print(f"    代理: {proxy_url}")
        print(f"    绕过反爬虫: 启用")
        print(f"    证书验证: 禁用（支持 HTTPS 拦截）")
        print()

        options = uc.ChromeOptions()

        # 基础配置
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # 代理配置（最关键）
        options.add_argument(f"--proxy-server={proxy_url}")

        # HTTPS 支持
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--allow-running-insecure-content")

        # 其他优化
        options.add_argument("--disable-web-resources")
        options.add_argument("--start-maximized")

        print("[*] 正在启动浏览器...")
        driver = uc.Chrome(
            headless=False,
            options=options,
            version_main=None,  # 自动检测 Chrome 版本
        )

        print("[✓] 浏览器启动成功！")
        print()

        # 打开 CNIPA 公开搜索页面
        print("[*] 打开 CNIPA 公开搜索页面...")
        cnipa_url = "https://cponline.cnipa.gov.cn/publicSearch"
        driver.get(cnipa_url)

        time.sleep(3)
        print("[✓] 页面加载完成")
        print()

        # 打印使用说明
        print("=" * 70)
        print("📌 使用说明")
        print("=" * 70)
        print()
        print("现在你可以在浏览器中进行以下操作：")
        print()
        print("1️⃣  输入查询条件（申请人、技术分类等）")
        print("2️⃣  点击'查询'按钮")
        print("3️⃣  手动点击'下一页'按钮")
        print()
        print("✨ 所有操作都会被 MITM 代理自动采集！")
        print()
        print("监控进度：")
        print("  - 查看启动 MITM 代理的终端")
        print("  - 会显示每页采集的记录数和保存的文件")
        print()
        print("完成后：")
        print("  - 关闭浏览器")
        print("  - 运行: python export_public_search.py")
        print()
        print("=" * 70)
        print()

        # 保持浏览器打开，等待用户操作
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] 正在关闭浏览器...")
            driver.quit()
            print("[✓] 浏览器已关闭")
            print()
            print("✅ 采集完成！")
            print()
            print("后续步骤:")
            print("  python export_public_search.py")
            print()

    except Exception as e:
        print(f"\n[❌] 启动浏览器失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    print("\n" + "=" * 70)
    print("🚀 CNIPA 公开搜索 - 浏览器代理启动工具")
    print("=" * 70)
    print()

    # 检查 MITM 代理
    print("[*] 检查 MITM 代理...")
    if not check_mitm_proxy():
        print("[-] MITM 代理未运行！")
        print()
        print("❌ 请先启动 MITM 代理:")
        print("   python start_mitm_public_search.py")
        print()
        print("然后在另一个终端运行此脚本。")
        sys.exit(1)

    print("[✓] MITM 代理已在运行（127.0.0.1:8082）")
    print()

    # 启动浏览器
    launch_chrome_with_proxy()


if __name__ == "__main__":
    main()
