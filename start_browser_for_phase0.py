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

然后浏览器会自动打开，配置好代理 127.0.0.1:8082

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
from browser_utils import check_mitm_proxy, create_driver_with_retry, auto_fill_login, load_credentials
from settings import MITM_HOST, MITM_PORT


def launch_browser_with_proxy(
    proxy_url: str | None = None,
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
    if proxy_url is None:
        proxy_url = f"http://{MITM_HOST}:{MITM_PORT}"
    # create_driver_with_retry 内部处理重试和本地 chromedriver 检测
    driver = create_driver_with_retry(max_retries=max_retries, use_mitm=True)

    # 打开目标网址
    print(f"\n[*] 打开: {target_url}")
    driver.get(target_url)
    time.sleep(3)

    # 自动填写账密
    username, password = load_credentials()
    if username and password:
        filled = auto_fill_login(driver, username, password)
        if filled:
            print("\n" + "="*70)
            print("请在浏览器中完成验证码，然后点击【登录】按钮")
            print("登录成功后，回到这里按 Enter 继续...")
            print("="*70)
        else:
            print("[!] 自动填写失败，请手动登录后按 Enter 继续...")
    else:
        print("[!] 未找到登录凭证，请手动登录后按 Enter 继续...")
        print("    提示：在 .env 中填写 CNIPA_USERNAME / CNIPA_PASSWORD 可自动填写")

    input()

    print("\n" + "="*70)
    print("📋 使用说明")
    print("="*70)
    print("""
✅ 已登录 CNIPA
✅ MITM 代理会自动拦截每页 API 响应

操作步骤：
  1. 在搜索框中输入申请人名称，点击搜索
  2. 逐页浏览查看结果（终端 1 会显示拦截日志）
  3. 浏览完毕后，关闭浏览器窗口

导入数据：
  浏览完成后运行：python import_from_cache.py
""")
    print("="*70)

    return driver


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
    print(f"[✓] MITM 代理 ({MITM_HOST}:{MITM_PORT}) 已启动")

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
            except Exception:
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
        except Exception:
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
