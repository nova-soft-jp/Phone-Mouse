import sys
import os

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

import socket
import secrets
import threading
import json
import ctypes
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
    global rem_x, rem_y, settings
    if token != TOKEN:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "init_settings", "settings": settings}))
    try:
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
    except WebSocketDisconnect:
        pass
    except Exception:
        pass

def start_server():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")

def main():
    threading.Thread(target=start_server, daemon=True).start()
    ip = get_local_ip()
    url = f"http://{ip}:{PORT}/?token={TOKEN}"

    root = tk.Tk()
    root.title("Phone-Mouse")
    root.configure(bg="#0d1117")
    root.geometry("430x660")
    root.minsize(280, 380)
    root.resizable(True, True)

    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    base_qr_img = qr.make_image(fill_color="#0d1117", back_color="#ffffff").convert("RGB")

    container = tk.Frame(root, bg="#0d1117")
    container.pack(fill="both", expand=True, padx=14, pady=12)

    # 左／上エリア（タイトル・QRコード・URL）
    left_frame = tk.Frame(container, bg="#0d1117")
    title_lbl = tk.Label(left_frame, text="Phone-Mouse", font=("Segoe UI", 18, "bold"), fg="#e6edf3", bg="#0d1117")
    title_lbl.pack(pady=(2, 2))

    sub_lbl = tk.Label(left_frame, text="スマホのカメラでQRコードを読み取ってください", font=("Segoe UI", 10), fg="#8b949e", bg="#0d1117")
    sub_lbl.pack(pady=(0, 8))

    qr_label = tk.Label(left_frame, bg="#ffffff", bd=3)
    qr_label.pack(pady=4)

    url_lbl = tk.Label(left_frame, text=url, font=("Consolas", 9), fg="#58a6ff", bg="#0d1117")
    url_lbl.pack(pady=(6, 2))

    wifi_lbl = tk.Label(left_frame, text="※ PCとスマホを同じWi-Fiに接続してください", font=("Segoe UI", 9), fg="#8b949e", bg="#0d1117")
    wifi_lbl.pack(pady=(0, 4))

    # 右／下エリア（操作説明カード）
    right_frame = tk.Frame(container, bg="#161b22", highlightbackground="#30363d", highlightthickness=1)
    guide_title = tk.Label(
        right_frame,
        text="スマホでの操作方法",
        font=("Segoe UI", 11, "bold"),
        fg="#58a6ff",
        bg="#161b22",
        anchor="w"
    )
    guide_title.pack(fill="x", padx=12, pady=(10, 4))

    guide_text = (
        "・1本指でなぞる：カーソル移動\n"
        "・1本指タップ：左クリック（2回でダブルクリック）\n"
        "・2回タップして長押し：ドラッグ＆ドロップ\n"
        "・2本指で上下：スクロール\n"
        "・2本指タップ：右クリック\n"
        "・2本指で左右スワイプ：前のページ・次のページ\n"
        "・上部入力欄：PCへ日本語・英語のテキスト送信"
    )
    guide_lbl = tk.Label(
        right_frame,
        text=guide_text,
        font=("Segoe UI", 9),
        fg="#c9d1d9",
        bg="#161b22",
        justify="left",
        anchor="nw"
    )
    guide_lbl.pack(fill="both", expand=True, padx=12, pady=(0, 10))

    last_size = [0, 0]
    resize_job = [None]

    def apply_layout(w, h):
        is_wide = w > h * 1.15 and w >= 560

        left_frame.pack_forget()
        right_frame.pack_forget()

        if is_wide:
            # 横長・最大化時は左右2列に配置
            left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
            right_frame.pack(side="right", fill="both", expand=True, padx=(10, 0), pady=12)
            avail_w = w * 0.46
            avail_h = h * 0.85
            scale = min(avail_w / 320.0, avail_h / 400.0)
            wrap_url = int(w * 0.44)
            wrap_guide = int(w * 0.44)
        else:
            # 縦長・標準時は上下に配置
            left_frame.pack(side="top", fill="x", expand=False)
            right_frame.pack(side="bottom", fill="both", expand=True, pady=(6, 0))
            scale = min(w / 420.0, h / 650.0)
            wrap_url = max(220, w - 48)
            wrap_guide = max(220, w - 60)

        scale = max(0.62, min(scale, 2.4))

        qr_size = max(120, int(205 * scale))
        resized = base_qr_img.resize((qr_size, qr_size), Image.NEAREST)
        tk_img = ImageTk.PhotoImage(resized)
        qr_label.configure(image=tk_img)
        qr_label.image = tk_img

        title_lbl.configure(font=("Segoe UI", max(12, int(18 * scale)), "bold"))
        sub_lbl.configure(font=("Segoe UI", max(8, int(10 * scale))), wraplength=wrap_url)
        url_lbl.configure(font=("Consolas", max(7, int(9 * scale))), wraplength=wrap_url)
        wifi_lbl.configure(font=("Segoe UI", max(8, int(9 * scale))), wraplength=wrap_url)
        guide_title.configure(font=("Segoe UI", max(9, int(11 * scale)), "bold"))
        guide_lbl.configure(font=("Segoe UI", max(8, int(9.5 * scale))), wraplength=wrap_guide)

    def on_configure(event):
        if event.widget is not root:
            return
        w, h = event.width, event.height
        if abs(w - last_size[0]) < 4 and abs(h - last_size[1]) < 4:
            return
        last_size[0], last_size[1] = w, h
        if resize_job[0] is not None:
            root.after_cancel(resize_job[0])
        resize_job[0] = root.after(20, lambda: apply_layout(w, h))

    root.bind("<Configure>", on_configure)
    root.update_idletasks()
    apply_layout(root.winfo_width(), root.winfo_height())

    root.mainloop()

if __name__ == "__main__":
    main()
