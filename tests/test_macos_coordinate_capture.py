"""Verify macOS window/click ownership without moving the real desktop."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

if sys.platform == "darwin":
    import macos_coordinate_capture as mac_capture
else:
    with patch.dict(sys.modules, {"AppKit": MagicMock(), "Quartz": MagicMock()}):
        import macos_coordinate_capture as mac_capture

import coordinate_service


WINDOW_GEOMETRY = {"x": 0, "y": 50, "width": 1440, "height": 900}
TITLE_MARKER = "专利审查信息查询"


class TestMacCoordinateCapture(unittest.TestCase):
    def setUp(self):
        self.appkit = MagicMock()
        self.appkit.NSAlertFirstButtonReturn = 1000
        self.appkit.NSScreen.screens.return_value = [
            SimpleNamespace(frame=lambda: SimpleNamespace(size=SimpleNamespace(height=1080)))
        ]
        self.appkit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.return_value = 123
        self.application = self.appkit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value
        self.application.bundleIdentifier.return_value = "com.google.Chrome"
        self.application.processIdentifier.return_value = 77
        self.quartz = MagicMock()
        for constant in (
            "kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowName",
            "kCGWindowBounds", "kCGWindowNumber", "kCGWindowAlpha",
        ):
            setattr(self.quartz, constant, constant)
        self.window = {
            "kCGWindowOwnerPID": 77,
            "kCGWindowLayer": 0,
            "kCGWindowName": TITLE_MARKER,
            "kCGWindowNumber": 123,
            "kCGWindowBounds": {"X": 0, "Y": 50, "Width": 1440, "Height": 900},
        }
        self.quartz.CGWindowListCopyWindowInfo.return_value = [self.window]
        self.quartz.CGPreflightScreenCaptureAccess.return_value = True
        self.quartz.CGPreflightListenEventAccess.return_value = True
        self.quartz.CGEventSourceKeyState.return_value = False
        self.quartz.CGEventGetLocation.return_value = SimpleNamespace(x=200.5, y=300.5)
        self.quartz.CGEventSourceButtonState.side_effect = [False, True, False]
        native_patch = patch.multiple(mac_capture, AppKit=self.appkit, Quartz=self.quartz)
        native_patch.start()
        self.addCleanup(native_patch.stop)
        prompt_patch = patch.object(mac_capture, "show_calibration_prompt")
        self.prompt = prompt_patch.start()
        self.addCleanup(prompt_patch.stop)
        self.clock = 0

        def next_time():
            self.clock += 0.1
            return self.clock

        clock_patch = patch.object(mac_capture.time, "monotonic", side_effect=next_time)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        sleep_patch = patch.object(mac_capture.time, "sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)

    def capture_click(self):
        return mac_capture.capture_target_click("申请号输入框", 123, WINDOW_GEOMETRY, TITLE_MARKER)

    def test_selects_foreground_cnipa_window_with_fixed_geometry(self):
        self.assertEqual(mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER), 123)

    def test_screen_permission_failure_is_actionable(self):
        self.quartz.CGPreflightScreenCaptureAccess.return_value = False
        with self.assertRaisesRegex(RuntimeError, "屏幕录制"):
            mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER)
        self.quartz.CGWindowListCopyWindowInfo.assert_not_called()

    def test_input_monitoring_permission_failure_does_not_wait_for_clicks(self):
        self.quartz.CGPreflightListenEventAccess.return_value = False
        with self.assertRaisesRegex(RuntimeError, "输入监控"):
            mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER)
        self.quartz.CGWindowListCopyWindowInfo.assert_not_called()

    def test_does_not_select_cnipa_window_behind_another_chrome_window(self):
        foreground = {**self.window, "kCGWindowNumber": 124, "kCGWindowName": "Other page"}
        self.quartz.CGWindowListCopyWindowInfo.return_value = [foreground, self.window]
        with patch.object(mac_capture, "_SELECTION_TIMEOUT_SECONDS", 0.3):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER)

    def test_does_not_select_chrome_behind_another_app(self):
        self.application.bundleIdentifier.return_value = "com.apple.Terminal"
        with patch.object(mac_capture, "_SELECTION_TIMEOUT_SECONDS", 0.3):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER)

    def test_geometry_mismatch_rejects_selection(self):
        self.window["kCGWindowBounds"]["Y"] = 30
        with self.assertRaisesRegex(RuntimeError, "位置或尺寸"):
            mac_capture.select_calibration_window(WINDOW_GEOMETRY, TITLE_MARKER)

    def test_records_desktop_points_after_mouse_release(self):
        self.assertEqual(self.capture_click(), (200, 300))
        self.assertEqual(self.quartz.CGEventSourceButtonState.call_count, 3)

    def test_activation_click_and_held_dialog_click_are_not_recorded(self):
        self.quartz.CGEventSourceButtonState.side_effect = [True, True, False, True, False]
        self.assertEqual(self.capture_click(), (200, 300))
        self.assertEqual(self.quartz.CGEventSourceButtonState.call_count, 5)

    def test_click_after_switching_windows_is_rejected(self):
        another_window = {**self.window, "kCGWindowNumber": 124}
        self.quartz.CGWindowListCopyWindowInfo.side_effect = [[self.window], [another_window]]
        with self.assertRaisesRegex(RuntimeError, "不属于"):
            self.capture_click()

    def test_returning_after_keyboard_app_switch_requires_another_target_click(self):
        self.quartz.CGWindowListCopyWindowInfo.side_effect = [
            [self.window], [], [self.window], [self.window], [self.window],
        ]
        self.quartz.CGEventSourceButtonState.side_effect = [False, False, True, False, True, False]
        self.assertEqual(self.capture_click(), (200, 300))
        self.assertEqual(self.quartz.CGEventSourceButtonState.call_count, 6)

    def test_click_after_switching_tabs_is_rejected(self):
        another_tab = {**self.window, "kCGWindowName": "Other page"}
        self.quartz.CGWindowListCopyWindowInfo.side_effect = [[self.window], [another_tab]]
        with self.assertRaisesRegex(RuntimeError, "不是 CNIPA"):
            self.capture_click()

    def test_click_after_resizing_window_is_rejected(self):
        resized = {**self.window, "kCGWindowBounds": {"X": 0, "Y": 50, "Width": 1400, "Height": 900}}
        self.quartz.CGWindowListCopyWindowInfo.side_effect = [[self.window], [resized]]
        with self.assertRaisesRegex(RuntimeError, "位置或尺寸"):
            self.capture_click()

    def test_floating_window_covering_the_target_is_rejected(self):
        overlay = {**self.window, "kCGWindowNumber": 456, "kCGWindowOwnerPID": 88}
        self.quartz.CGWindowListCopyWindowInfo.return_value = [overlay, self.window]
        self.appkit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.return_value = 456
        with self.assertRaisesRegex(RuntimeError, "遮挡"):
            self.capture_click()

    def test_click_outside_selected_window_is_rejected(self):
        self.quartz.CGEventGetLocation.return_value = SimpleNamespace(x=1500, y=300)
        self.appkit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "遮挡"):
            self.capture_click()

    def test_click_through_overlay_does_not_obscure_chrome(self):
        overlay = {
            **self.window, "kCGWindowNumber": 456, "kCGWindowOwnerPID": 88,
            "kCGWindowOwnerName": "Dock", "kCGWindowLayer": 20, "kCGWindowAlpha": 1,
            "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1920, "Height": 1080},
        }
        self.quartz.CGWindowListCopyWindowInfo.return_value = [overlay, self.window]
        self.assertEqual(self.capture_click(), (200, 300))

    def test_hit_test_uses_the_same_pointer_snapshot_and_logical_retina_height(self):
        self.appkit.NSScreen.screens.return_value = [
            SimpleNamespace(frame=lambda: SimpleNamespace(size=SimpleNamespace(height=900)))
        ]
        self.quartz.CGDisplayPixelsHigh.return_value = 1800
        self.assertEqual(self.capture_click(), (200, 300))
        self.appkit.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_.assert_called_once_with(
            (200.5, 599.5), 0,
        )
        self.quartz.CGEventGetLocation.assert_called_once()
        self.quartz.CGDisplayPixelsHigh.assert_not_called()

    def test_escape_cancels_without_sampling_a_target(self):
        self.quartz.CGEventSourceKeyState.return_value = True
        with self.assertRaisesRegex(RuntimeError, "取消"):
            self.capture_click()
        self.quartz.CGEventGetLocation.assert_not_called()

    def test_missing_click_times_out(self):
        self.quartz.CGEventSourceButtonState.side_effect = None
        self.quartz.CGEventSourceButtonState.return_value = False
        with patch.object(mac_capture, "_CLICK_TIMEOUT_SECONDS", 0.3):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                self.capture_click()

    def test_escape_while_mouse_is_held_cancels_the_captured_target(self):
        self.quartz.CGEventSourceKeyState.side_effect = [False, False, True]
        self.quartz.CGEventSourceButtonState.side_effect = [False, True, True, False]
        with self.assertRaisesRegex(RuntimeError, "取消"):
            self.capture_click()

    def test_mouse_held_after_target_press_cannot_block_forever(self):
        self.quartz.CGEventSourceButtonState.side_effect = [False, True, True, True, True]
        with patch.object(mac_capture, "_CLICK_TIMEOUT_SECONDS", 0.5):
            with self.assertRaisesRegex(RuntimeError, "超时"):
                self.capture_click()


class TestMacCalibrationDispatch(unittest.TestCase):
    def test_existing_coordinate_operations_use_native_mac_capture(self):
        with (
            patch.object(coordinate_service.sys, "platform", "darwin"),
            patch.object(mac_capture, "select_calibration_window", return_value=123) as select_window,
            patch.object(mac_capture, "capture_target_click", return_value=(200, 300)) as capture_click,
            patch.object(mac_capture, "show_calibration_prompt") as show_prompt,
        ):
            coordinate_service._show_calibration_prompt("准备搜索页")
            selected = coordinate_service.position_foreground_window_for_coordinate_calibration()
            target = coordinate_service._capture_next_left_click_coordinate("查询按钮", selected)
        self.assertEqual(target, (200, 300))
        show_prompt.assert_called_once_with("准备搜索页")
        select_window.assert_called_once_with(coordinate_service._configured_window_geometry(), TITLE_MARKER)
        capture_click.assert_called_once_with("查询按钮", 123, coordinate_service._configured_window_geometry(), TITLE_MARKER)

    def test_native_dialog_cancel_stops_calibration(self):
        appkit = MagicMock()
        appkit.NSAlertFirstButtonReturn = 1000
        appkit.NSAlert.alloc.return_value.init.return_value.runModal.return_value = 1001
        with patch.object(mac_capture, "AppKit", appkit):
            with self.assertRaisesRegex(RuntimeError, "取消"):
                mac_capture.show_calibration_prompt("准备搜索页")
        appkit.NSAlert.alloc.return_value.init.return_value.window.return_value.orderOut_.assert_called_once_with(None)

    def test_native_dialog_is_removed_before_click_capture_starts(self):
        appkit = MagicMock()
        appkit.NSAlertFirstButtonReturn = 1000
        prompt = appkit.NSAlert.alloc.return_value.init.return_value
        prompt.runModal.return_value = 1000
        with patch.object(mac_capture, "AppKit", appkit):
            mac_capture.show_calibration_prompt("准备搜索页")
        prompt.window.return_value.orderOut_.assert_called_once_with(None)
