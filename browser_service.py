#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
浏览器服务 - 统一处理浏览器启动和登录流程
"""

import os
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from browser_utils import (
    load_credentials, auto_fill_login, create_driver_with_retry, fill_vue_input,
)
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from collection_health import record_collection_alert

from settings import (
    BROWSER_PAGE_LOAD_TIMEOUT,
    CNIPA_LOGIN_WAIT_SECONDS, CNIPA_URL, LOGIN_READY_FLAG_FILE,
    USE_VIRTUAL_DISPLAY, VIRTUAL_DISPLAY_WIDTH, VIRTUAL_DISPLAY_HEIGHT,
)

# 虚拟显示器实例（模块级单例）
_virtual_display = None
# 启动虚拟显示器前保存的真实 DISPLAY（用于在物理桌面弹出截图）
_original_display = None


class LoginConfirmationRequired(RuntimeError):
    """Private collection must not start without an operator-confirmed login."""


def start_virtual_display() -> None:
    """启动 Xvfb 虚拟显示器，将 DISPLAY 环境变量切换到虚拟屏幕"""
    global _virtual_display, _original_display
    if _virtual_display is not None:
        return

    try:
        from pyvirtualdisplay import Display
    except ImportError:
        print("⚠️  pyvirtualdisplay 未安装，跳过虚拟显示器（仍使用物理桌面）")
        if sys.platform.startswith('linux'):
            print("   安装命令: pip install pyvirtualdisplay && sudo apt-get install -y xvfb")
        else:
            print("   （Windows 不需要虚拟显示器，将使用物理屏幕）")
        return

    _original_display = os.environ.get('DISPLAY', ':0')
    _virtual_display = Display(
        visible=False,
        size=(VIRTUAL_DISPLAY_WIDTH, VIRTUAL_DISPLAY_HEIGHT),
        color_depth=24,
    )
    _virtual_display.start()
    print(f"✓ 虚拟显示器已启动 ({VIRTUAL_DISPLAY_WIDTH}x{VIRTUAL_DISPLAY_HEIGHT})，物理桌面已释放")


def stop_virtual_display() -> None:
    """关闭 Xvfb 虚拟显示器"""
    global _virtual_display
    if _virtual_display is not None:
        try:
            _virtual_display.stop()
        except Exception:
            pass
        _virtual_display = None


class BrowserService:
    """统一管理浏览器创建和登录"""

    @staticmethod
    def launch_and_login(url: str, page_load_wait: float = 15.0) -> object:
        """
        创建浏览器、打开 URL、自动填写账密、等待用户完成验证码

        Args:
            url: 目标 URL
            page_load_wait: 打开页面后的等待秒数（默认 15s，给 WAF 挑战留足时间）

        Returns:
            已完成登录的 WebDriver 实例
        """
        driver = create_driver_with_retry()
        try:
            driver.set_page_load_timeout(BROWSER_PAGE_LOAD_TIMEOUT)
            try:
                driver.get(url)
            except TimeoutException as error:
                raise RuntimeError(
                    f"{url} 在 {BROWSER_PAGE_LOAD_TIMEOUT:.0f} 秒内未加载完成；"
                    "检查网络，以及 MITM 代理是否正常转发"
                ) from error
            time.sleep(page_load_wait)
            print("\n✓ 浏览器已打开")

            BrowserService._do_login(driver)
            return driver
        except BaseException as error:
            # The caller has not received the driver yet, so startup owns cleanup.
            try:
                driver.quit()
            except Exception:
                pass
            if isinstance(error, LoginConfirmationRequired):
                print(f"[LOGIN_REQUIRED] {error}")
                record_collection_alert('login_required', str(error), 0)
            raise

    @staticmethod
    def _do_login(driver) -> None:
        """自动填写账密，等待用户完成验证码后按 Enter"""
        LOGIN_READY_FLAG_FILE.unlink(missing_ok=True)
        username, password = load_credentials()
        if username and password:
            filled = auto_fill_login(driver, username, password)
            if filled:
                if USE_VIRTUAL_DISPLAY:
                    BrowserService._virtual_display_captcha(driver)
                print("\n" + "="*60)
                print("请在浏览器中完成验证码，然后点击【登录】按钮")
                print("登录成功后，回到这里按 Enter 继续...")
                print("="*60)
            else:
                print("\n⚠️  自动填写失败，请手动登录")
                if USE_VIRTUAL_DISPLAY:
                    BrowserService._show_virtual_screenshot(driver, "login_failed.png")
        else:
            print("\n⚠️  未找到登录凭证，请手动登录")
            print("提示：在 .env 文件中填写 CNIPA_USERNAME 和 CNIPA_PASSWORD 可自动填写账密")
            if USE_VIRTUAL_DISPLAY:
                BrowserService._show_virtual_screenshot(driver, "login_page.png")

        print("[WAITING_FOR_LOGIN] 等待操作员确认登录完成")
        try:
            if sys.stdin.isatty():
                try:
                    input("登录完成后按 Enter 继续...")
                except EOFError as error:
                    raise LoginConfirmationRequired("未收到登录确认，已停止采集") from error
            else:
                deadline = time.monotonic() + CNIPA_LOGIN_WAIT_SECONDS
                print(f"请在控制台确认登录（最多 {int(CNIPA_LOGIN_WAIT_SECONDS)} 秒）")
                while not LOGIN_READY_FLAG_FILE.exists():
                    if time.monotonic() >= deadline:
                        raise LoginConfirmationRequired(
                            "等待登录确认超时，已停止采集；请重新启动任务并完成登录"
                        )
                    time.sleep(0.8)
            BrowserService._verify_confirmed_login(driver)
            print("[LOGIN_CONFIRMED] 登录已由操作员确认，登录表单已退出")
        finally:
            LOGIN_READY_FLAG_FILE.unlink(missing_ok=True)

    @staticmethod
    def _verify_confirmed_login(driver) -> None:
        # Absence of a password field is only a veto check after human confirmation,
        # never evidence that an unattended session has authenticated successfully.
        def confirmed_page_ready(browser):
            if urlparse(browser.current_url).hostname != urlparse(CNIPA_URL).hostname:
                return False
            if not browser.execute_script(
                "return document.readyState === 'complete' && "
                "!!document.body && document.body.innerText.trim().length > 0;"
            ):
                return False
            login_inputs = browser.find_elements(
                By.CSS_SELECTOR,
                'input[type="password"], input[placeholder="请输入密码"], '
                'input[placeholder="代理机构代码"]',
            )
            return not any(element.is_displayed() for element in login_inputs)

        try:
            WebDriverWait(
                driver, 10, ignored_exceptions=(StaleElementReferenceException,)
            ).until(confirmed_page_ready)
        except TimeoutException as error:
            raise LoginConfirmationRequired(
                "登录确认后仍停留在登录页或页面未就绪，已停止采集"
            ) from error

    @staticmethod
    def _show_virtual_screenshot(driver, filename: str = "screenshot.png") -> None:
        """截虚拟屏幕并在物理桌面弹出，让用户看到当前页面状态"""
        path = str(Path(tempfile.gettempdir()) / f'cnipa_{filename}')
        driver.save_screenshot(path)
        try:
            webbrowser.open(Path(path).as_uri())
            print(f"\n✓ 当前页面截图已在桌面弹出: {path}")
            # 30 秒后自动清理临时截图文件
            def _cleanup(p=path):
                import time as _time
                _time.sleep(30)
                try:
                    os.unlink(p)
                except OSError:
                    pass
            threading.Thread(target=_cleanup, daemon=True).start()
        except FileNotFoundError:
            print(f"\n截图已保存: {path}（请手动用图片查看器打开）")

    @staticmethod
    def _virtual_display_captcha(driver) -> None:
        """
        虚拟显示模式：截取登录页截图，在物理桌面弹出，
        让用户在终端输入验证码，再自动填入并提交。
        """
        BrowserService._show_virtual_screenshot(driver, "captcha.png")
        if not sys.stdin.isatty():
            return

        print("\n" + "="*60)
        print("请查看弹出的截图，找到图片验证码���4 位字母数字）")
        print("="*60)
        captcha_code = input("请输入验证码后按 Enter: ").strip()

        if captcha_code:
            BrowserService._fill_captcha_and_submit(driver, captcha_code)

    @staticmethod
    def _fill_captcha_and_submit(driver, captcha_code: str) -> None:
        """填写验证码并点击登录按钮"""
        from selenium.webdriver.common.by import By

        # CNIPA 登录页的验证码输入框（按优先级尝试）
        captcha_selectors = [
            'input[placeholder="验证码"]',
            'input[placeholder="请输入验证码"]',
            'input[name="captcha"]',
            'input[id*="captcha"]',
            'input[type="text"][maxlength="4"]',
            'input[type="text"][maxlength="6"]',
        ]
        login_button_selectors = [
            'button[type="submit"]',
            'button.login-btn',
            '.login-form button',
            'button.el-button--primary',
            'input[type="submit"]',
        ]

        captcha_input = None
        for selector in captcha_selectors:
            try:
                for el in driver.find_elements(By.CSS_SELECTOR, selector):
                    if el.is_displayed():
                        captcha_input = el
                        break
                if captcha_input:
                    break
            except Exception:
                continue

        if captcha_input:
            fill_vue_input(driver, captcha_input, captcha_code)
            print(f"[✓] 已填写验证码: {captcha_code}")
            time.sleep(0.3)
        else:
            print("⚠️  未自动找到验证码输入框，如截图中有验证码请手动处理")

        # 点击登录按钮
        for selector in login_button_selectors:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    print("[✓] 已点击登录按钮，等待跳转...")
                    time.sleep(3)
                    break
            except Exception:
                continue
