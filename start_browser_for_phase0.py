#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Phase 0 浏览器启动脚本

功能：启动一个配置了 MITM 代理的 Chrome 浏览器
     自动填写账密，等待用户完成验证码后手动翻页浏览 CNIPA

使用方式：
  # 终端 1：启动 MITM 代理
  python start_mitm_proxy.py

  # 终端 2：启动配置好代理的浏览器
  python start_browser_for_phase0.py

使用场景：
  1. 浏览器启动后自动打开 CNIPA 登录页并填写账密
  2. 用户完成验证码后（或在 Dashboard 点击【我已完成验证码】）继续
  3. 按申请人名称搜索，手动翻页
  4. MITM 自动拦截 API 写入 patent_cache.json
  5. 浏览完毕关闭浏览器，运行 import_from_cache.py 导入数据
"""

import sys
import time

from browser_service import BrowserService
from browser_utils import check_mitm_proxy, raise_system_exit_on_sigterm
from settings import MITM_HOST, MITM_PORT, CNIPA_URL


def main():
    raise_system_exit_on_sigterm()
    print("\n" + "=" * 70)
    print("🌐 Phase 0 浏览器启动程序")
    print("=" * 70)

    # 检查主 MITM 代理（8083）
    print(f"\n[*] 检查 MITM 代理状态（{MITM_HOST}:{MITM_PORT}）...")
    if not check_mitm_proxy():
        print("[⚠️ ] MITM 代理未响应")
        print(f"请先在 Dashboard 点击【启动主代理】，或在终端运行：python start_mitm_proxy.py")
        sys.exit(1)
    print(f"[✓] MITM 代理已启动")

    # 启动浏览器 + 自动登录（BrowserService 统一处理 stdin/信号文件两种等待方式）
    print("\n[*] 启动浏览器并自动填写账密...")
    try:
        driver = BrowserService.launch_and_login(CNIPA_URL)
    except Exception as e:
        print(f"\n[❌] 浏览器启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 70)
    print("📋 使用说明")
    print("=" * 70)
    print("""
✅ 已登录 CNIPA
✅ MITM 代理会自动拦截每页 API 响应

操作步骤：
  1. 在搜索框中输入申请人名称，点击搜索
  2. 逐页浏览查看结果（终端会显示拦截日志）
  3. 浏览完毕后，关闭浏览器窗口

导入数据：
  浏览完成后运行：python import_from_cache.py
""")
    print("=" * 70)

    # 监听浏览器窗口，直到用户关闭
    print("\n[*] 浏览器已打开，关闭浏览器窗口退出本程序")
    try:
        while True:
            try:
                _ = driver.window_handles
                time.sleep(1)
            except Exception:
                print("\n[✓] 浏览器已关闭")
                break
    except KeyboardInterrupt:
        print("\n[*] 用户中断")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("✅ 浏览器已关闭")
    print("=" * 70)
    print("""
下一步：
  1. 检查 MITM 的输出，确认已拦截到数据
  2. 运行：python import_from_cache.py
""")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n[!] 程序错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
