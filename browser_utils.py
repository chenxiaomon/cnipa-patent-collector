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
import re
import glob
import subprocess

import pyautogui
from settings import MITM_HOST, MITM_PORT, USE_MITM_PROXY, USE_VIRTUAL_DISPLAY
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def _major_version_from_text(version_text: str) -> int | None:
    version_match = re.search(r'(\d+)\.\d+', version_text or '')
    if version_match:
        return int(version_match.group(1))
    return None


def _get_windows_chrome_major_version() -> int | None:
    try:
        import winreg
    except ImportError:
        return None

    registry_locations = [
        (winreg.HKEY_CURRENT_USER, r'Software\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Google\Chrome\BLBeacon'),
    ]
    for registry_root, registry_path in registry_locations:
        try:
            with winreg.OpenKey(registry_root, registry_path) as key:
                version_text, _ = winreg.QueryValueEx(key, 'version')
            chrome_major_version = _major_version_from_text(version_text)
            if chrome_major_version:
                return chrome_major_version
        except OSError:
            continue

    chrome_roots = [
        r'C:\Program Files\Google\Chrome\Application',
        r'C:\Program Files (x86)\Google\Chrome\Application',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application'),
    ]
    for chrome_root in chrome_roots:
        if not os.path.isdir(chrome_root):
            continue
        for folder_name in os.listdir(chrome_root):
            chrome_major_version = _major_version_from_text(folder_name)
            if chrome_major_version:
                return chrome_major_version
    return None


def _get_chrome_major_version() -> int | None:
    """检测系统 Chrome 主版本号，供 undetected_chromedriver 使用"""
    if sys.platform == 'win32':
        registry_major_version = _get_windows_chrome_major_version()
        if registry_major_version:
            return registry_major_version

        win_candidates = [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        ]
        for path in win_candidates:
            if os.path.isfile(path):
                try:
                    r = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
                    chrome_major_version = _major_version_from_text(r.stdout)
                    if chrome_major_version:
                        return chrome_major_version
                except subprocess.TimeoutExpired:
                    pass
        return None

    for cmd in ['google-chrome', 'google-chrome-stable', 'chromium-browser', 'chromium']:
        try:
            r = subprocess.run([cmd, '--version'], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                chrome_major_version = _major_version_from_text(r.stdout)
                if chrome_major_version:
                    return chrome_major_version
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def _get_chromedriver_major_version(driver_path: str) -> int | None:
    try:
        completed_process = subprocess.run(
            [driver_path, '--version'],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _major_version_from_text(completed_process.stdout)


def _find_matching_chromedriver(chrome_major_version: int | None) -> str | None:
    if not chrome_major_version:
        return None

    if sys.platform == 'win32':
        driver_patterns = [
            os.path.join(os.path.dirname(__file__), 'chromedriver-win64', 'chromedriver.exe'),
            os.path.expandvars(
                r'%LOCALAPPDATA%\Temp\chromedriver-win64-*\chromedriver-win64\chromedriver.exe'
            ),
        ]
    else:
        driver_patterns = [
            os.path.join(os.path.dirname(__file__), 'chromedriver-linux64', 'chromedriver'),
            '/tmp/chromedriver-linux64-*/chromedriver-linux64/chromedriver',
        ]

    for driver_pattern in driver_patterns:
        for driver_path in glob.glob(driver_pattern):
            if not os.path.exists(driver_path):
                continue
            driver_major_version = _get_chromedriver_major_version(driver_path)
            if driver_major_version == chrome_major_version:
                return driver_path
    return None


def load_credentials() -> tuple[str, str]:
    """从 .env 文件或环境变量加载登录凭证，返回 (username, password)"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    credentials = {}
    if os.path.exists(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                credentials[key.strip()] = val.strip()
    return (
        credentials.get('CNIPA_USERNAME') or os.getenv('CNIPA_USERNAME', ''),
        credentials.get('CNIPA_PASSWORD') or os.getenv('CNIPA_PASSWORD', ''),
    )


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


def check_mitm_proxy(host: str = MITM_HOST, port: int = MITM_PORT) -> bool:
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
        wait = WebDriverWait(driver, 45)

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
        use_mitm = USE_MITM_PROXY

    # 自动检测本地 ChromeDriver
    chrome_ver = _get_chrome_major_version()
    matching_driver_path = _find_matching_chromedriver(chrome_ver)

    for attempt in range(max_retries):
        try:
            print(f"\n[尝试 {attempt+1}/{max_retries}] 启动浏览器...")

            options = uc.ChromeOptions()
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")

            if USE_VIRTUAL_DISPLAY:
                options.add_argument("--disable-software-rasterizer")
                options.add_argument("--no-first-run")
                options.add_argument("--start-maximized")

            if use_mitm:
                print(f"[*] 启用 MITM 代理: {MITM_HOST}:{MITM_PORT}")
                options.add_argument(f"--proxy-server=http://{MITM_HOST}:{MITM_PORT}")
                options.add_argument("--ignore-certificate-errors")

            kwargs = dict(headless=False, options=options)
            if matching_driver_path:
                print(f"[*] 使用 Chrome {chrome_ver} 匹配的 ChromeDriver: {matching_driver_path}")
                kwargs['driver_executable_path'] = matching_driver_path
            else:
                if chrome_ver:
                    kwargs['version_main'] = chrome_ver
                    print(f"[*] Chrome {chrome_ver}，指定匹配的 ChromeDriver")
                else:
                    print("[*] 未检测到 Chrome 主版本，使用 UC 默认驱动")

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
