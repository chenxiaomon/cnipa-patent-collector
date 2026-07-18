"""
坐标管理服务 - 统一处理搜索页和发文信息页的鼠标坐标
"""

import os
import json
import time
from datetime import datetime
import pyautogui

from settings import CONFIG_FILE, CONFIG_FWXX_FILE
from atomic_write import write_json_atomic


class CoordinateService:
    """统一管理鼠标坐标的加载和记录"""

    @staticmethod
    def load_or_record_search_coordinates():
        """
        加载或记录搜索页坐标（申请号输入框和查询按钮）

        Returns:
            tuple: (input_x, input_y, button_x, button_y)
        """
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print("\n✓ 从配置文件加载鼠标位置")
                    print(f"  输入框: ({config['input_x']}, {config['input_y']})")
                    print(f"  按钮: ({config['button_x']}, {config['button_y']})")
                    return config['input_x'], config['input_y'], config['button_x'], config['button_y']
            except Exception as e:
                print(f"⚠️  配置文件读取失败: {e}")

        return CoordinateService._record_search_coordinates()

    @staticmethod
    def _record_search_coordinates():
        """手动记录搜索页坐标"""
        print("\n" + "="*60)
        print("📍 鼠标位置记录 - 搜索页")
        print("="*60)
        print("⚠️  紧急停止: 把鼠标甩到屏幕左上角")

        print("\n▶ 请把鼠标移到 [申请号输入框] 的中间")
        CoordinateService._countdown(8)
        input_x, input_y = pyautogui.position()
        print(f"  ✓ 输入框坐标: ({input_x}, {input_y})   ")

        print("\n▶ 请把鼠标移到 [查询按钮] 的中间")
        CoordinateService._countdown(8)
        button_x, button_y = pyautogui.position()
        print(f"  ✓ 按钮坐标: ({button_x}, {button_y})   ")

        # 保存到配置文件
        config = {
            'input_x': input_x,
            'input_y': input_y,
            'button_x': button_x,
            'button_y': button_y,
            'last_updated': datetime.now().isoformat()
        }
        try:
            write_json_atomic(CONFIG_FILE, config)
            print("\n✓ 位置已保存到配置文件")
        except Exception as e:
            print(f"\n⚠️  保存配置失败: {e}")

        return input_x, input_y, button_x, button_y

    @staticmethod
    def load_or_record_fwxx_coordinates():
        """
        加载或记录发文信息页坐标（发文链接和菜单位置）

        Returns:
            tuple: (link_x, link_y, fwxx_menu_x, fwxx_menu_y)
        """
        if os.path.exists(CONFIG_FWXX_FILE):
            try:
                with open(CONFIG_FWXX_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    print("\n✓ 从配置文件加载发文信息页鼠标位置")
                    print(f"  发文链接: ({config['link_x']}, {config['link_y']})")
                    print(f"  菜单: ({config['fwxx_menu_x']}, {config['fwxx_menu_y']})")
                    return config['link_x'], config['link_y'], config['fwxx_menu_x'], config['fwxx_menu_y']
            except Exception as e:
                print(f"⚠️  配置文件读取失败: {e}")

        return CoordinateService._record_fwxx_coordinates()

    @staticmethod
    def _record_fwxx_coordinates():
        """手动记录发文信息页坐标"""
        print("\n" + "="*60)
        print("📍 鼠标位置记录 - 发文信息页")
        print("="*60)
        print("⚠️  紧急停止: 把鼠标甩到屏幕左上角")

        print("\n▶ 第一步：打开任意一个申请号的详情页")
        print("  进入发文信息页（需要手动点击）")
        print("  准备好后，请把鼠标移到 [发文链接（小链接图标）] 的位置")
        CoordinateService._countdown(15, "等待用户打开发文页面并移动鼠标")
        link_x, link_y = pyautogui.position()
        print(f"  ✓ 发文链接坐标: ({link_x}, {link_y})   ")

        print("\n▶ 第二步：请把鼠标移到 [发文信息菜单] 的位置（通常在左侧菜单栏）")
        CoordinateService._countdown(15, "等待用户移动鼠标到菜单位置")
        fwxx_menu_x, fwxx_menu_y = pyautogui.position()
        print(f"  ✓ 菜单坐标: ({fwxx_menu_x}, {fwxx_menu_y})   ")

        # 保存到配置文件
        config = {
            'link_x': link_x,
            'link_y': link_y,
            'fwxx_menu_x': fwxx_menu_x,
            'fwxx_menu_y': fwxx_menu_y,
            'last_updated': datetime.now().isoformat()
        }
        try:
            write_json_atomic(CONFIG_FWXX_FILE, config)
            print("\n✓ 位置已保存到配置文件")
        except Exception as e:
            print(f"\n⚠️  保存配置失败: {e}")

        return link_x, link_y, fwxx_menu_x, fwxx_menu_y

    @staticmethod
    def load_or_record_fee_menu_coordinates():
        """加载费用信息菜单坐标；旧配置缺少该坐标时只补录这一项。"""
        config = {}
        if os.path.exists(CONFIG_FWXX_FILE):
            try:
                with open(CONFIG_FWXX_FILE, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                if 'fee_menu_x' in config and 'fee_menu_y' in config:
                    print("\n✓ 从配置文件加载费用信息菜单位置")
                    print(f"  菜单: ({config['fee_menu_x']}, {config['fee_menu_y']})")
                    return config['fee_menu_x'], config['fee_menu_y']
            except Exception as e:
                print(f"⚠️  配置文件读取失败: {e}")

        return CoordinateService._record_fee_menu_coordinates(config)

    @staticmethod
    def _record_fee_menu_coordinates(config: dict):
        """补录详情页左侧的费用信息菜单坐标。"""
        print("\n" + "="*60)
        print("📍 鼠标位置记录 - 费用信息菜单")
        print("="*60)
        print("⚠️  紧急停止: 把鼠标甩到屏幕左上角")
        print("\n▶ 详情页已自动打开，请把鼠标移到左侧 [费用信息] 菜单")
        CoordinateService._countdown(20, "等待用户移动鼠标到费用信息菜单")
        fee_menu_x, fee_menu_y = pyautogui.position()
        print(f"  ✓ 费用信息菜单: ({fee_menu_x}, {fee_menu_y})   ")

        updated_config = dict(config) if isinstance(config, dict) else {}
        updated_config.update({
            'fee_menu_x': fee_menu_x,
            'fee_menu_y': fee_menu_y,
            'last_updated': datetime.now().isoformat(),
        })
        try:
            write_json_atomic(CONFIG_FWXX_FILE, updated_config)
            print("\n✓ 费用信息菜单位置已保存")
        except Exception as e:
            print(f"\n⚠️  保存配置失败: {e}")

        return fee_menu_x, fee_menu_y

    @staticmethod
    def _countdown(seconds: int, message: str = "请手动记录坐标，倒计时"):
        """倒计时提示"""
        for i in range(seconds, 0, -1):
            print(f"\r{message}: {i:2d} 秒...", end="", flush=True)
            time.sleep(1)
        print(f"\r{message}: 0 秒...完成！    ")
