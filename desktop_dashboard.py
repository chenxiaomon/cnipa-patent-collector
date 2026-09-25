#!/usr/bin/env python3
"""Open the existing local Dashboard in a native WebView window."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import subprocess
import sys
import threading
import time

from settings import BASE_DIR, DASHBOARD_SERVICE_LOG_FILE, DESKTOP_ICON_PNG_FILE, DESKTOP_ICON_ICO_FILE


class DashboardMaintenanceBusy(RuntimeError):
    """A code or database write must finish before the already requested application exit."""


def run_desktop_service(port: int) -> None:
    """Own the detached HTTP service and bounded logs, including import/startup failures."""
    service_output = RotatingFileHandler(
        DASHBOARD_SERVICE_LOG_FILE, maxBytes=4 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    service_output.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
    service_logger = logging.getLogger("cnipa.dashboard")
    service_logger.addHandler(service_output)
    service_logger.setLevel(logging.INFO)
    service_logger.propagate = False
    try:
        from web_dashboard import run_server
        run_server("127.0.0.1", port)
    except Exception:
        service_logger.exception("桌面后台启动或运行失败")
        raise
    finally:
        service_logger.removeHandler(service_output)
        service_output.close()


def read_dashboard_identity(port: int) -> dict | None:
    """Only an absent listener permits startup; another service must never be reused."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/api/desktop-status")
        response = connection.getresponse()
        identity_bytes = response.read(16385)
        if response.status != 200 or len(identity_bytes) > 16384:
            raise ValueError("没有兼容的桌面连接接口")
        identity = json.loads(identity_bytes)
        if (
            not isinstance(identity, dict)
            or identity.get("application") != "cnipa-patent-collector"
            or identity.get("desktop_protocol") != 2
            or not isinstance(identity.get("instance_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", identity["instance_id"])
            or identity.get("project_directory") != str(BASE_DIR.resolve())
            or type(identity.get("pid")) is not int
            or identity["pid"] <= 0
        ):
            raise ValueError("后台身份或项目目录不匹配")
        return identity
    except ConnectionRefusedError:
        return None
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise RuntimeError(
            f"无法连接端口 {port} 上的本项目控制台：{error}。"
            "若旧版控制台仍在运行，请先确认任务已结束，再停止旧版并重开桌面入口。"
        ) from error
    finally:
        connection.close()


@dataclass(frozen=True)
class DashboardConnection:
    """Bind desktop shutdown to one server lifetime, even when its port is reused."""

    port: int
    instance_id: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def shutdown(self) -> None:
        identity = read_dashboard_identity(self.port)
        if identity is None or identity["instance_id"] != self.instance_id:
            return
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=40)
        try:
            connection.request("GET", "/api/operator-token")
            token_response = connection.getresponse()
            token_payload = json.loads(token_response.read(16384))
            if (
                token_response.status != 200 or not isinstance(token_payload, dict)
                or not isinstance(token_payload.get("token"), str)
            ):
                raise RuntimeError("无法取得本机控制台的退出权限")
            connection.request(
                "POST", "/api/desktop-shutdown",
                body=json.dumps({"instance_id": self.instance_id}),
                headers={"Content-Type": "application/json", "X-CNIPA-Token": token_payload["token"]},
            )
            shutdown_response = connection.getresponse()
            shutdown_receipt = json.loads(shutdown_response.read(16384))
            if not isinstance(shutdown_receipt, dict):
                raise RuntimeError("后台返回了无效的退出确认")
            if shutdown_response.status == 409 and shutdown_receipt.get("reason") == "maintenance_running":
                raise DashboardMaintenanceBusy(shutdown_receipt.get("error") or "维护任务尚未结束")
            if shutdown_response.status != 200 or shutdown_receipt.get("stopped") is not True:
                identity = read_dashboard_identity(self.port)
                if identity is None or identity["instance_id"] != self.instance_id:
                    return
                raise RuntimeError(shutdown_receipt.get("error") or "后台尚未完成退出")
            if shutdown_receipt.get("instance_id") != self.instance_id:
                raise RuntimeError("退出确认不属于当前控制台")
        except ConnectionRefusedError:
            return
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise RuntimeError(f"后台退出未完成：{error}") from error
        finally:
            connection.close()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                identity = read_dashboard_identity(self.port)
            except RuntimeError:
                # Closing a listener can reset the last in-flight probe before refusing new connections.
                time.sleep(0.1)
                continue
            if identity is None or identity["instance_id"] != self.instance_id:
                return
            time.sleep(0.1)
        raise RuntimeError("任务已停止，但后台端口尚未关闭，请稍后再退出")


def connect_dashboard(port: int) -> DashboardConnection:
    """Reuse this checkout's server or start it, returning its shutdown ownership."""
    identity = read_dashboard_identity(port)
    if identity is not None:
        return DashboardConnection(port, identity["instance_id"])

    launch_options = {
        "cwd": str(BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        launch_options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        launch_options["start_new_session"] = True
    server_process = subprocess.Popen(
        [sys.executable, "-u", "-c",
         f"from desktop_dashboard import run_desktop_service; run_desktop_service({port})"],
        **launch_options,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            # A simultaneous second launcher may have won the bind; identity is authoritative.
            identity = read_dashboard_identity(port)
            if identity is not None:
                return DashboardConnection(port, identity["instance_id"])
            if server_process.poll() is not None:
                raise RuntimeError(f"后台启动失败，退出码 {server_process.returncode}")
            time.sleep(0.2)
        raise RuntimeError("等待后台启动超时")
    except Exception as error:
        # Only reap the child started here before a successful connection, never an existing server.
        if server_process.poll() is None:
            server_process.terminate()
            try:
                server_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server_process.kill()
                server_process.wait(timeout=3)
        raise RuntimeError(f"{error}；服务日志：{DASHBOARD_SERVICE_LOG_FILE}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="打开 CNIPA 独立桌面控制台，关闭窗口时停止任务和后台")
    parser.add_argument("--port", type=int, default=8765, help="本地后台端口，默认 8765")
    arguments = parser.parse_args()
    if not 1 <= arguments.port <= 65535:
        parser.error("端口必须在 1–65535 之间")
    if sys.platform not in {"darwin", "win32"}:
        print("桌面入口目前支持 macOS 和 Windows；其他系统请运行 web_dashboard.py。", file=sys.stderr)
        return 1
    try:
        import webview
    except ImportError:
        print("桌面组件尚未安装。请在项目目录运行：\n"
              "uv sync --frozen --extra desktop --python 3.11", file=sys.stderr)
        return 1

    try:
        dashboard_connection = connect_dashboard(arguments.port)
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.settings["ALLOW_FILE_URLS"] = False
            window = webview.create_window(
                "CNIPA 专利采集控制台", dashboard_connection.url,
                width=1280, height=860, min_size=(960, 640),
            )

            def close_desktop() -> bool:
                try:
                    dashboard_connection.shutdown()
                except RuntimeError as error:
                    print(f"尚未退出：{error}", file=sys.stderr)
                    # Cocoa dialogs dispatch to the UI thread; closing already runs there.
                    threading.Thread(
                        target=window.create_confirmation_dialog,
                        args=("暂时无法退出", str(error)), daemon=True,
                    ).start()
                    return False
                return True

            window.events.closing += close_desktop
            print("桌面控制台已连接。关闭窗口将停止本控制台的采集、代理和后台。")
            # No Python bridge or embedded HTTP server: the existing operator API owns all actions.
            webview.start(
                gui="edgechromium" if sys.platform == "win32" else "cocoa", private_mode=True,
                icon=str(DESKTOP_ICON_ICO_FILE if sys.platform == "win32" else DESKTOP_ICON_PNG_FILE),
            )
        finally:
            # Some Cocoa Cmd+Q paths stop the event loop without emitting closing.
            while True:
                try:
                    dashboard_connection.shutdown()
                    break
                except DashboardMaintenanceBusy:
                    # The GUI already exited; finish the pending quit once protected writes finish.
                    time.sleep(1)
        return 0
    except (Exception, KeyboardInterrupt) as error:
        print(f"桌面控制台未能正常打开或退出：{error}", file=sys.stderr)
        if sys.platform == "win32":
            print("Windows 桌面窗口需要 Microsoft Edge WebView2 Runtime。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
