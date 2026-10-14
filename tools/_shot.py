# DeskPulse 界面截图工具（内部调试用）
"""用演示数据渲染界面并截图，用来核对排版。

用法：
  python tools/_shot.py [输出路径]                # 主卡片（默认 <仓库>/shot.png）
  python tools/_shot.py [输出路径] --settings     # 设置窗口
  python tools/_shot.py [输出路径] --active       # 激活态（锁定 + 置顶）

依赖：Pillow（仅本脚本需要，主程序仍然零第三方依赖）。
"""
import ctypes
import ctypes.wintypes as wt
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEMO = tempfile.mkdtemp(prefix="deskpulse_shot_")

spec = importlib.util.spec_from_file_location(
    "dw", os.path.join(REPO, "desktop_widget.py"))
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

# 演示数据落在临时目录，不碰用户真实配置
dw.CONFIG_PATH = os.path.join(DEMO, "config.json")
dw.CACHE_PATH = os.path.join(DEMO, "balance_cache.json")
dw.LOG_PATH = os.path.join(DEMO, "error.log")

PROFILES = [
    {"id": "demo_ds", "type": "deepseek", "name": "DeepSeek",
     "api_key": "sk-demo", "threshold": 20.0},
    {"id": "demo_volc", "type": "volc", "name": "火山方舟",
     "api_key": "", "ak": "AKLTdemo", "sk": "demoSK", "threshold": 10.0},
    {"id": "demo_or", "type": "openrouter", "name": "OpenRouter",
     "api_key": "sk-or-demo", "threshold": 5.0},
]
CFG = {"city": "北京", "profiles": PROFILES, "theme": "midnight",
       "refresh_minutes": 10, "opacity": 1.0, "always_on_top": False,
       "locked": False, "ui_scale": 1.0, "window_x": -1, "window_y": -1,
       "hotkey_settings": "ctrl+alt+s", "hotkey_quit": "ctrl+alt+q",
       "show_seconds": False, "autostart": False, "check_update": False,
       "notify_low_balance": False}


def seed():
    json.dump(CFG, open(dw.CONFIG_PATH, "w", encoding="utf-8"),
              ensure_ascii=False)
    now = time.time()
    hist = lambda base, step, n=22: [[now - (n - i) * 600,
                                      round(base + step * i, 3)]
                                     for i in range(n)]
    json.dump({"demo_ds": {"main": 128.40, "ts": now,
                           "hist": hist(163.0, -1.65)},
               "demo_volc": {"main": 42.75, "ts": now,
                             "hist": hist(30.0, 0.62)},
               "demo_or": {"main": 12.30, "ts": now,
                           "hist": hist(25.0, -0.65)}},
              open(dw.CACHE_PATH, "w", encoding="utf-8"))


def mock_network():
    dw.PLATFORMS["deepseek"]["fetch"] = lambda k: {
        "available": True, "currency": "CNY", "total": "128.40",
        "topped_up": "100.00", "granted": "28.40"}
    dw.PLATFORMS["openrouter"]["fetch"] = lambda k: {
        "available": True, "currency": "USD", "total": "12.30",
        "sub": "累计充值 $30.00 · 已用 $17.70"}
    dw.fetch_volc_balance = lambda ak, sk: {
        "available": "42.75", "cash": "30.00", "credit": "100.00",
        "freeze": "0.00", "arrears": "0.00"}
    dw.fetch_ark_usage = lambda ak, sk: {
        "input": 1240000, "output": 386000, "total": 1626000, "req": 182}
    dw.fetch_weather = lambda city: {
        "city": "北京", "admin1": "", "temp": 18.5, "feels": 17.2,
        "humidity": 42, "is_day": 1, "code": 1, "precip": 0, "wind": 12.6,
        "wind_dir": 315, "tmax": 22.0, "tmin": 11.0, "pop": 10}


def pump(root, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update_idletasks()
        root.update()
        time.sleep(0.05)


def shot(win, path):
    from PIL import Image
    if isinstance(win, int):
        hwnd = win
    else:
        win.update_idletasks()
        win.update()
        hwnd = win.winfo_id()
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = wt.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    src = u32.GetDC(0)
    mem = g32.CreateCompatibleDC(src)
    bmp = g32.CreateCompatibleBitmap(src, w, h)
    g32.SelectObject(mem, bmp)
    u32.PrintWindow(hwnd, mem, 3)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                    ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                    ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPPM", wt.LONG),
                    ("biYPPM", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bmi = BMI()
    bmi.biSize = ctypes.sizeof(BMI)
    bmi.biWidth, bmi.biHeight = w, -h
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    buf = (ctypes.c_char * (w * h * 4))()
    g32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA",
                           0, 1).convert("RGB")
    g32.DeleteObject(bmp)
    g32.DeleteDC(mem)
    u32.ReleaseDC(0, src)
    img.save(path)
    return img.size


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    out = args[0] if args else os.path.join(REPO, "shot.png")
    seed()
    mock_network()
    app = dw.Widget()
    root = app.root
    pump(root, 5)
    if "--active" in flags:
        app.cfg["locked"] = True
        app.cfg["always_on_top"] = True
        app._redraw_buttons()
        pump(root, 1)
    if "--settings" in flags:
        app._open_settings()
        pump(root, 2.5)
        dlg = getattr(app, "_settings_dlg", None)
        if dlg is None:
            print("设置窗口没打开")
            return 1
        size = shot(dlg, out)
    else:
        size = shot(app.root.winfo_id(), out)
    print("已保存 %s %sx%s" % (out, size[0], size[1]))
    sys.stdout.flush()
    root.destroy()
    os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
