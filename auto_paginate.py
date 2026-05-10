#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CNIPA 公开搜索自动翻页脚本
- 启动浏览器并配置代理
- 等待用户手动输入查询条件
- 自动翻页采集结果
"""

import argparse
import os
import sys
import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service


def create_driver():
    """创建浏览器驱动，配置代理"""
    print("\n" + "=" * 60)
    print("🚀 正在初始化 undetected_chromedriver...")
    print("=" * 60)

    try:
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")

        # 配置 MITM 代理
        print("[*] 配置代理: 127.0.0.1:8082")
        options.add_argument("--proxy-server=http://127.0.0.1:8082")
        options.add_argument("--ignore-certificate-errors")

        driver = uc.Chrome(
            headless=False,
            options=options,
        )

        print("[✓] 浏览器创建成功!")
        return driver

    except Exception as e:
        print(f"[❌] 浏览器初始化失败: {e}")
        sys.exit(1)


def wait_for_user_input():
    """等待用户手动输入查询条件并点击查询"""
    print("\n" + "=" * 60)
    print("⏸️  用户交互阶段")
    print("=" * 60)
    print()
    print("请在浏览器中执行以下操作:")
    print("1. 输入查询条件（申请人、技术分类等）")
    print("2. 点击'查询'按钮")
    print()
    print("完成后，在此终端按 Enter 键开始自动翻页...")
    print()

    input()  # 阻塞等待用户按 Enter

    print("\n[*] 开始自动翻页...")


def is_next_page_available(driver) -> bool:
    """检测'下一页'按钮是否可用"""
    try:
        # CSS 选择器：.ant-pagination-next[aria-disabled="false"]
        next_btn = driver.find_element(
            By.CSS_SELECTOR,
            ".ant-pagination-next[aria-disabled='false']"
        )
        return True
    except:
        return False


def click_next_page(driver):
    """点击下一页按钮"""
    try:
        next_btn = driver.find_element(By.CSS_SELECTOR, ".ant-pagination-next")
        next_btn.click()
        return True
    except Exception as e:
        print(f"[!] 点击下一页失败: {e}")
        return False


def wait_for_page_load(driver, timeout: int = 5):
    """等待页面加载完成"""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_all_elements_located((By.XPATH, "//table/tbody/tr"))
        )
        return True
    except:
        return False


def paginate_loop(driver, delay: float, max_pages: int):
    """自动翻页循环"""
    page_count = 1

    while page_count < max_pages:
        print(f"[*] 第 {page_count} 页 → 等待 {delay} 秒...")
        time.sleep(delay)

        # 检测是否还有下一页
        if not is_next_page_available(driver):
            print("[✓] 已到达最后一页，停止翻页")
            break

        # 点击下一页
        if not click_next_page(driver):
            print("[✗] 点击下一页失败，停止翻页")
            break

        # 等待页面加载
        time.sleep(0.5)  # 给页面一点反应时间
        wait_for_page_load(driver, timeout=5)

        page_count += 1
        print(f"[✓] 已翻到第 {page_count} 页")

    print(f"\n" + "=" * 60)
    print(f"✅ 翻页完成！共采集 {page_count} 页数据")
    print("=" * 60)
    print()
    print("后续步骤:")
    print("1. 关闭浏览器（关闭此脚本）")
    print("2. 运行导出脚本: python export_public_search.py")
    print()


def main():
    parser = argparse.ArgumentParser(description="CNIPA 公开搜索自动翻页脚本")
    parser.add_argument(
        "--delay",
        type=float,
        default=1.5,
        help="翻页延迟（秒），默认 1.5 秒"
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=50,
        help="最多翻多少页，默认 50 页"
    )
    parser.add_argument(
        "--test",
        type=int,
        default=None,
        help="测试模式：只翻指定页数（例如 --test 5）"
    )

    args = parser.parse_args()

    # 如果指定了 --test，覆盖 --max-pages
    max_pages = args.test if args.test else args.max_pages
    delay = args.delay

    print("\n" + "=" * 70)
    print("🔍 CNIPA 公开搜索自动翻页工具")
    print("=" * 70)
    print()
    print(f"配置参数:")
    print(f"  翻页延迟: {delay} 秒")
    print(f"  最大页数: {max_pages} 页")
    if args.test:
        print(f"  [测试模式]")
    print()

    # 创建浏览器
    driver = create_driver()

    try:
        # 打开 CNIPA 公开搜索页面
        print("\n[*] 打开 CNIPA 公开搜索页面...")
        cnipa_url = "https://cponline.cnipa.gov.cn/publicSearch"
        driver.get(cnipa_url)

        # 等待页面加载
        time.sleep(3)
        print("[✓] 页面加载完成")

        # 等待用户交互
        wait_for_user_input()

        # 自动翻页循环
        paginate_loop(driver, delay, max_pages)

        # 保持浏览器打开，等待用户关闭
        print("[*] 浏览器保持打开，按 Ctrl+C 关闭...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[*] 正在关闭浏览器...")

    except Exception as e:
        print(f"\n[❌] 执行出错: {e}")
        import traceback
        traceback.print_exc()

    finally:
        try:
            driver.quit()
            print("[✓] 浏览器已关闭")
        except:
            pass


if __name__ == "__main__":
    main()
