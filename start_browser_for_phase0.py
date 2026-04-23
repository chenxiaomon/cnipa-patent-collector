#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 0 浏览器启动脚本

功能：启动一个配置了 MITM 代理的 Chrome 浏览器
     用户可以手动浏览 CNIPA，按申请人搜索，翻页查看

使用方式：
  # 终端 1：启动 MITM 代理
  python start_mitm_proxy.py

  # 终端 2：启动配置好代理的浏览器
  python start_browser_for_phase0.py

然后浏览器会自动打开，配置好代理 127.0.0.1:8080

使用场景：
  1. 打开后，浏览器会自动忽略 HTTPS 证书错误
  2. 访问 CNIPA: https://cpquery.cponline.cnipa.gov.cn
  3. 登录并按申请人名称搜索
  4. 手动翻页浏览（MITM 会自动拦截 API，写入 patent_cache.json）
  5. 浏览完毕后，关闭浏览器
  6. 运行 import_from_cache.py 导入数据
"""

import os
import sys
import time

import undetected_chromedriver as uc
from browser_utils import check_mitm_proxy


def launch_browser_with_proxy(
    proxy_url: str = "http://127.0.0.1:8080",
    target_url: str = "https://cpquery.cponline.cnipa.gov.cn",
    max_retries: int = 3
) -> uc.Chrome:
    """
    启动配置了代理的 Chrome 浏览器

    Args:
        proxy_url: MITM 代理地址
        target_url: 目标网址
        max_retries: 重试次数

    Returns:
        uc.Chrome 驱动实例
    """
    for attempt in range(max_retries):
        try:
            print(f"\n[尝试 {attempt+1}/{max_retries}] 启动浏览器...")

            options = uc.ChromeOptions()

            # 基础选项
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

            # 配置 MITM 代理
            print(f"[*] 配置代理: {proxy_url}")
            options.add_argument(f"--proxy-server={proxy_url}")
            options.add_argument("--ignore-certificate-errors")
            options.add_argument("--ignore-certificate-errors-spki-list")

            # 启动浏览器
            driver = uc.Chrome(
                headless=False,
                options=options,
            )

            print("[✓] 浏览器启动成功!")

            # 打开目标网址
            print(f"\n[*] 打开: {target_url}")
            driver.get(target_url)
            time.sleep(2)

            print("[✓] 页面已加载")
            print("\n" + "="*70)
            print("📋 使用说明")
            print("="*70)
            print("""
✅ 浏览器已配置代理 (127.0.0.1:8080)
✅ HTTPS 证书错误已忽略
✅ MITM 代理会自动拦截 API 响应

操作步骤：
  1. 手动登录 CNIPA 账户
  2. 在搜索框中输入申请人名称（如"华为"、"小米"等）
  3. 点击搜索
  4. 逐页浏览查看结果（MITM 会自动拦截每页的 API）
  5. 浏览完毕后，关闭浏览器窗口

监控 MITM：
  在另一个终端运行：tail -f /tmp/mitmproxy.log（或查看 MITM 终端的输出）

导入数据：
  浏览完成后，在新终端运行：python import_from_cache.py
""")
            print("="*70)

            return driver

        except Exception as e:
            print(f"[✗] 启动失败: {str(e)[:100]}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"  {wait_time} 秒后重试...\n")
                time.sleep(wait_time)
            else:
                print(f"\n[❌] 所有 {max_retries} 次重试都失败了")
                raise RuntimeError("浏览器启动失败")


def main():
    """主函数"""
    print("\n" + "="*70)
    print("🌐 Phase 0 浏览器启动程序")
    print("="*70)

    # 检查 MITM 代理是否运行
    print("\n[*] 检查 MITM 代理状态...")
    if not check_mitm_proxy():
        print("[⚠️ ] MITM 代理未响应")
        print("请先在另一个终端运行：python start_mitm_proxy.py")
        sys.exit(1)
    print("[✓] MITM 代理 (127.0.0.1:8080) 已启动")

    # 启动浏览器
    print("\n[*] 启动浏览器...")
    try:
        driver = launch_browser_with_proxy()

        # 保持浏览器打开，直到用户关闭
        print("\n[*] 浏览器已打开，请手动操作...")
        print("[*] 关闭浏览器窗口退出本程序")

        # 监听浏览器窗口
        while True:
            try:
                # 尝试获取窗口句柄，如果失败说明浏览器已关闭
                _ = driver.window_handles
                time.sleep(1)
            except:
                print("\n[✓] 浏览器已关闭")
                break

    except KeyboardInterrupt:
        print("\n[*] 用户中断")
    except Exception as e:
        print(f"\n[!] 错误: {e}")
        sys.exit(1)
    finally:
        try:
            driver.quit()
        except:
            pass

    print("\n" + "="*70)
    print("✅ 浏览器已关闭")
    print("="*70)
    print("""
下一步：
  1. 检查 MITM 的输出，确认已拦截到数据
  2. 运行：python import_from_cache.py
  3. 导入缓存数据到 detection_log.json
  4. 继续其他采集任务
""")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n[!] 程序错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
