import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import socket
import asyncio
import secrets
import threading
import json
import ctypes
import tempfile
import tkinter as tk
from PIL import Image, ImageTk
import qrcode
import pyautogui
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0

user32 = ctypes.windll.user32
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800

VK_BROWSER_BACK = 0xA6
VK_BROWSER_FORWARD = 0xA7
KEYEVENTF_KEYUP = 0x0002

def press_vk(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

def get_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)

SETTINGS_PATH = os.path.join(get_base_dir(), "settings.json")
DEFAULT_SETTINGS = {
    "sensitivity": 1.3,
    "scroll_speed": 1.0,
    "invert_scroll": False
}

def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

settings = load_settings()
TOKEN = secrets.token_urlsafe(12)
PORT = 8765
CONNECTION_GRACE_SECONDS = 24 * 60 * 60
server_loop = None
active_websockets = set()
disconnect_task = None
tray_report = None

def report_tray(connected=None, title=None, message=None, is_error=False):
    if tray_report is not None:
        tray_report(connected, title, message, is_error)

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

app = FastAPI()

@app.get("/")
async def index():
    html_path = get_resource_path("index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(
        content=content,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )

rem_x = 0.0
rem_y = 0.0

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query("")):
    global rem_x, rem_y, settings, server_loop, disconnect_task
    if token != TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    server_loop = asyncio.get_running_loop()
    if disconnect_task is not None:
        disconnect_task.cancel()
        disconnect_task = None
    first_client = not active_websockets
    previous_clients = tuple(active_websockets)
    active_websockets.add(websocket)
    if first_client:
        report_tray(True, "Phone-Mouse", "接続完了")
    # Only the newest browser tab should control the PC. Keep the old page
    # open, but close its socket so it cannot send duplicate input.
    for previous in previous_clients:
        try:
            await previous.close(code=4001, reason="新しい接続に切り替わりました")
        except Exception:
            pass
    close_code = None
    try:
        await websocket.send_text(json.dumps({"type": "init_settings", "settings": settings}))
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "move":
                sens = float(settings.get("sensitivity", 1.3))
                rem_x += float(msg.get("dx", 0)) * sens
                rem_y += float(msg.get("dy", 0)) * sens
                ix = int(rem_x)
                iy = int(rem_y)
                if ix != 0 or iy != 0:
                    user32.mouse_event(MOUSEEVENTF_MOVE, ix, iy, 0, 0)
                    rem_x -= ix
                    rem_y -= iy
            elif t == "click":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif t == "double_click":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif t == "right_click":
                user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            elif t == "mouse_down":
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            elif t == "mouse_up":
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif t == "scroll":
                spd = float(settings.get("scroll_speed", 1.0))
                inv = -1 if settings.get("invert_scroll", False) else 1
                val = int(float(msg.get("dy", 0)) * spd * 4 * inv)
                if val != 0:
                    user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, val, 0)
            elif t == "back":
                press_vk(VK_BROWSER_BACK)
            elif t == "forward":
                press_vk(VK_BROWSER_FORWARD)
            elif t == "key_press":
                key = msg.get("key", "")
                if key in ("enter", "backspace", "escape", "tab", "space"):
                    pyautogui.press(key)
            elif t == "type_text":
                text = msg.get("text", "")
                if text:
                    try:
                        import pyperclip
                        pyperclip.copy(text)
                        pyautogui.hotkey("ctrl", "v")
                    except Exception:
                        root = tk.Tk()
                        root.withdraw()
                        root.clipboard_clear()
                        root.clipboard_append(text)
                        root.update()
                        pyautogui.hotkey("ctrl", "v")
                        root.destroy()
            elif t == "preview_settings":
                if "sensitivity" in msg:
                    settings["sensitivity"] = float(msg["sensitivity"])
                if "scroll_speed" in msg:
                    settings["scroll_speed"] = float(msg["scroll_speed"])
                if "invert_scroll" in msg:
                    settings["invert_scroll"] = bool(msg["invert_scroll"])
            elif t == "save_settings":
                save_settings(settings)
    except WebSocketDisconnect as exc:
        close_code = exc.code
    except Exception:
        close_code = None
        report_tray(None, "Phone-Mouse", "通信エラーが発生しました", True)
    finally:
        active_websockets.discard(websocket)
        if not active_websockets:
            if close_code in (4002, 4004, 1000, 1001):
                if disconnect_task is not None:
                    disconnect_task.cancel()
                    disconnect_task = None
                message = (
                    "QRコードが更新されました"
                    if close_code == 4002
                    else "スマホのタブが閉じられました"
                )
                report_tray(False, "Phone-Mouse", message)
            else:
                if disconnect_task is not None:
                    disconnect_task.cancel()
                disconnect_task = asyncio.create_task(report_disconnect_after_grace())

async def report_disconnect_after_grace():
    global disconnect_task
    try:
        await asyncio.sleep(CONNECTION_GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    if not active_websockets:
        report_tray(False, "Phone-Mouse", "24時間接続がなかったため接続解除")
    disconnect_task = None

async def disconnect_active_clients():
    for websocket in tuple(active_websockets):
        try:
            await websocket.close(code=4002, reason="QRコードが更新されました")
        except Exception:
            pass

def start_server():
    try:
        uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    except BaseException:
        report_tray(False, "Phone-Mouse", "エラー: サーバーを起動できませんでした", True)

def add_tray_icon(root):
    """Add a Windows notification-area icon with Show and Exit commands."""
    from ctypes import wintypes

    WM_APP = 0x8000
    WM_TRAY = WM_APP + 1
    WM_COMMAND = 0x0111
    WM_CONTEXTMENU = 0x007B
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    WM_NULL = 0x0000
    NIM_ADD, NIM_DELETE, NIM_SETVERSION = 0, 2, 4
    NIM_MODIFY = 1
    NIF_MESSAGE, NIF_ICON, NIF_TIP = 1, 2, 4
    NIF_INFO = 0x10
    NOTIFYICON_VERSION_4 = 4
    NIIF_INFO, NIIF_ERROR = 1, 3
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
    GWL_WNDPROC = -4
    MF_STRING = 0
    TPM_RIGHTBUTTON = 0x0002
    TPM_RETURNCMD = 0x0100
    ID_SHOW, ID_EXIT = 1001, 1002

    class NOTIFYICONDATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON),
        ]

    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    hwnd = wintypes.HWND(root.winfo_id())
    user32.LoadImageW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetWindowLongPtrW.restype = ctypes.c_void_p
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = ctypes.c_ssize_t
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
                                   wintypes.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      ctypes.c_void_p]
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL
    data = NOTIFYICONDATA()
    data.cbSize = ctypes.sizeof(data)
    data.hWnd = hwnd
    data.uID = 1
    data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    data.uCallbackMessage = WM_TRAY
    current_hicon = [None]
    connected_state = [False]

    def make_icon(connected):
        from PIL import ImageDraw
        icon_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon_image)
        draw.rounded_rectangle((11, 5, 53, 57), radius=18, fill="#e6edf3",
                               outline="#58a6ff", width=4)
        draw.line((32, 8, 32, 25), fill="#58a6ff", width=3)
        dot_color = "#3fb950" if connected else "#f85149"
        draw.ellipse((38, 39, 62, 63), fill=dot_color, outline="#0d1117", width=3)
        file_handle, icon_path = tempfile.mkstemp(suffix=".ico")
        os.close(file_handle)
        try:
            icon_image.save(icon_path, format="ICO", sizes=[(32, 32)])
            icon_handle = user32.LoadImageW(None, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        finally:
            try:
                os.remove(icon_path)
            except OSError:
                pass
        if not icon_handle:
            raise ctypes.WinError()
        return icon_handle

    def set_icon(connected):
        new_icon = make_icon(connected)
        old_icon = current_hicon[0]
        data.hIcon = new_icon
        data.szTip = "Phone-Mouse - 接続中" if connected else "Phone-Mouse - 未接続"
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        if old_icon is None:
            added = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data))
        else:
            added = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        if not added:
            user32.DestroyIcon(new_icon)
            raise ctypes.WinError()
        current_hicon[0] = new_icon
        if old_icon is not None:
            user32.DestroyIcon(old_icon)

    def notify(title, message, is_error=False):
        data.uFlags = NIF_INFO
        data.szInfoTitle = title[:63]
        data.szInfo = message[:255]
        data.dwInfoFlags = NIIF_ERROR if is_error else NIIF_INFO
        delivered = shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        if not delivered:
            raise ctypes.WinError()

    def apply_tray_report(connected, title, message, is_error):
        try:
            if connected is not None and connected_state[0] != connected:
                set_icon(connected)
                connected_state[0] = connected
                status_label = getattr(root, "_connection_status_label", None)
                if status_label is not None:
                    status_label.configure(
                        text="接続中" if connected else "未接続",
                        fg="#3fb950" if connected else "#f85149",
                    )
            if title and message:
                notify(title, message, is_error)
        except Exception:
            root.title("Phone-Mouse - エラー")

    def queue_tray_report(connected, title, message, is_error):
        root.after(0, lambda: apply_tray_report(connected, title, message, is_error))

    global tray_report
    tray_report = queue_tray_report

    old_proc = user32.GetWindowLongPtrW(hwnd, GWL_WNDPROC)
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    def show_window():
        root.deiconify()
        root.lift()
        root.focus_force()

    def tray_proc(window, message, wparam, lparam):
        if message == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (0, 1, WM_LBUTTONUP):  # NIN_SELECT / NIN_KEYSELECT / legacy click
                root.after(0, show_window)
                return 0
            if event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                menu = user32.CreatePopupMenu()
                user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "ウィンドウを表示")
                user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "終了")
                point = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(hwnd)
                command = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                                point.x, point.y, 0, hwnd, None)
                user32.DestroyMenu(menu)
                user32.PostMessageW(hwnd, WM_NULL, 0, 0)
                if command == ID_SHOW:
                    root.after(0, show_window)
                elif command == ID_EXIT:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
                    if current_hicon[0] is not None:
                        user32.DestroyIcon(current_hicon[0])
                    root.after(0, root.destroy)
                return 0
        if message == WM_COMMAND and (wparam & 0xFFFF) == ID_EXIT:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            if current_hicon[0] is not None:
                user32.DestroyIcon(current_hicon[0])
            root.after(0, root.destroy)
            return 0
        return user32.CallWindowProcW(old_proc, window, message, wparam, lparam)

    callback = WNDPROC(tray_proc)
    user32.SetWindowLongPtrW(hwnd, GWL_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
    set_icon(False)
    data.uTimeoutOrVersion = NOTIFYICON_VERSION_4
    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))
    root._tray_callback = callback
    root._tray_data = data
    root._tray_old_proc = old_proc
    root._tray_hwnd = hwnd

def set_dark_title_bar(root):
    """Match the native Windows title bar to the app's dark color scheme."""
    try:
        from ctypes import wintypes
        dwmapi = ctypes.windll.dwmapi
        user32 = ctypes.windll.user32
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        client_hwnd = wintypes.HWND(root.winfo_id())
        hwnd = user32.GetAncestor(client_hwnd, 2)  # GA_ROOT: DWM needs the top-level HWND.
        if not hwnd:
            hwnd = client_hwnd

        def set_color(attribute, color):
            value = wintypes.DWORD(color)
            return dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )

        set_dark = wintypes.DWORD(1)
        dark_result = dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(set_dark), ctypes.sizeof(set_dark)
        )  # DWMWA_USE_IMMERSIVE_DARK_MODE
        caption_result = set_color(35, 0x00100C09)  # DWMWA_CAPTION_COLOR: #090c10
        text_result = set_color(36, 0x00F3EDE6)  # DWMWA_TEXT_COLOR: #e6edf3
        root._titlebar_dwm_results = (dark_result, caption_result, text_result)
    except Exception as exc:
        root._titlebar_dwm_error = str(exc)

def main():
    ip = get_local_ip()
    url = f"http://{ip}:{PORT}/?token={TOKEN}"

    root = tk.Tk()
    root.title("Phone-Mouse")
    root.configure(bg="#0d1117")
    root.geometry("520x700")
    root.minsize(360, 500)
    root.resizable(True, True)

    def hide_to_tray():
        root.withdraw()

    root.protocol("WM_DELETE_WINDOW", hide_to_tray)

    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    base_qr_img = qr.make_image(fill_color="#0d1117", back_color="#ffffff").convert("RGB")

    # The main page stays short; detailed explanations live in the sliding guide.
    topbar = tk.Frame(root, bg="#161b22", height=52,
                      highlightbackground="#30363d", highlightthickness=1)
    topbar.pack(fill="x")
    topbar.pack_propagate(False)
    menu_btn = tk.Button(
        topbar, text="☰", font=("Segoe UI Symbol", 17),
        bg="#161b22", fg="#e6edf3", activebackground="#30363d",
        activeforeground="#ffffff", relief="flat", bd=0, cursor="hand2",
        command=lambda: open_drawer(),
    )
    menu_btn.pack(side="left", padx=(10, 8))
    app_title = tk.Label(topbar, text="Phone-Mouse", font=("Segoe UI", 12, "bold"),
                         fg="#e6edf3", bg="#161b22")
    app_title.pack(side="left")
    status_lbl = tk.Label(topbar, text="● 未接続", font=("Segoe UI", 9, "bold"),
                          fg="#f85149", bg="#161b22")
    status_lbl.pack(side="right", padx=14)
    root._connection_status_label = status_lbl

    home = tk.Frame(root, bg="#0d1117")
    home.pack(fill="both", expand=True, padx=18, pady=(8, 12))
    home_title = tk.Label(home, text="接続", font=("Segoe UI", 17, "bold"),
                          fg="#e6edf3", bg="#0d1117")
    home_title.pack(pady=(4, 2))
    sub_lbl = tk.Label(
        home,
        text="同じWi-Fiにつないで、スマホのカメラでQRコードを読み取ります。",
        font=("Segoe UI", 9), fg="#8b949e", bg="#0d1117", justify="center",
    )
    sub_lbl.pack(pady=(0, 5))
    qr_label = tk.Label(home, bg="#ffffff", bd=3)
    qr_label.pack(pady=5)
    url_lbl = tk.Label(home, text=url, font=("Consolas", 8), fg="#58a6ff",
                       bg="#0d1117", justify="center")
    url_lbl.pack(pady=(2, 4))
    update_qr_btn = tk.Button(
        home, text="QRコードを更新", command=lambda: update_qr(),
        bg="#21262d", fg="#e6edf3", activebackground="#30363d",
        activeforeground="#ffffff", relief="flat", padx=14, pady=5,
        cursor="hand2",
    )
    update_qr_btn.pack(pady=(0, 7))
    wifi_lbl = tk.Label(home, text="PCとスマホを同じWi-Fiに接続してください",
                        font=("Segoe UI", 9, "bold"), fg="#c9d1d9", bg="#0d1117")
    wifi_lbl.pack(pady=(0, 5))
    close_note = tk.Label(
        home,
        text="×で閉じても接続は続きます。完全に終了するときは、\n右下のPhone-Mouseアイコンから「終了」を選んでください。",
        font=("Segoe UI", 8), fg="#8b949e", bg="#161b22",
        justify="center", padx=10, pady=7,
        highlightbackground="#30363d", highlightthickness=1,
    )
    close_note.configure(wraplength=470)
    close_note.pack(fill="x", pady=(1, 0))

    guide_sections = {
        "接続方法": (
            "スマホを接続する\n\n"
            "1. PCとスマホを同じWi-Fiに接続します。\n\n"
            "2. スマホのカメラでホーム画面のQRコードを読み取ります。\n\n"
            "3. 表示されたリンクを開きます。スマホ画面に操作パッドが表示され、PC上部が緑色の「接続中」になれば接続完了です。\n\n"
            "QRコードを読み取れない場合は、スマホのカメラを少し離してコード全体を映してください。"
        ),
        "タッチパッド操作": (
            "タッチパッドの操作\n\n"
            "カーソル移動とクリック\n"
            "・1本指でパッド上を動かす：PCのカーソルを移動\n"
            "・1本指で短くタップ：左クリック\n"
            "・続けて2回タップ：ダブルクリック\n\n"
            "ドラッグ\n"
            "1回タップして指を離し、すぐにもう一度タップします。2回目は指を離さずに動かすと、ドラッグできます。指を離すと解除されます。\n\n"
            "2本指の操作\n"
            "・上下に動かす：スクロール\n"
            "・短くタップ：右クリック\n"
            "・左右にスワイプ：ブラウザーの戻る／進む\n\n"
            "画面下の左クリックボタンは、押している間ドラッグになります。右クリックボタンも使えます。"
        ),
        "文字入力・キー": (
            "文字入力とキー操作\n\n"
            "スマホ画面上部の入力欄をタップして文字を入力し、「送信」を押します。入力した文字は、PCで現在選択されている場所に送られます。日本語も入力できます。\n\n"
            "入力欄の下にあるキー\n"
            "・戻る／進む：ブラウザーのページ移動\n"
            "・Esc：操作のキャンセルなど\n"
            "・Tab：入力欄や項目の移動\n"
            "・Space：スペース入力\n"
            "・Backspace：1文字削除\n"
            "・Enter：決定や改行\n\n"
            "文字を送る前に、PC側で入力先が選択されていることを確認してください。"
        ),
        "接続・終了": (
            "接続状態とアプリの終了\n\n"
            "PC画面右上と通知領域のアイコンで接続状態を確認できます。緑色は接続中、赤色は未接続です。接続時と切断時には通知が表示されます。\n\n"
            "ウィンドウ右上の×を押すと、画面が隠れて通知領域に移動します。スマホとの接続は続くので、閉じても大丈夫です。\n\n"
            "画面を再表示する：通知領域のPhone-Mouseアイコンをクリック\n\n"
            "アプリを終了する：アイコンを右クリックして「終了」を選ぶ\n\n"
            "QRコードを更新すると、接続用コードが新しくなり、現在のスマホ接続は解除されます。新しいQRコードを読み直してください。"
        ),
        "困ったとき": (
            "接続できないとき\n\n"
            "PC画面右上が赤色の「未接続」のままの場合、次を確認してください。\n\n"
            "1. PCとスマホが同じWi-Fiにつながっているか確認します。\n\n"
            "2. スマホで古いページを開いている場合は閉じて、ホーム画面のQRコードを読み直します。\n\n"
            "3. QRコードを更新した場合は、必ず新しいコードを読み取ります。\n\n"
            "4. それでも接続できない場合は、アプリを終了してから起動し直します。"
        ),
    }

    guide_page = tk.Frame(root, bg="#0d1117")
    guide_page_title = tk.Label(guide_page, text="", font=("Segoe UI", 18, "bold"),
                                fg="#e6edf3", bg="#0d1117", anchor="w")
    guide_page_title.pack(fill="x", padx=22, pady=(18, 10))
    guide_body = tk.Frame(guide_page, bg="#161b22", highlightbackground="#30363d",
                          highlightthickness=1)
    guide_body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
    guide_text = tk.Text(
        guide_body, wrap="word", state="disabled", relief="flat", bd=0,
        bg="#161b22", fg="#c9d1d9", insertbackground="#c9d1d9",
        font=("Segoe UI", 10), padx=18, pady=16, spacing1=3, spacing3=6,
    )
    guide_scroll = tk.Scrollbar(guide_body, command=guide_text.yview)
    guide_text.configure(yscrollcommand=guide_scroll.set)
    guide_scroll.pack(side="right", fill="y")
    guide_text.pack(side="left", fill="both", expand=True)

    # The drawer is intentionally wide; selecting an item opens a full detail page.
    shade = tk.Frame(root, bg="#090c10", cursor="hand2")
    drawer = tk.Frame(root, bg="#161b22", highlightbackground="#30363d",
                      highlightthickness=1)
    drawer_header = tk.Frame(drawer, bg="#161b22")
    drawer_header.pack(fill="x", padx=16, pady=(16, 12))
    drawer_title = tk.Label(drawer_header, text="メニュー",
                            font=("Segoe UI", 14, "bold"), fg="#e6edf3", bg="#161b22")
    drawer_title.pack(side="left")
    drawer_close = tk.Button(
        drawer_header, text="×", font=("Segoe UI", 16),
        bg="#161b22", fg="#8b949e", activebackground="#30363d",
        activeforeground="#ffffff", relief="flat", bd=0, cursor="hand2",
        command=lambda: close_drawer(),
    )
    drawer_close.pack(side="right")
    nav_frame = tk.Frame(drawer, bg="#161b22")
    nav_frame.pack(fill="x", padx=12, pady=(0, 10))
    drawer_hint = tk.Label(
        drawer, text="項目を選ぶと、画面が詳しい説明に切り替わります。",
        font=("Segoe UI", 9), fg="#8b949e", bg="#161b22",
        justify="left", wraplength=350, padx=16, pady=12,
    )
    drawer_hint.pack(side="bottom", fill="x")
    drawer_width = [0]
    drawer_open = [False]
    drawer_animation = [None]
    current_page = ["ホーム"]
    nav_buttons = {}

    def animate_drawer(opening):
        if drawer_animation[0] is not None:
            root.after_cancel(drawer_animation[0])
            drawer_animation[0] = None
        drawer_open[0] = opening
        width = min(760, max(260, int(root.winfo_width() * 0.72)))
        drawer_width[0] = width
        drawer_hint.configure(wraplength=max(200, width - 30))
        start_x = drawer.winfo_x() if drawer.winfo_manager() else -width
        target_x = 0 if opening else -width
        distance = target_x - start_x
        if distance == 0:
            return
        steps = 12
        step = [0]

        def move_next():
            progress = min(1.0, (step[0] + 1) / steps)
            eased = 1 - (1 - progress) ** 3
            x = int(start_x + distance * eased)
            drawer.place(x=x, y=0, width=width, height=root.winfo_height())
            shade_left = max(0, x + width)
            if opening or progress < 1:
                shade.place(x=shade_left, y=0,
                            width=max(0, root.winfo_width() - shade_left),
                            height=root.winfo_height())
                shade.lift()
                drawer.lift()
            if progress < 1:
                step[0] += 1
                drawer_animation[0] = root.after(15, move_next)
            else:
                drawer_animation[0] = None
                if not opening:
                    drawer.place_forget()
                    shade.place_forget()

        move_next()

    def open_drawer():
        if not drawer_open[0]:
            animate_drawer(True)

    def close_drawer():
        if drawer_open[0]:
            animate_drawer(False)

    def show_page(name):
        current_page[0] = name
        if name == "ホーム":
            guide_page.pack_forget()
            home.pack(fill="both", expand=True, padx=18, pady=(8, 12))
        else:
            home.pack_forget()
            guide_page_title.configure(text=name)
            guide_text.configure(state="normal")
            guide_text.delete("1.0", "end")
            guide_text.insert("1.0", guide_sections[name])
            guide_text.configure(state="disabled")
            guide_text.yview_moveto(0)
            guide_page.pack(fill="both", expand=True)
        for item_name, button in nav_buttons.items():
            selected = item_name == name
            button.configure(
                bg="#238636" if selected else "#21262d",
                fg="#ffffff" if selected else "#c9d1d9",
            )
        close_drawer()

    menu_items = ["ホーム", *guide_sections.keys()]
    for item_name in menu_items:
        button = tk.Button(
            nav_frame, text=item_name, anchor="w", font=("Segoe UI", 11, "bold"),
            bg="#21262d", fg="#c9d1d9", activebackground="#30363d",
            activeforeground="#ffffff", relief="flat", padx=14, pady=11,
            cursor="hand2", command=lambda name=item_name: show_page(name),
        )
        button.pack(fill="x", pady=4)
        nav_buttons[item_name] = button
    nav_buttons["ホーム"].configure(bg="#238636", fg="#ffffff")

    shade.bind("<Button-1>", lambda event: close_drawer())

    last_size = [0, 0]
    resize_job = [None]

    def apply_layout(w, h):
        scale = max(0.7, min(w / 520.0, h / 700.0, 1.25))
        qr_size = max(120, min(260, w - 56, h - 365, int(250 * scale)))
        resized = base_qr_img.resize((qr_size, qr_size), Image.NEAREST)
        tk_img = ImageTk.PhotoImage(resized)
        qr_label.configure(image=tk_img)
        qr_label.image = tk_img
        home_title.configure(font=("Segoe UI", max(14, int(17 * scale)), "bold"))
        sub_lbl.configure(font=("Segoe UI", max(8, int(9 * scale))),
                          wraplength=max(220, w - 45))
        url_lbl.configure(font=("Consolas", max(7, int(8 * scale))),
                          wraplength=max(220, w - 48))
        wifi_lbl.configure(font=("Segoe UI", max(8, int(9 * scale))))
        close_note.configure(font=("Segoe UI", max(7, int(8 * scale))),
                             wraplength=max(230, w - 60))
        if drawer_open[0]:
            width = min(760, max(260, int(w * 0.72)))
            drawer_width[0] = width
            drawer_hint.configure(wraplength=max(200, width - 30))
            drawer.place_configure(width=width, height=h)
            shade_left = max(0, drawer.winfo_x() + width)
            shade.place_configure(x=shade_left, width=max(0, w - shade_left), height=h)

    def update_qr():
        global TOKEN
        nonlocal base_qr_img
        TOKEN = secrets.token_urlsafe(12)
        fresh_url = f"http://{get_local_ip()}:{PORT}/?token={TOKEN}"
        refreshed_qr = qrcode.QRCode(box_size=10, border=2)
        refreshed_qr.add_data(fresh_url)
        refreshed_qr.make(fit=True)
        base_qr_img = refreshed_qr.make_image(
            fill_color="#0d1117", back_color="#ffffff"
        ).convert("RGB")
        url_lbl.configure(text=fresh_url)
        apply_layout(root.winfo_width(), root.winfo_height())
        if server_loop is not None and server_loop.is_running():
            asyncio.run_coroutine_threadsafe(disconnect_active_clients(), server_loop)

    def on_configure(event):
        if event.widget is not root:
            return
        w, h = event.width, event.height
        if abs(w - last_size[0]) < 4 and abs(h - last_size[1]) < 4:
            return
        last_size[0], last_size[1] = w, h
        if resize_job[0] is not None:
            root.after_cancel(resize_job[0])
        resize_job[0] = root.after(30, lambda: apply_layout(w, h))

    root.bind("<Configure>", on_configure)
    root.update_idletasks()
    set_dark_title_bar(root)
    root.after(250, lambda: set_dark_title_bar(root))
    apply_layout(root.winfo_width(), root.winfo_height())

    add_tray_icon(root)
    threading.Thread(target=start_server, daemon=True).start()
    root.mainloop()

if __name__ == "__main__":
    main()
