#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
公开查询浏览器启动脚本

功能：
  - 启动配置了公开查询 MITM 代理（PUBLIC_MITM_PORT）的 Chrome 浏览器
  - 打开 CNIPA 公开搜索页面，用户手动输入查询条件后翻页
  - 配合 auto_paginate.py 可以自动翻页采集

使用方式（命令行）：
  python start_mitm_public_search.py   # 终端 1：启动公开查询代理
  python launch_browser_with_proxy.py  # 终端 2：启动浏览器

Dashboard 使用：Tab 5 → 步骤 1（公开代理）→ 步骤 2（公开浏览器）
"""

import sys
import time

try:
    import undetected_chromedriver as uc
except ImportError:
    print("[!] 缺少依赖: undetected_chromedriver")
    print("[!] 请运行: pip install undetected_chromedriver")
    sys.exit(1)

from browser_utils import (
    _get_chrome_major_version,
    auto_fill_login,
    load_credentials,
    raise_system_exit_on_sigterm,
)
from settings import MITM_HOST, PUBLIC_MITM_PORT, CNIPA_PUBLIC_SEARCH_URL


def _check_public_proxy() -> bool:
    """检查公开查询 MITM 代理（PUBLIC_MITM_PORT）是否在线"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    reachable = s.connect_ex((MITM_HOST, PUBLIC_MITM_PORT)) == 0
    s.close()
    return reachable


def launch_chrome_with_proxy(proxy_url: str = None) -> uc.Chrome:
    """
    启动配置了公开查询代理的 Chrome 浏览器，打开 CNIPA 公开搜索页。

    Returns:
        已打开目标页面的 WebDriver 实例
    """
    if proxy_url is None:
        proxy_url = f"http://{MITM_HOST}:{PUBLIC_MITM_PORT}"

    print("\n" + "=" * 70)
    print("🌐 启动浏览器（配置公开查询代理）")
    print("=" * 70)
    print()
    print(f"[*] 配置参数:")
    print(f"    代理: {proxy_url}")
    print(f"    目标: {CNIPA_PUBLIC_SEARCH_URL}")
    print(f"    绕过反爬虫: 启用")
    print(f"    证书验证: 禁用（支持 HTTPS 拦截）")
    print()

    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--proxy-server={proxy_url}")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-running-insecure-content")
    options.add_argument("--disable-web-resources")
    options.add_argument("--start-maximized")

    print("[*] 正在启动浏览器...")
    chrome_ver = _get_chrome_major_version()
    if chrome_ver:
        print(f"[*] 检测到 Chrome {chrome_ver}，固定匹配 ChromeDriver 版本")
    driver = uc.Chrome(
        headless=False,
        options=options,
        version_main=chrome_ver,  # 固定匹配本机 Chrome 版本，避免 UC 自动下载错误版本
    )
    print("[✓] 浏览器启动成功！")

    # 公开搜索页不强制登录，但如果配置了账密则尝试自动填写（防止被重定向到登录页）
    print(f"\n[*] 打开 {CNIPA_PUBLIC_SEARCH_URL}...")
    driver.get(CNIPA_PUBLIC_SEARCH_URL)
    time.sleep(3)

    # 尝试自动填写账密（若页面未重定向到登录页则 auto_fill_login 会快速返回 False，无副作用）
    username, password = load_credentials()
    if username and password:
        filled = auto_fill_login(driver, username, password)
        if filled:
            # 页面跳转到了登录页，等待用户完成验证码（与 Phase 0 相同的信号文件机制）
            if sys.stdin.isatty():
                input("\n登录完成后按 Enter 继续...")
            else:
                from settings import CNIPA_LOGIN_WAIT_SECONDS, DATA_DIR
                flag_file = DATA_DIR / 'login_ready.flag'
                flag_file.unlink(missing_ok=True)
                deadline = time.time() + CNIPA_LOGIN_WAIT_SECONDS
                print(f"⏳ 等待登录信号（最多 {int(CNIPA_LOGIN_WAIT_SECONDS)} 秒）...")
                print("💡 在 Dashboard 点击【我已完成验证码】继续")
                while time.time() < deadline:
                    if flag_file.exists():
                        flag_file.unlink(missing_ok=True)
                        print("✅ 收到登录完成信号，继续...")
                        break
                    time.sleep(0.8)
                else:
                    print(f"⏰ 等待超时，继续执行...")
            # 登录后重新导航到公开查询页
            driver.get(CNIPA_PUBLIC_SEARCH_URL)
            time.sleep(2)

    print("[✓] 页面加载完成")
    print()
    print("=" * 70)
    print("📌 使用说明")
    print("=" * 70)
    print("""
现在你可以在浏览器中进行以下操作：

1️⃣  输入查询条件（申请人、技术分类等）
2️⃣  点击'查询'按钮，出现结果后在 Dashboard 点击「我已完成查询设置」
3️⃣  Dashboard 点击「自动翻页」，脚本会自动逐页采集

✨ 所有翻页请求都会被公开查询 MITM 代理自动采集！

完成后：
  - 关闭浏览器
  - Dashboard 步骤 3 点击「导出公开结果」
""")
    print("=" * 70)
    print()

    return driver


def main():
    raise_system_exit_on_sigterm()
    print("\n" + "=" * 70)
    print("🚀 CNIPA 公开查询 - 浏览器启动工具")
    print("=" * 70)
    print()

    # 检查公开查询 MITM 代理
    print(f"[*] 检查公开查询 MITM 代理（{MITM_HOST}:{PUBLIC_MITM_PORT}）...")
    if not _check_public_proxy():
        print(f"[-] 公开查询 MITM 代理未运行（端口 {PUBLIC_MITM_PORT}）！")
        print()
        print("❌ 请先在 Dashboard 点击「公开代理」按钮，或在终端运行:")
        print("   python start_mitm_public_search.py")
        print()
        sys.exit(1)
    print(f"[✓] 公开查询 MITM 代理已在运行（{MITM_HOST}:{PUBLIC_MITM_PORT}）")
    print()

    driver = launch_chrome_with_proxy()

    # 保持浏览器打开，响应用户关闭或 Ctrl+C
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[*] 正在关闭浏览器...")
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        print("[✓] 浏览器已关闭")


if __name__ == "__main__":
    main()
