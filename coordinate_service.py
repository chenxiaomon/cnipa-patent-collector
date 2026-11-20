"""
坐标管理服务 - 统一处理搜索页和发文信息页的鼠标坐标
"""

from __future__ import annotations

import ctypes
import os
import json
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
import pyautogui

from settings import (
    BROWSER_WINDOW_HEIGHT, BROWSER_WINDOW_WIDTH,
    BROWSER_WINDOW_X, BROWSER_WINDOW_Y, CONFIG_FILE, CONFIG_FWXX_FILE,
)
from atomic_write import write_json_atomic
from coordinate_config import (
    DETAIL_LINK_COORDINATE_PAIRS, FEE_MENU_COORDINATE_PAIRS,
    FWXX_COORDINATE_PAIRS, SEARCH_COORDINATE_PAIRS,
    coordinate_configuration_issues, recorded_coordinates, validate_coordinate_config,
)


_BROWSER_TOOLBAR_GUARD_HEIGHT = 125
_TARGET_CLICK_TIMEOUT_SECONDS = 60.0
_FOREGROUND_WINDOW_TIMEOUT_SECONDS = 60.0
_FOREGROUND_WINDOW_POLL_INTERVAL_SECONDS = 0.1
_LEFT_BUTTON_VIRTUAL_KEY = 0x01
_CNIPA_WINDOW_TITLE_MARKER = "专利审查信息查询"
_CHROME_TOP_LEVEL_WINDOW_CLASS = "Chrome_WidgetWin_1"
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_GA_ROOT = 2


class CoordinateConfigurationError(ValueError):
    """Coordinates are missing, unsafe, or bound to another desktop layout."""


def _configured_window_geometry() -> dict[str, int]:
    return {
        "x": BROWSER_WINDOW_X,
        "y": BROWSER_WINDOW_Y,
        "width": BROWSER_WINDOW_WIDTH,
        "height": BROWSER_WINDOW_HEIGHT,
    }


def _current_screen_geometry() -> dict[str, int]:
    screen_width, screen_height = pyautogui.size()
    return {"width": int(screen_width), "height": int(screen_height)}


def _load_coordinate_config(config_path: Path) -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as config_stream:
            coordinate_config = json.load(config_stream)
    except FileNotFoundError as error:
        raise CoordinateConfigurationError(
            f"坐标配置不存在：{config_path}；请先运行坐标校准"
        ) from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CoordinateConfigurationError(
            f"坐标配置无法读取：{config_path}；请重新运行坐标校准"
        ) from error
    if not isinstance(coordinate_config, dict):
        raise CoordinateConfigurationError("坐标配置必须是 JSON 对象")
    return coordinate_config


def _require_recorded_coordinates(
    coordinate_config: dict,
    coordinate_pairs: tuple,
    coordinate_label: str,
) -> tuple[int, ...]:
    coordinate_issues = coordinate_configuration_issues(coordinate_config, coordinate_pairs)
    if coordinate_issues:
        raise CoordinateConfigurationError(
            f"{coordinate_label}：{'；'.join(coordinate_issues)}；请先运行坐标校准"
        )
    return tuple(coordinate_config[key] for pair in coordinate_pairs for key in pair)


def _browser_page_minimum_y() -> int:
    # Chrome tabs, address bar, and bookmarks occupy about 125 px; constrain
    # the guard proportionally for windows shorter than the supported layout.
    scaled_guard = max(1, (BROWSER_WINDOW_HEIGHT - 1) // 5)
    return BROWSER_WINDOW_Y + min(_BROWSER_TOOLBAR_GUARD_HEIGHT, scaled_guard)


def _validate_page_target(
    coordinate_label: str,
    target: tuple[int, int],
    screen_geometry: dict[str, int],
) -> None:
    target_x, target_y = target
    if not (
        0 <= target_x < screen_geometry["width"]
        and 0 <= target_y < screen_geometry["height"]
    ):
        raise CoordinateConfigurationError(
            f"{coordinate_label}坐标 {target} 超出当前屏幕范围"
        )
    if not (
        BROWSER_WINDOW_X <= target_x < BROWSER_WINDOW_X + BROWSER_WINDOW_WIDTH
        and BROWSER_WINDOW_Y <= target_y < BROWSER_WINDOW_Y + BROWSER_WINDOW_HEIGHT
    ):
        raise CoordinateConfigurationError(
            f"{coordinate_label}坐标 {target} 不在固定浏览器窗口内；"
            "请切换到 CNIPA Chrome 窗口后重新校准"
        )
    if target_y < _browser_page_minimum_y():
        raise CoordinateConfigurationError(
            f"{coordinate_label}坐标 {target} 落在浏览器工具栏；"
            "请把鼠标移到网页内容区域后重新校准"
        )


def _validate_saved_geometry(coordinate_config: dict) -> None:
    if coordinate_config.get("window_geometry") != _configured_window_geometry():
        raise CoordinateConfigurationError("浏览器窗口几何不匹配；请重新运行坐标校准")
    if coordinate_config.get("screen_geometry") != _current_screen_geometry():
        raise CoordinateConfigurationError("屏幕几何不匹配；请重新运行坐标校准")


def _matching_detail_config() -> dict:
    try:
        coordinate_config = _load_coordinate_config(CONFIG_FWXX_FILE)
        _validate_saved_geometry(coordinate_config)
    except CoordinateConfigurationError:
        return {}
    return coordinate_config


def _coordinate_metadata() -> dict:
    return {
        "window_geometry": _configured_window_geometry(),
        "screen_geometry": _current_screen_geometry(),
        "last_updated": datetime.now().isoformat(),
    }


def _show_calibration_prompt(message: str) -> None:
    if sys.platform == "darwin":
        from macos_coordinate_capture import show_calibration_prompt
        show_calibration_prompt(message)
        return
    if sys.platform != "win32":
        raise CoordinateConfigurationError("当前平台不支持点击式坐标校准")
    user32 = ctypes.windll.user32
    user32.MessageBoxW.argtypes = (
        wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT,
    )
    user32.MessageBoxW.restype = ctypes.c_int
    prompt_response = user32.MessageBoxW(
        None, message, "CNIPA 坐标校准",
        0x00000041 | 0x00010000 | 0x00040000,
    )
    if not prompt_response:
        raise OSError("无法显示坐标校准提示")
    if prompt_response != 1:
        raise CoordinateConfigurationError("已取消坐标校准，未保存新坐标")


def _prepare_calibration_window(page_description: str) -> int:
    _show_calibration_prompt(
        f"请先在 CNIPA 浏览器中{page_description}。\n\n"
        "现在可以操作浏览器完成查询或打开详情页，这些点击不会录入坐标。"
        "准备好后回到本提示，点击确定，再切回该 CNIPA 窗口。"
    )
    return position_foreground_window_for_coordinate_calibration()


def _validate_calibration_click(
    window_handle: int,
    pointer_target: tuple[int, int],
) -> None:
    """A desktop click must belong to the selected CNIPA window and its saved layout."""
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.argtypes = (wintypes.POINT,)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL

    clicked_window = user32.WindowFromPoint(wintypes.POINT(*pointer_target))
    clicked_root = user32.GetAncestor(clicked_window, _GA_ROOT)
    if clicked_root != window_handle or user32.GetForegroundWindow() != window_handle:
        raise CoordinateConfigurationError(
            "点击不属于本次选定的 CNIPA 窗口；未保存坐标，请重新校准"
        )
    title_buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(window_handle, title_buffer, len(title_buffer))
    if _CNIPA_WINDOW_TITLE_MARKER not in title_buffer.value:
        raise CoordinateConfigurationError("当前标签页不是 CNIPA 页面；请重新校准")
    window_rect = wintypes.RECT()
    if not user32.GetWindowRect(window_handle, ctypes.byref(window_rect)):
        raise OSError("无法核验点击时的 Chrome 窗口尺寸")
    actual_geometry = {
        "x": window_rect.left,
        "y": window_rect.top,
        "width": window_rect.right - window_rect.left,
        "height": window_rect.bottom - window_rect.top,
    }
    if actual_geometry != _configured_window_geometry():
        raise CoordinateConfigurationError("Chrome 窗口位置或尺寸已改变；请重新校准")


def _capture_next_left_click_coordinate(
    coordinate_label: str,
    window_handle: int,
) -> tuple[int, int]:
    """Capture a deliberate target click in the prepared CNIPA window."""
    if sys.platform == "darwin":
        from macos_coordinate_capture import capture_target_click
        return capture_target_click(
            coordinate_label, window_handle, _configured_window_geometry(),
            _CNIPA_WINDOW_TITLE_MARKER,
        )
    _show_calibration_prompt(f"点击确定后，请在 60 秒内单击【{coordinate_label}】。")
    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short

    while user32.GetAsyncKeyState(_LEFT_BUTTON_VIRTUAL_KEY) & 0x8000:
        time.sleep(0.02)
    # Ignore a possible second click from double-clicking the dialog button.
    time.sleep(0.35)

    deadline = time.monotonic() + _TARGET_CLICK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if user32.GetAsyncKeyState(_LEFT_BUTTON_VIRTUAL_KEY) & 0x8000:
            pointer_x, pointer_y = pyautogui.position()
            pointer_target = (int(pointer_x), int(pointer_y))
            _validate_calibration_click(window_handle, pointer_target)
            while user32.GetAsyncKeyState(_LEFT_BUTTON_VIRTUAL_KEY) & 0x8000:
                time.sleep(0.02)
            return pointer_target
        time.sleep(0.02)
    raise CoordinateConfigurationError(
        f"等待点击{coordinate_label}超时；请重新运行坐标校准"
    )


def _wait_for_operator_selected_cnipa_chrome_window(user32) -> int:
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int

    deadline = time.monotonic() + _FOREGROUND_WINDOW_TIMEOUT_SECONDS
    last_window_class = ""
    last_window_title = ""
    while time.monotonic() < deadline:
        window_handle = user32.GetForegroundWindow()
        if window_handle:
            class_name_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(window_handle, class_name_buffer, len(class_name_buffer))
            window_title_buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(window_handle, window_title_buffer, len(window_title_buffer))
            last_window_class = class_name_buffer.value
            last_window_title = window_title_buffer.value
            if (
                last_window_class == _CHROME_TOP_LEVEL_WINDOW_CLASS
                and _CNIPA_WINDOW_TITLE_MARKER in last_window_title
            ):
                return window_handle
        time.sleep(_FOREGROUND_WINDOW_POLL_INTERVAL_SECONDS)

    raise CoordinateConfigurationError(
        "等待人工切换到 CNIPA Chrome 窗口超时；"
        f"请将标题含“{_CNIPA_WINDOW_TITLE_MARKER}”的 CNIPA 标签页置于前台后重试；"
        f"最近前台窗口：类名“{last_window_class or '未知'}”，"
        f"标题“{last_window_title or '未知'}”"
    )


def position_foreground_window_for_coordinate_calibration() -> int:
    """Position only the CNIPA Chrome window selected by the operator."""
    if sys.platform == "darwin":
        from macos_coordinate_capture import select_calibration_window
        return select_calibration_window(
            _configured_window_geometry(), _CNIPA_WINDOW_TITLE_MARKER,
        )
    if sys.platform != "win32":
        raise CoordinateConfigurationError("当前平台不支持人工前台窗口校准")

    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    )
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    user32.GetWindowRect.restype = wintypes.BOOL

    while True:
        window_handle = _wait_for_operator_selected_cnipa_chrome_window(user32)
        if not user32.SetWindowPos(
            window_handle, None,
            BROWSER_WINDOW_X, BROWSER_WINDOW_Y, BROWSER_WINDOW_WIDTH, BROWSER_WINDOW_HEIGHT,
            _SWP_NOZORDER | _SWP_NOACTIVATE,
        ):
            raise OSError("无法把人工选择的 Chrome 窗口调整到坐标校准尺寸")

        window_rect = wintypes.RECT()
        if not user32.GetWindowRect(window_handle, ctypes.byref(window_rect)):
            raise OSError("无法核验人工选择的 Chrome 窗口尺寸")
        actual_geometry = {
            "x": window_rect.left,
            "y": window_rect.top,
            "width": window_rect.right - window_rect.left,
            "height": window_rect.bottom - window_rect.top,
        }
        expected_geometry = _configured_window_geometry()
        if actual_geometry != expected_geometry:
            raise CoordinateConfigurationError(
                "Chrome 窗口未能调整到校准尺寸；"
                f"期望 {expected_geometry}，实际 {actual_geometry}"
            )
        if user32.GetForegroundWindow() == window_handle:
            return window_handle
        time.sleep(_FOREGROUND_WINDOW_POLL_INTERVAL_SECONDS)


class CoordinateService:
    """统一管理鼠标坐标的加载和记录"""

    @staticmethod
    def load_search_coordinates() -> tuple[int, int, int, int]:
        coordinate_config = _load_coordinate_config(CONFIG_FILE)
        coordinates = _require_recorded_coordinates(
            coordinate_config, SEARCH_COORDINATE_PAIRS, "搜索页",
        )
        input_target = coordinates[:2]
        button_target = coordinates[2:]
        if input_target == button_target:
            raise CoordinateConfigurationError("输入框和查询按钮坐标不能相同")
        screen_geometry = _current_screen_geometry()
        _validate_page_target("申请号输入框", input_target, screen_geometry)
        _validate_page_target("查询按钮", button_target, screen_geometry)
        _validate_saved_geometry(coordinate_config)
        return coordinates

    @staticmethod
    def load_detail_link_coordinates() -> tuple[int, int]:
        coordinate_config = _load_coordinate_config(CONFIG_FWXX_FILE)
        coordinates = _require_recorded_coordinates(
            coordinate_config, DETAIL_LINK_COORDINATE_PAIRS, "搜索结果详情链接",
        )
        _validate_page_target("搜索结果详情链接", coordinates, _current_screen_geometry())
        _validate_saved_geometry(coordinate_config)
        return coordinates

    @staticmethod
    def load_fwxx_menu_coordinates() -> tuple[int, int]:
        coordinate_config = _load_coordinate_config(CONFIG_FWXX_FILE)
        coordinates = _require_recorded_coordinates(
            coordinate_config, FWXX_COORDINATE_PAIRS[1:], "发文信息菜单",
        )
        _validate_page_target("发文信息菜单", coordinates, _current_screen_geometry())
        _validate_saved_geometry(coordinate_config)
        return coordinates

    @staticmethod
    def load_fee_menu_coordinates() -> tuple[int, int]:
        coordinate_config = _load_coordinate_config(CONFIG_FWXX_FILE)
        coordinates = _require_recorded_coordinates(
            coordinate_config, FEE_MENU_COORDINATE_PAIRS, "费用信息菜单",
        )
        _validate_page_target("费用信息菜单", coordinates, _current_screen_geometry())
        _validate_saved_geometry(coordinate_config)
        return coordinates

    @staticmethod
    def load_fwxx_coordinates() -> tuple[int, int, int, int]:
        link_x, link_y = CoordinateService.load_detail_link_coordinates()
        menu_x, menu_y = CoordinateService.load_fwxx_menu_coordinates()
        return link_x, link_y, menu_x, menu_y

    @staticmethod
    def record_search_coordinates_from_pointer() -> tuple[int, int, int, int]:
        print(
            "\n请在 60 秒内人工切换到显示 CNIPA 搜索表单的 Chrome 窗口，"
            "并保持 CNIPA 标签页在前台。识别后请按弹窗提示依次单击输入框和查询按钮"
        )
        window_handle = _prepare_calibration_window("打开显示申请号输入框和查询按钮的搜索页")
        input_x, input_y = _capture_next_left_click_coordinate("申请号输入框", window_handle)
        button_x, button_y = _capture_next_left_click_coordinate("查询按钮", window_handle)
        coordinate_config = {
            "input_x": input_x, "input_y": input_y,
            "button_x": button_x, "button_y": button_y,
        }
        coordinates = _require_recorded_coordinates(
            coordinate_config, SEARCH_COORDINATE_PAIRS, "搜索页",
        )
        input_target = coordinates[:2]
        button_target = coordinates[2:]
        screen_geometry = _current_screen_geometry()
        _validate_page_target("申请号输入框", input_target, screen_geometry)
        _validate_page_target("查询按钮", button_target, screen_geometry)
        if input_target == button_target:
            raise CoordinateConfigurationError("输入框和查询按钮坐标不能相同")
        coordinate_config.update(_coordinate_metadata())
        write_json_atomic(CONFIG_FILE, coordinate_config)
        return coordinates

    @staticmethod
    def record_detail_link_coordinate_from_pointer() -> tuple[int, int]:
        print(
            "\n请在 60 秒内人工切换到显示搜索结果的 CNIPA Chrome 窗口，"
            "并保持 CNIPA 标签页在前台。识别后请按弹窗提示单击专利标题"
        )
        window_handle = _prepare_calibration_window("查询任意案件，让搜索结果中的专利标题显示出来")
        link_x, link_y = _capture_next_left_click_coordinate("搜索结果专利标题", window_handle)
        new_coordinates = {"link_x": link_x, "link_y": link_y}
        coordinates = _require_recorded_coordinates(
            new_coordinates, DETAIL_LINK_COORDINATE_PAIRS, "搜索结果详情链接",
        )
        _validate_page_target("搜索结果详情链接", coordinates, _current_screen_geometry())
        coordinate_config = _matching_detail_config()
        coordinate_config.update({**new_coordinates, **_coordinate_metadata()})
        write_json_atomic(CONFIG_FWXX_FILE, coordinate_config)
        return coordinates

    @staticmethod
    def record_fwxx_menu_coordinate_from_pointer() -> tuple[int, int]:
        print(
            "\n请在 60 秒内人工切换到已打开案件详情页的 CNIPA Chrome 窗口，"
            "并保持 CNIPA 标签页在前台。识别后请按弹窗提示单击发文信息菜单"
        )
        window_handle = _prepare_calibration_window("打开任意案件详情页，让左侧发文信息菜单显示出来")
        menu_x, menu_y = _capture_next_left_click_coordinate("发文信息菜单", window_handle)
        new_coordinates = {"fwxx_menu_x": menu_x, "fwxx_menu_y": menu_y}
        coordinates = _require_recorded_coordinates(
            new_coordinates, FWXX_COORDINATE_PAIRS[1:], "发文信息菜单",
        )
        _validate_page_target("发文信息菜单", coordinates, _current_screen_geometry())
        coordinate_config = _matching_detail_config()
        coordinate_config.update({**new_coordinates, **_coordinate_metadata()})
        write_json_atomic(CONFIG_FWXX_FILE, coordinate_config)
        return coordinates

    @staticmethod
    def record_fee_menu_coordinate_from_pointer() -> tuple[int, int]:
        print(
            "\n请在 60 秒内人工切换到已打开案件详情页的 CNIPA Chrome 窗口，"
            "并保持 CNIPA 标签页在前台。识别后请按弹窗提示单击费用信息菜单"
        )
        window_handle = _prepare_calibration_window("打开任意案件详情页，让左侧费用信息菜单显示出来")
        menu_x, menu_y = _capture_next_left_click_coordinate("费用信息菜单", window_handle)
        new_coordinates = {"fee_menu_x": menu_x, "fee_menu_y": menu_y}
        coordinates = _require_recorded_coordinates(
            new_coordinates, FEE_MENU_COORDINATE_PAIRS, "费用信息菜单",
        )
        _validate_page_target("费用信息菜单", coordinates, _current_screen_geometry())
        coordinate_config = _matching_detail_config()
        coordinate_config.update({**new_coordinates, **_coordinate_metadata()})
        write_json_atomic(CONFIG_FWXX_FILE, coordinate_config)
        return coordinates

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
                recorded = recorded_coordinates(config, SEARCH_COORDINATE_PAIRS)
                if recorded:
                    print("\n✓ 从配置文件加载鼠标位置")
                    print(f"  输入框: ({recorded[0]}, {recorded[1]})")
                    print(f"  按钮: ({recorded[2]}, {recorded[3]})")
                    return recorded
                print("\n⚠️  搜索页坐标缺失、格式无效或仍是占位值，需要重新录制")
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
        validate_coordinate_config(config)
        try:
            write_json_atomic(CONFIG_FILE, config)
            print("\n✓ 位置已保存到配置文件")
        except Exception as e:
            print(f"\n⚠️  保存配置失败: {e}")

        return input_x, input_y, button_x, button_y

    @staticmethod
    def load_or_record_detail_link_coordinates():
        """加载或记录搜索结果中的详情链接坐标，不读取详情页菜单坐标。"""
        coordinate_config = {}
        if os.path.exists(CONFIG_FWXX_FILE):
            try:
                with open(CONFIG_FWXX_FILE, 'r', encoding='utf-8') as config_stream:
                    coordinate_config = json.load(config_stream)
                recorded = recorded_coordinates(coordinate_config, DETAIL_LINK_COORDINATE_PAIRS)
                if recorded:
                    print("\n✓ 从配置文件加载详情链接位置")
                    print(f"  详情链接: ({recorded[0]}, {recorded[1]})")
                    return recorded
                print("\n⚠️  详情链接坐标缺失、格式无效或仍是占位值，需要重新录制")
            except Exception as error:
                print(f"⚠️  配置文件读取失败: {error}")

        return CoordinateService._record_detail_link_coordinates(
            coordinate_config
        )

    @staticmethod
    def _record_detail_link_coordinates(coordinate_config: dict):
        """在搜索页记录详情链接坐标并保留已有详情页坐标。"""
        print("\n" + "=" * 60)
        print("📍 鼠标位置记录 - 详情链接")
        print("=" * 60)
        print("⚠️  紧急停止: 把鼠标甩到屏幕左上角")
        print("\n▶ 请把鼠标移到搜索结果中的 [申请号详情链接]")
        CoordinateService._countdown(15, "等待用户移动鼠标到详情链接")
        link_x, link_y = pyautogui.position()
        print(f"  ✓ 详情链接坐标: ({link_x}, {link_y})   ")

        new_coordinates = {
            'link_x': link_x,
            'link_y': link_y,
            'last_updated': datetime.now().isoformat(),
        }
        validate_coordinate_config(new_coordinates)
        updated_coordinates = (
            dict(coordinate_config)
            if isinstance(coordinate_config, dict)
            else {}
        )
        updated_coordinates.update(new_coordinates)
        try:
            write_json_atomic(CONFIG_FWXX_FILE, updated_coordinates)
            print("\n✓ 详情链接位置已保存")
        except Exception as error:
            print(f"\n⚠️  保存配置失败: {error}")

        return link_x, link_y

    @staticmethod
    def load_or_record_fwxx_coordinates():
        """
        加载或记录发文信息页坐标（发文链接和菜单位置）

        Returns:
            tuple: (link_x, link_y, fwxx_menu_x, fwxx_menu_y)
        """
        coordinate_config = {}
        if os.path.exists(CONFIG_FWXX_FILE):
            try:
                with open(CONFIG_FWXX_FILE, 'r', encoding='utf-8') as f:
                    coordinate_config = json.load(f)
                recorded = recorded_coordinates(coordinate_config, FWXX_COORDINATE_PAIRS)
                if recorded:
                    print("\n✓ 从配置文件加载发文信息页鼠标位置")
                    print(f"  发文链接: ({recorded[0]}, {recorded[1]})")
                    print(f"  菜单: ({recorded[2]}, {recorded[3]})")
                    return recorded
                print("\n⚠️  发文信息页坐标缺失、格式无效或仍是占位值，需要重新录制")
            except Exception as e:
                print(f"⚠️  配置文件读取失败: {e}")

        return CoordinateService._record_fwxx_coordinates(coordinate_config)

    @staticmethod
    def _record_fwxx_coordinates(coordinate_config: dict | None = None):
        """手动记录发文信息页坐标，并保留已有费用菜单坐标。"""
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
        new_coordinates = {
            'link_x': link_x,
            'link_y': link_y,
            'fwxx_menu_x': fwxx_menu_x,
            'fwxx_menu_y': fwxx_menu_y,
            'last_updated': datetime.now().isoformat(),
        }
        validate_coordinate_config(new_coordinates)
        updated_coordinates = (
            dict(coordinate_config)
            if isinstance(coordinate_config, dict)
            else {}
        )
        updated_coordinates.update(new_coordinates)
        try:
            write_json_atomic(CONFIG_FWXX_FILE, updated_coordinates)
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
                recorded = recorded_coordinates(config, FEE_MENU_COORDINATE_PAIRS)
                if recorded:
                    print("\n✓ 从配置文件加载费用信息菜单位置")
                    print(f"  菜单: ({recorded[0]}, {recorded[1]})")
                    return recorded
                print("\n⚠️  费用信息菜单坐标缺失、格式无效或仍是占位值，需要重新录制")
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

        new_coordinates = {
            'fee_menu_x': fee_menu_x,
            'fee_menu_y': fee_menu_y,
            'last_updated': datetime.now().isoformat(),
        }
        validate_coordinate_config(new_coordinates)
        updated_config = dict(config) if isinstance(config, dict) else {}
        updated_config.update(new_coordinates)
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
