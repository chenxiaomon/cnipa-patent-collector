#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动 MITM 代理服务器
用于拦截浏览器请求和响应
"""

import sys
import os

# 在任何 print 之前强制 UTF-8，防止 emoji/中文在 GBK 终端崩溃
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保我们在正确的目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from settings import MITM_HOST, MITM_PORT

print("="*70)
print("🔐 MITM 代理服务器启动")
print("="*70)
print()

try:
    from mitmproxy.tools import main

    print("[+] 启动 mitmproxy...")
    print(f"[*] 监听地址: {MITM_HOST}:{MITM_PORT}")
    print("[*] 脚本文件: patent_mitm_scraper.py")
    print()
    print("📝 配置浏览器代理:")
    print(f"  - 代理地址: {MITM_HOST}")
    print(f"  - 端口: {MITM_PORT}")
    print("  - 协议: HTTP 和 HTTPS")
    print()
    print("⚠️  HTTPS 需要信任 mitmproxy 的 CA 证书:")
    print("  - Firefox: 手动添加例外")
    print("  - Chrome: 需要系统信任证书或使用启用标志")
    print()
    print("按 Ctrl+C 停止服务器")
    print()

    # 启动 mitmdump
    sys.argv = [
        "mitmdump",
        "-s", "patent_mitm_scraper.py",
        "-p", str(MITM_PORT),
        "--listen-host", MITM_HOST,
        "--mode", "regular",
    ]

    main.mitmdump()

except KeyboardInterrupt:
    print("\n[*] 服务器已停止")
    sys.exit(0)
except ImportError as e:
    print(f"[!] 依赖错误: {e}")
    print("[!] 请确保已安装 mitmproxy:")
    print("    uv sync")
    sys.exit(1)
except Exception as e:
    print(f"[!] 错误: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
