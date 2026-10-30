"""Small native Windows tray; the main thread owns all windows and dialogs."""

from __future__ import annotations

import os
import queue
import sys
import webbrowser
from typing import TYPE_CHECKING, Any, cast

import win32api
import win32con
import win32gui as _win32gui

assert sys.platform == "win32"

# Generated pywin32 GUI stubs misdeclare mutable WNDCLASS fields and
# tuple-based Shell_NotifyIcon/AppendMenu signatures. Keep that boundary here.
gui = cast(Any, _win32gui)

if TYPE_CHECKING:
    from tlon_launcher.portable_paths import PortableLayout
    from tlon_launcher.portable_runtime import PortableRuntime

_UPDATE = win32con.WM_APP + 1
_TRAY = win32con.WM_APP + 2
_OPEN, _LOGS, _EXIT = 1001, 1002, 1003


def show_error(message: str, logs: str | None = None) -> None:
    text = message
    if logs:
        text += f"\n\n日志目录：{logs}\n是否打开日志目录？"
    answer = gui.MessageBox(
        0, text, "Tlon", win32con.MB_ICONERROR | (win32con.MB_YESNO if logs else win32con.MB_OK)
    )
    if logs and answer == win32con.IDYES:
        os.startfile(logs)


def open_browser(url: str) -> None:
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        gui.MessageBox(0, f"请在浏览器中打开：\n{url}", "Tlon", win32con.MB_OK)


class Tray:
    def __init__(self, layout: PortableLayout) -> None:
        self.layout = layout
        self.runtime: PortableRuntime | None = None
        self.messages: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self.url = ""
        self.status = "starting"
        self.icon = int(gui.LoadIcon(0, win32con.IDI_APPLICATION))
        window_class = gui.WNDCLASS()
        self.hinstance = win32api.GetModuleHandle(None)
        self.class_name = f"TlonPortable-{os.getpid()}"
        window_class.hInstance = self.hinstance
        window_class.lpszClassName = self.class_name
        window_class.lpfnWndProc = self._window_proc
        atom = gui.RegisterClass(window_class)
        self.hwnd = gui.CreateWindow(
            atom, "Tlon", 0, 0, 0, 0, 0, 0, 0, window_class.hInstance, None
        )
        gui.Shell_NotifyIcon(gui.NIM_ADD, self._icon_data("Tlon 正在启动…"))
        # A balloon is nonmodal; the menu remains available during initialization.
        gui.Shell_NotifyIcon(
            gui.NIM_MODIFY,
            (
                self.hwnd,
                0,
                gui.NIF_INFO,
                _TRAY,
                self.icon,
                "Tlon 正在启动…",
                "服务就绪后会自动打开浏览器。",
                10000,
                "Tlon 正在启动",
                gui.NIIF_INFO,
            ),
        )

    def _icon_data(self, title: str) -> tuple[int, int, int, int, int, str]:
        return (
            self.hwnd,
            0,
            gui.NIF_ICON | gui.NIF_MESSAGE | gui.NIF_TIP,
            _TRAY,
            self.icon,
            title,
        )

    def notify(self, event: str, value: str) -> None:
        self.messages.put((event, value))
        gui.PostMessage(self.hwnd, _UPDATE, 0, 0)

    def run(self, runtime: PortableRuntime) -> None:
        self.runtime = runtime
        runtime.start()
        try:
            gui.PumpMessages()
        finally:
            runtime.stop()
            runtime.join()
            if gui.IsWindow(self.hwnd):
                gui.DestroyWindow(self.hwnd)
            gui.UnregisterClass(self.class_name, self.hinstance)

    def _menu(self) -> None:
        menu = gui.CreatePopupMenu()
        try:
            gui.AppendMenu(
                menu,
                win32con.MF_STRING | (0 if self.url else win32con.MF_GRAYED),
                _OPEN,
                "打开 Tlon",
            )
            gui.AppendMenu(menu, win32con.MF_STRING, _LOGS, "打开日志目录")
            gui.AppendMenu(
                menu,
                win32con.MF_STRING | (win32con.MF_GRAYED if self.status == "stopping" else 0),
                _EXIT,
                "退出 Tlon",
            )
            gui.SetForegroundWindow(self.hwnd)
            x, y = gui.GetCursorPos()
            gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, x, y, 0, self.hwnd, None)
            gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        finally:
            gui.DestroyMenu(menu)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == _UPDATE:
            while not self.messages.empty():
                event, value = self.messages.get()
                if event == "ready":
                    self.url, self.status = value, "ready"
                    gui.Shell_NotifyIcon(gui.NIM_MODIFY, self._icon_data("Tlon 正在运行"))
                    open_browser(value)
                elif event == "stopping":
                    self.status = "stopping"
                    gui.Shell_NotifyIcon(gui.NIM_MODIFY, self._icon_data(value))
                elif event == "error":
                    show_error(value, str(self.layout.logs))
                elif event == "stopped":
                    gui.DestroyWindow(hwnd)
            return 0
        if message == _TRAY:
            if lparam == win32con.WM_LBUTTONDBLCLK and self.url:
                open_browser(self.url)
            elif lparam == win32con.WM_RBUTTONUP:
                self._menu()
            return 0
        if message == win32con.WM_COMMAND:
            command = win32api.LOWORD(wparam)
            if command == _OPEN and self.url:
                open_browser(self.url)
            elif command == _LOGS:
                os.startfile(self.layout.logs)
            elif command == _EXIT and self.runtime:
                self.runtime.stop()
            return 0
        if message == win32con.WM_CLOSE:
            if self.runtime:
                self.runtime.stop()
            return 0
        if message == win32con.WM_QUERYENDSESSION:
            if self.runtime:
                self.runtime.stop()
            return 1
        if message == win32con.WM_DESTROY:
            gui.Shell_NotifyIcon(gui.NIM_DELETE, (hwnd, 0))
            gui.PostQuitMessage(0)
            return 0
        return int(gui.DefWindowProc(hwnd, message, wparam, lparam))
