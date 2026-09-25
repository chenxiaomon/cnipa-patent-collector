"""Native macOS prompts and verified desktop clicks for coordinate calibration.

Window bounds and pointer locations use Quartz desktop points, including on
Retina displays. The browser launcher owns window sizing; calibration only
accepts that fixed layout and never silently blesses a moved window.
"""

from __future__ import annotations

import time

import AppKit
import Quartz


_SELECTION_TIMEOUT_SECONDS = 60.0
_CLICK_TIMEOUT_SECONDS = 60.0
_POLL_INTERVAL_SECONDS = 0.02
_CHROME_BUNDLE_IDS = {"com.google.Chrome", "com.google.Chrome.beta", "org.chromium.Chromium"}


def show_calibration_prompt(message: str) -> None:
    application = AppKit.NSApplication.sharedApplication()
    application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    application.activateIgnoringOtherApps_(True)
    prompt = AppKit.NSAlert.alloc().init()
    prompt.setMessageText_("CNIPA 坐标校准")
    prompt.setInformativeText_(message)
    prompt.addButtonWithTitle_("确定")
    prompt.addButtonWithTitle_("取消")
    try:
        prompt_response = prompt.runModal()
    finally:
        prompt.window().orderOut_(None)
    if prompt_response != AppKit.NSAlertFirstButtonReturn:
        raise RuntimeError("已取消坐标校准，未保存新坐标")


def _visible_windows() -> list[dict]:
    return Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )


def _foreground_chrome_window(windows: list[dict]) -> dict | None:
    # CLI polling does not run Cocoa's event loop; without pumping it,
    # NSWorkspace can keep reporting the app that was active before the prompt.
    AppKit.NSRunLoop.currentRunLoop().runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.001))
    application = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if application is None or application.bundleIdentifier() not in _CHROME_BUNDLE_IDS:
        return None
    # Only the frontmost window of the selected app counts, even if another
    # Chrome window behind it also displays a CNIPA page.
    return next((window for window in windows if (
        window[Quartz.kCGWindowOwnerPID] == application.processIdentifier()
        and window[Quartz.kCGWindowLayer] == 0
    )), None)


def _require_window_geometry(window: dict, expected_geometry: dict[str, int]) -> None:
    bounds = window[Quartz.kCGWindowBounds]
    actual_geometry = {
        "x": bounds["X"], "y": bounds["Y"],
        "width": bounds["Width"], "height": bounds["Height"],
    }
    if actual_geometry != expected_geometry:
        raise RuntimeError(
            "Chrome 窗口位置或尺寸与校准设置不匹配；"
            "请使用本次校准打开的浏览器，勿移动窗口；"
            f"期望 {expected_geometry}，实际 {actual_geometry}"
        )


def select_calibration_window(expected_geometry: dict[str, int], title_marker: str) -> int:
    missing_permissions = []
    if not Quartz.CGPreflightScreenCaptureAccess():
        missing_permissions.append("屏幕录制")
    if not Quartz.CGPreflightListenEventAccess():
        missing_permissions.append("输入监控")
    if missing_permissions:
        raise RuntimeError(
            f"macOS 坐标校准缺少{'、'.join(missing_permissions)}权限；"
            "请在系统设置 → 隐私与安全性中允许启动控制台的应用（如终端），"
            "然后重新启动该应用及控制台并校准；自动采集还需辅助功能权限"
        )
    deadline = time.monotonic() + _SELECTION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        window = _foreground_chrome_window(_visible_windows())
        if window is not None and title_marker in window.get(Quartz.kCGWindowName, ""):
            _require_window_geometry(window, expected_geometry)
            return int(window[Quartz.kCGWindowNumber])
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(
        f"等待切换到 CNIPA Chrome 窗口超时；请将标题含“{title_marker}”的标签页置于前台后重试"
    )


def capture_target_click(
    coordinate_label: str,
    window_id: int,
    expected_geometry: dict[str, int],
    title_marker: str,
) -> tuple[int, int]:
    show_calibration_prompt(
        f"点击确定后，请切回本次选定的 CNIPA Chrome 窗口，"
        f"再在 60 秒内单击【{coordinate_label}】。\n"
        "切换窗口的点击不会录入；按 Esc 取消。"
    )
    deadline = time.monotonic() + _CLICK_TIMEOUT_SECONDS
    armed = False
    while time.monotonic() < deadline:
        if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 53):
            raise RuntimeError("已取消坐标校准，未保存新坐标")
        windows = _visible_windows()
        window = _foreground_chrome_window(windows)
        left_pressed = Quartz.CGEventSourceButtonState(
            Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGMouseButtonLeft,
        )
        if not left_pressed and (window is None or window[Quartz.kCGWindowNumber] != window_id):
            armed = False
        if not armed:
            if window is not None and window[Quartz.kCGWindowNumber] == window_id and not left_pressed:
                armed = True
        elif left_pressed:
            if window is None or window[Quartz.kCGWindowNumber] != window_id:
                raise RuntimeError("点击不属于本次选定的 CNIPA 窗口；未保存坐标，请重新校准")
            if title_marker not in window.get(Quartz.kCGWindowName, ""):
                raise RuntimeError("当前标签页不是 CNIPA 页面；请重新校准")
            _require_window_geometry(window, expected_geometry)
            pointer = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
            # AppKit hit-tests actual mouse recipients, including windows that
            # ignore mouse events; bounding rectangles alone cannot do that.
            # Quartz has a top-left origin, AppKit a bottom-left origin. Use the
            # primary screen's logical height, not its Retina pixel resolution.
            primary_screen_height = AppKit.NSScreen.screens()[0].frame().size.height
            clicked_window_id = AppKit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(
                (pointer.x, primary_screen_height - pointer.y), 0,
            )
            if clicked_window_id != window_id:
                raise RuntimeError("点击被其他窗口遮挡；未保存坐标，请重新校准")
            while time.monotonic() < deadline:
                if Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, 53):
                    raise RuntimeError("已取消坐标校准，未保存新坐标")
                if not Quartz.CGEventSourceButtonState(
                    Quartz.kCGEventSourceStateCombinedSessionState, Quartz.kCGMouseButtonLeft,
                ):
                    return int(pointer.x), int(pointer.y)
                time.sleep(_POLL_INTERVAL_SECONDS)
            break
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"等待点击{coordinate_label}超时；请重新运行坐标校准")
