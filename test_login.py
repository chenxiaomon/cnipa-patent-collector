#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
登录自动填写测试脚本
验证 .env 中的账密能否自动填入 CNIPA 登录页
"""

import os
import sys
import time

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from browser_utils import load_credentials, fill_vue_input

URL = "https://cpquery.cponline.cnipa.gov.cn/"


def main():
    username, password = load_credentials()
    if not username or not password:
        print("❌ .env 文件中没有找到凭证，请先填写 CNIPA_USERNAME 和 CNIPA_PASSWORD")
        sys.exit(1)

    print(f"\n[*] 账号: {username}")
    print("[*] 密码: " + "*" * len(password))

    print("\n[*] 启动浏览器...")
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = uc.Chrome(headless=False, options=options)

    print(f"[*] 打开 {URL}")
    driver.get(URL)
    time.sleep(3)

    try:
        wait = WebDriverWait(driver, 15)
        print("[*] 等待登录表单加载...")

        username_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[placeholder="代理机构代码"]'))
        )
        fill_vue_input(driver, username_input, username)
        print(f"[✓] 已填写代理机构代码: {username}")
        time.sleep(0.3)

        password_input = driver.find_element(By.CSS_SELECTOR, 'input[placeholder="请输入密码"]')
        fill_vue_input(driver, password_input, password)
        print("[✓] 已填写密码")

        print("\n" + "="*60)
        print("请在浏览器中完成验证码，然后点击【登录】按钮")
        print("登录成功后，回到这里按 Enter 退出测试...")
        print("="*60)
        input()

    except Exception as e:
        print(f"\n[!] 出错: {e}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("\n✅ 测试完成")


if __name__ == '__main__':
    main()
