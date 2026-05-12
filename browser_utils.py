#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器工具函数（跨脚本复用）
- 凭证加载
- Vue.js 输入触发
- 浏览器存活检测
- 反爬虫输入模拟
- 带重试的 Chrome 驱动创建
- MITM 代理端口检测
"""

import os
import sys
import time
import random
import socket

import pyautogui
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def load_credentials() -> tuple[str, str]:
    """从 .env 文件或环境变量加载登录凭证，返回 (username, password)"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                os.environ.setdefault(key.strip(), val.strip())
    return os.getenv('CNIPA_USERNAME', ''), os.getenv('CNIPA_PASSWORD', '')


def clear_input_field() -> None:
    """全选输入框内容并删除：macOS 用 command+a，其他用 ctrl+a，再 backspace"""
    select_all = 'command' if sys.platform == 'darwin' else 'ctrl'
    pyautogui.hotkey(select_all, 'a')
    time.sleep(0.15)
    pyautogui.press('backspace')
    time.sleep(0.2)


def fill_vue_input(driver, element, value: str) -> None:
    """通过 JS 触发 Vue.js 的 input 事件，避免 send_keys 无法响应式更新的问题"""
    driver.execute_script(
        "var n=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        "n.call(arguments[0],arguments[1]);"
        "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
        element, value
    )


def is_browser_alive(driver) -> bool:
    """检测浏览器是否仍在运行（通过 window_handles 判断连接是否存活）"""
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def check_mitm_proxy(host: str = "127.0.0.1", port: int = 8082) -> bool:
    """检查 MITM 代理是否在运行"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def auto_fill_login(driver, username: str, password: str) -> bool:
    """
    自动填写代理机构代码和密码。

    Vue.js 需要通过 JS 触发 input 事件才能响应式更新，
    所以不能直接用 send_keys，而是通过 fill_vue_input 触发。

    Returns:
        True: 填写成功，等待用户完成验证码后按 Enter
        False: 填写失败，需用户手动登录
    """
    try:
        wait = WebDriverWait(driver, 15)

        print("\n[*] 等待登录页面加载...")
        username_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder="代理机构代码"]'))
        )

        fill_vue_input(driver, username_input, username)
        print(f"[✓] 已填写代理机构代码: {username}")
        time.sleep(0.3)

        password_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')
        fill_vue_input(driver, password_input, password)
        print("[✓] 已填写密码")

        return True

    except Exception as e:
        print(f"[!] 自动填写失败: {e}")
        return False


def real_type(text: str, delay_range: tuple = (0.03, 0.08), pause_prob: float = 0.08) -> None:
    """
    逐字输入文本，模拟真人输入以绕过反爬虫

    Args:
        text: 要输入的文本
        delay_range: 字符间延迟范围（秒）；collect_fwxx 流程传 (0.05, 0.18)
        pause_prob: 随机长延迟的触发概率；collect_fwxx 流程传 0.15
    """
    for char in text:
        if random.random() < pause_prob:
            time.sleep(random.uniform(0.2, 0.5))
        if char.isupper():
            pyautogui.hotkey('shift', char.lower())
        elif char == '.':
            pyautogui.press('.')
        else:
            pyautogui.press(char)
        time.sleep(random.uniform(*delay_range))


def create_driver_with_retry(max_retries: int = 3, use_mitm: bool = None) -> uc.Chrome:
    """
    创建 undetected_chromedriver 浏览器实例（带重试和本地 ChromeDriver 自动检测）

    Args:
        max_retries: 最大重试次数
        use_mitm: 是否启用 MITM 代理；None 时读取 USE_MITM_PROXY 环境变量
    """
    if use_mitm is None:
        use_mitm = os.getenv('USE_MITM_PROXY', '').lower() in ('true', '1', 'yes')

    # 自动检测本地 ChromeDriver
    local_driver_path = None
    if sys.platform == 'win32':
        candidate = os.path.join(os.path.dirname(__file__), 'chromedriver-win64', 'chromedriver.exe')
    else:
        candidate = os.path.join(os.path.dirname(__file__), 'chromedriver-linux64', 'chromedriver')
    if os.path.exists(candidate):
        local_driver_path = candidate

    for attempt in range(max_retries):
        try:
            print(f"\n[尝试 {attempt+1}/{max_retries}] 启动浏览器...")

            options = uc.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")

            if use_mitm:
                print("[*] 启用 MITM 代理: 127.0.0.1:8082")
                options.add_argument("--proxy-server=http://127.0.0.1:8082")
                options.add_argument("--ignore-certificate-errors")

            kwargs = dict(headless=False, options=options)
            if local_driver_path:
                print(f"[*] 使用本地 ChromeDriver: {local_driver_path}")
                kwargs['driver_executable_path'] = local_driver_path

            driver = uc.Chrome(**kwargs)
            print("[✓] 浏览器创建成功!")
            return driver

        except Exception as e:
            print(f"[✗] 失败: {str(e)[:80]}")
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"  {wait_time} 秒后重试...\n")
                time.sleep(wait_time)
            else:
                raise RuntimeError("浏览器初始化失败")
