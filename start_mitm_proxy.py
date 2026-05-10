#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动 MITM 代理服务器
用于拦截浏览器请求和响应
"""

import sys
import os
import subprocess

# 确保我们在正确的目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("="*70)
print("🔐 MITM 代理服务器启动")
print("="*70)
print()

try:
    from mitmproxy.tools import main

    print("[+] 启动 mitmproxy...")
    print("[*] 监听地址: 127.0.0.1:8082")
    print("[*] 脚本文件: patent_mitm_scraper.py")
    print()
    print("📝 配置浏览器代理:")
    print("  - 代理地址: 127.0.0.1")
    print("  - 端口: 8082")
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
        "-p", "8082",
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
