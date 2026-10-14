# ---- freeze patch v4 (2026-10-09): 修复工具条 hover 重绘死循环 ----
# ---- freeze patch v3f (2026-10-09): 用 GA_ROOT 外框矩形夹取窗口 ----
# ---- freeze patch v3e (2026-10-09): 映射后延迟复夹窗口位置 ----
# ---- freeze patch v3d (2026-10-09): 用外框尺寸夹取窗口位置 ----
# ---- freeze patch v3c (2026-10-09): 窗口尺寸变化后重新夹取到屏内 ----
# ---- freeze patch v3b (2026-10-09): 设置窗夹取到屏幕内（含映射后复夹） ----
# ---- freeze patch v3 (2026-10-09): 修复卡死 / 模态窗与阻塞调用 ----
# ---- perf patch v2 (2026-10-09): 降低空闲 CPU / 电源占用 ----
# -*- coding: utf-8 -*-
"""
DeskPulse 桌面看板
- 日期 / 时钟（农历、节日；点击打开系统“日期和时间”设置）
- 当天实时天气（Open-Meteo，免费无 Key；点击打开天气网页）
- 多个监控账户：DeepSeek 余额、火山引擎余额 + 火山方舟当日推理用量
- 余额阈值预警、一键跳转充值；多套护眼配色；界面缩放；窗口锁定
纯 Python 标准库 + tkinter，无第三方依赖，可用 PyInstaller 打包。
"""

import os
import sys
import json
import math
import re
import time
import hmac
import uuid
import base64
import hashlib
import datetime as dt
import threading
import urllib.parse
import urllib.request
import urllib.error
import queue
import ctypes
import ctypes.wintypes
import traceback
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk, filedialog

try:
    import winreg
except Exception:
    winreg = None

APP_NAME = "DeskPulse 桌面看板"
APP_VERSION = "2.5.0"

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "error.log")
CACHE_PATH = os.path.join(BASE_DIR, "balance_cache.json")

DEFAULT_CONFIG = {
    "city": "北京",
    "profiles": [],
    "theme": "midnight",
    "refresh_minutes": 10,
    "opacity": 1.0,
    "always_on_top": False,
    "locked": False,
    "ui_scale": 1.0,
    "window_x": -1,
    "window_y": -1,
    "hotkey_settings": "ctrl+alt+s",
    "hotkey_quit": "ctrl+alt+q",
    "show_seconds": False,
    "autostart": False,
    "check_update": True,
    "notify_low_balance": True,
}


def find_tray_clock_rect():
    """返回任务栏系统时钟（TrayClockWClass）的屏幕矩形 (l,t,r,b)，找不到返回 None。"""
    try:
        u = ctypes.windll.user32
        tray = u.FindWindowW("Shell_TrayWnd", None)
        if not tray:
            return None
        enum_proc_t = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        stack = [tray]
        while stack:
            parent = stack.pop()
            children = []
            cb = enum_proc_t(lambda ch, lp: children.append(ch) or True)
            u.EnumChildWindows(parent, cb, 0)
            for ch in children:
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(ch, buf, 256)
                if buf.value == "TrayClockWClass":
                    r = ctypes.wintypes.RECT()
                    u.GetWindowRect(ch, ctypes.byref(r))
                    return (r.left, r.top, r.right, r.bottom)
                stack.append(ch)
    except Exception:
        return None
    return None


def fmt_hk(spec):
    """ctrl+alt+s -> Ctrl+Alt+S"""
    if not spec:
        return ""
    names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt",
             "shift": "Shift", "win": "Win"}
    out = []
    for part in str(spec).split("+"):
        q = part.strip().lower()
        if not q:
            continue
        if q in names:
            out.append(names[q])
        elif len(q) == 1:
            out.append(q.upper())
        else:
            out.append(q.capitalize())
    return "+".join(out)

CST = dt.timezone(dt.timedelta(hours=8))

UI_SCALE_MIN, UI_SCALE_MAX, UI_SCALE_STEP = 0.85, 1.6, 0.1
HTTP_TIMEOUT = 12
UI_FONT = "Microsoft YaHei UI"
MONO_FONT = "Consolas"

DS_TOPUP_URL = "https://platform.deepseek.com/top_up"
DS_KEYS_URL = "https://platform.deepseek.com/api_keys"
VOLC_TOPUP_URL = "https://console.volcengine.com/finance/fund/recharge/"
VOLC_AK_URL = "https://console.volcengine.com/iam/keymanage/"
SF_TOPUP_URL = "https://cloud.siliconflow.cn/account/charge"
SF_KEYS_URL = "https://cloud.siliconflow.cn/account/ak"
MS_TOPUP_URL = "https://platform.kimi.com/console/pay"
MS_KEYS_URL = "https://platform.kimi.com/console/api-keys"
ZP_TOPUP_URL = "https://www.bigmodel.cn/finance/overview"
ZP_KEYS_URL = "https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys"

# ----------------------------------------------------------------------------
# 主题（柔和护眼，深浅各三套）
# ----------------------------------------------------------------------------
THEMES = {
    "midnight": {
        "name": "深夜蓝", "bg": "#151823", "stroke": "#2A3040",
        "text": "#EDF0F6", "text2": "#9AA3B8", "text3": "#6B7488",
        "accent": "#5B8DEF", "card_alt": "#1B1F2C", "hover": "#2C3346",
        "ds": "#7B93FF", "volc": "#FF8A3D",
        "sf": "#9B8CFF", "ms": "#5B7CFF", "zp": "#4FA3E3", "wb": "#3ECF8E",
        "or": "#F472B6",
        "ok": "#3ECF8E", "warn": "#FFC93C", "err": "#FF6B6B", "errbg": "#3A2530",
        "sun": "#FFC93C", "cloud": "#C7D0E2", "rain": "#6EC1E4", "snow": "#E8F1FF",
    },
    "pine": {
        "name": "墨绿护眼", "bg": "#102018", "stroke": "#24402F",
        "text": "#E4F0E8", "text2": "#93B3A0", "text3": "#678573",
        "accent": "#6FC48E", "card_alt": "#152A1F", "hover": "#1E3A2B",
        "ds": "#7FB8FF", "volc": "#FF9A55",
        "sf": "#9B8CFF", "ms": "#6F8FFF", "zp": "#5AB3E0", "wb": "#3ECF8E",
        "or": "#F472B6",
        "ok": "#55D68A", "warn": "#E8C15A", "err": "#FF7A72", "errbg": "#37262A",
        "sun": "#F2C94C", "cloud": "#AFC9B8", "rain": "#6FC9E0", "snow": "#E2F0EC",
    },
    "cocoa": {
        "name": "暖可可", "bg": "#211B16", "stroke": "#42352A",
        "text": "#F0E7DC", "text2": "#B3A290", "text3": "#847463",
        "accent": "#D2A36C", "card_alt": "#2A221B", "hover": "#3A2F24",
        "ds": "#93A8FF", "volc": "#FF9455",
        "sf": "#A390FF", "ms": "#7B93FF", "zp": "#5AB3E0", "wb": "#3ECF8E",
        "or": "#F472B6",
        "ok": "#7BC489", "warn": "#E8BE65", "err": "#FF7D6E", "errbg": "#40282A",
        "sun": "#F2C14E", "cloud": "#CDBFAF", "rain": "#7FBFD0", "snow": "#EDE4D8",
    },
    "mung": {
        "name": "豆沙绿(浅)", "bg": "#DCE9DC", "stroke": "#B5CCB8",
        "text": "#243329", "text2": "#48594E", "text3": "#6E8274",
        "accent": "#3F8F62", "card_alt": "#CFE0D2", "hover": "#C2D8C6",
        "ds": "#3D5BD9", "volc": "#C25E17",
        "sf": "#6A4FE0", "ms": "#2F54D1", "zp": "#2E6BE0", "wb": "#1F9E57",
        "or": "#C2185B",
        "ok": "#2F9E63", "warn": "#B98514", "err": "#D24A4A", "errbg": "#F2D8D8",
        "sun": "#E8A200", "cloud": "#8FA896", "rain": "#3E8FC4", "snow": "#5A7888",
    },
    "cream": {
        "name": "暖米黄(浅)", "bg": "#F3ECDA", "stroke": "#D8CAA9",
        "text": "#3D3823", "text2": "#64563B", "text3": "#8A7B5C",
        "accent": "#A9782F", "card_alt": "#ECE1C8", "hover": "#E2D3B4",
        "ds": "#3D5BD9", "volc": "#C25E17",
        "sf": "#6A4FE0", "ms": "#2F54D1", "zp": "#2E6BE0", "wb": "#1F9E57",
        "or": "#C2185B",
        "ok": "#3E9460", "warn": "#B07F16", "err": "#CE4B4B", "errbg": "#F0DACE",
        "sun": "#E8A200", "cloud": "#A39880", "rain": "#4E8FB8", "snow": "#6E8494",
    },
    "mist": {
        "name": "雾灰蓝(浅)", "bg": "#E3EAF2", "stroke": "#C2D0E0",
        "text": "#22303D", "text2": "#4A5C70", "text3": "#74869A",
        "accent": "#3F74C8", "card_alt": "#D7E1EC", "hover": "#C8D6E6",
        "ds": "#3D5BD9", "volc": "#C25E17",
        "sf": "#6A4FE0", "ms": "#2F54D1", "zp": "#2E6BE0", "wb": "#1F9E57",
        "or": "#C2185B",
        "ok": "#2F9E63", "warn": "#B08218", "err": "#D24A4A", "errbg": "#F0D9DE",
        "sun": "#E8A200", "cloud": "#8FA0B8", "rain": "#3E8FC4", "snow": "#5A708C",
    },
}


def trans_key(bg):
    """与卡片底色仅差 1 个色阶的透明色键，消除圆角抗锯齿毛边。"""
    r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    bump = lambda v: v + 1 if v < 255 else v - 1
    return f"#{bump(r):02X}{bump(g):02X}{bump(b):02X}"


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def now_cst():
    return dt.datetime.now(CST)


def log_exception(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def safe_grab(win):
    """模态 grab：等整个窗口构造完成后再调用；失败也不会锁死主界面。"""
    try:
        win.update_idletasks()
        win.grab_set()
        return True
    except Exception as e:
        log_exception(f"模态窗口 grab 失败: {e}")
        return False


def clamp_window_pos(win, x, y):
    """把窗口位置夹取进屏幕可见范围，避免模态窗跑到屏幕外点不到。"""
    try:
        win.update_idletasks()
        w = max(win.winfo_width(), win.winfo_reqwidth())
        h = max(win.winfo_height(), win.winfo_reqheight())
        try:
            # winfo_* 只统计客户区；Tk 的 toplevel 有两层 HWND，
            # winfo_id() 指向客户区窗口，必须用 GA_ROOT 取外层（带边框/标题栏）
            _u = ctypes.windll.user32
            _u.GetAncestor.restype = ctypes.c_void_p
            _u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            _u.GetWindowRect.argtypes = [ctypes.c_void_p,
                                         ctypes.POINTER(ctypes.wintypes.RECT)]
            _hwnd = _u.GetAncestor(win.winfo_id(), 2) or win.winfo_id()
            _r = ctypes.wintypes.RECT()
            if _u.GetWindowRect(_hwnd, ctypes.byref(_r)):
                w = max(w, _r.right - _r.left)
                h = max(h, _r.bottom - _r.top)
        except Exception:
            pass
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(0, min(int(x), max(0, sw - w - 8)))
        y = max(0, min(int(y), max(0, sh - h - 8)))
    except Exception:
        pass
    try:
        win.geometry(f"+{int(x)}+{int(y)}")
    except Exception as e:
        log_exception(f"设置窗口位置失败: {e}")


def keep_on_screen(win, limit=4):
    """窗口尺寸/位置变化时把窗口重新夹取进屏幕（只处理前几次，不干扰用户拖动）。"""
    state = {"n": 0}

    def on_configure(event):
        if event.widget is not win or state["n"] >= limit:
            return
        state["n"] += 1
        clamp_window_pos(win, win.winfo_x(), win.winfo_y())

    try:
        win.bind("<Configure>", on_configure, add="+")
    except Exception as e:
        log_exception(f"绑定窗口位置校正失败: {e}")


def reclamp_later(win, x, y, delays=(150, 600)):
    """窗口首次显示后系统才补上标题栏/边框，需要延迟用真实外框尺寸再夹一次。"""
    for d in delays:
        try:
            win.after(d, lambda xx=x, yy=y: clamp_window_pos(win, xx, yy))
        except Exception:
            pass


def open_url_async(url):
    """后台打开外链：webbrowser.open 冷启动可能阻塞数秒，不能占用 UI 线程。"""
    def run():
        try:
            webbrowser.open(url)
        except Exception as e:
            log_exception(f"打开链接失败: {e}")
    threading.Thread(target=run, daemon=True).start()


def _install_crash_log():
    """把未捕获异常与 Tk 回调异常写入 error.log（windowed 模式无控制台，必须落盘）。"""
    def hook(exc_type, exc, tb):
        try:
            text = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:
            text = f"{exc_type}: {exc}"
        log_exception("未捕获异常:\n" + text)
    sys.excepthook = hook
    try:
        import threading
        threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)
    except Exception:
        pass


def fmt_money(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v)


def fmt_tokens(n):
    n = int(n or 0)
    if n >= 10000:
        return f"{n / 10000:.1f}万".replace(".0万", "万")
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def wind_dir(deg):
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    try:
        return dirs[int((float(deg) + 22.5) / 45) % 8]
    except Exception:
        return ""


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:8]


WMO_MAP = {
    0: ("晴", "sun"), 1: ("晴间多云", "partly"), 2: ("多云", "cloud"),
    3: ("阴", "overcast"), 45: ("雾", "fog"), 48: ("雾凇", "fog"),
    51: ("小毛毛雨", "rain"), 53: ("毛毛雨", "rain"), 55: ("浓毛毛雨", "rain"),
    56: ("冻毛毛雨", "rain"), 57: ("冻毛毛雨", "rain"),
    61: ("小雨", "rain"), 63: ("中雨", "rain"), 65: ("大雨", "rain"),
    66: ("冻雨", "rain"), 67: ("冻雨", "rain"),
    71: ("小雪", "snow"), 73: ("中雪", "snow"), 75: ("大雪", "snow"),
    77: ("雪粒", "snow"),
    80: ("小阵雨", "rain"), 81: ("阵雨", "rain"), 82: ("强阵雨", "rain"),
    85: ("阵雪", "snow"), 86: ("强阵雪", "snow"),
    95: ("雷暴", "storm"), 96: ("雷暴伴冰雹", "storm"), 99: ("强雷暴伴冰雹", "storm"),
}


def wmo_desc(code):
    return WMO_MAP.get(int(code), (f"天气({code})", "cloud"))


# 内置常用中国城市坐标（Open-Meteo 地理编码对部分中文城市收录不全）
CITY_COORDS = {
    "北京": (39.9042, 116.4074), "上海": (31.2304, 121.4737),
    "天津": (39.0842, 117.2009), "重庆": (29.5630, 106.5516),
    "香港": (22.3193, 114.1694), "澳门": (22.1987, 113.5439), "台北": (25.0330, 121.5654),
    "广州": (23.1291, 113.2644), "长沙": (28.2282, 112.9388), "武汉": (30.5928, 114.3055),
    "郑州": (34.7466, 113.6254), "济南": (36.6512, 117.1201), "太原": (37.8706, 112.5489),
    "石家庄": (38.0428, 114.5149), "沈阳": (41.8057, 123.4315), "长春": (43.8171, 125.3235),
    "哈尔滨": (45.8038, 126.5350), "南京": (32.0603, 118.7969), "合肥": (31.8206, 117.2272),
    "杭州": (30.2741, 120.1551), "福州": (25.0745, 119.2965), "南昌": (28.6820, 115.8582),
    "海口": (20.0440, 110.1990), "南宁": (22.8170, 108.3669), "成都": (30.5728, 104.0668),
    "贵阳": (26.6470, 106.6302), "昆明": (25.0389, 102.7183), "西安": (34.3416, 108.9398),
    "兰州": (36.0611, 103.8343), "西宁": (36.6171, 101.7782), "银川": (38.4872, 106.2309),
    "乌鲁木齐": (43.8256, 87.6168), "拉萨": (29.6520, 91.1721),
    "呼和浩特": (40.8426, 111.7492),
    "汉中": (33.0671, 107.0231), "宝鸡": (34.3619, 107.2370), "咸阳": (34.3296, 108.7089),
    "渭南": (34.4994, 109.5086), "延安": (36.5853, 109.4897), "榆林": (38.2854, 109.7346),
    "安康": (32.6900, 109.0290), "商洛": (33.8700, 109.9400), "铜川": (34.8966, 108.9450),
    "唐山": (39.6309, 118.1802), "保定": (38.8740, 115.4646), "邯郸": (36.6256, 114.5391),
    "沧州": (38.3037, 116.8388), "廊坊": (39.5377, 116.6837), "张家口": (40.7686, 114.8867),
    "邢台": (37.0682, 114.5046), "秦皇岛": (39.9354, 119.6005), "大同": (40.0768, 113.3001),
    "包头": (40.6574, 109.8403), "鄂尔多斯": (39.6089, 109.7814), "赤峰": (42.2585, 118.8870),
    "通辽": (43.6529, 122.2440), "大连": (38.9140, 121.6147), "鞍山": (41.1106, 122.9942),
    "吉林市": (43.8378, 126.5500), "延吉": (42.8913, 129.5089), "齐齐哈尔": (47.3540, 123.9183),
    "大庆": (46.5907, 125.1038), "牡丹江": (44.5526, 129.6326), "佳木斯": (46.7996, 130.3185),
    "丹东": (40.1295, 124.3937), "锦州": (41.0954, 121.1269), "营口": (40.6675, 122.2349),
    "苏州": (31.2989, 120.5853), "无锡": (31.4912, 120.3119), "常州": (31.8106, 119.9740),
    "南通": (32.0162, 120.8642), "徐州": (34.2058, 117.2839), "扬州": (32.3942, 119.4128),
    "盐城": (33.3476, 120.1633), "镇江": (32.2044, 119.4528), "淮安": (33.6104, 119.0153),
    "连云港": (34.5967, 119.2216), "泰州": (32.4554, 119.9229), "宁波": (29.8683, 121.5440),
    "温州": (27.9938, 120.6994), "绍兴": (30.0303, 120.5802), "金华": (29.0791, 119.6474),
    "嘉兴": (30.7522, 120.7555), "台州": (28.6564, 121.4209), "芜湖": (31.3526, 118.4331),
    "青岛": (36.0671, 120.3826), "烟台": (37.4638, 121.4479), "潍坊": (36.7068, 119.1618),
    "临沂": (35.1045, 118.3564), "淄博": (36.8135, 118.0550), "威海": (37.5131, 122.1205),
    "厦门": (24.4798, 118.0894), "泉州": (24.8741, 118.6757), "赣州": (25.8311, 114.9350),
    "九江": (29.7051, 116.0019),
    "洛阳": (34.6197, 112.4540), "开封": (34.7973, 114.3076), "南阳": (32.9908, 112.5283),
    "宜昌": (30.6917, 111.2864), "襄阳": (32.0090, 112.1227), "株洲": (27.8274, 113.1339),
    "衡阳": (26.8935, 112.5719), "岳阳": (29.3571, 113.1288), "常德": (29.0317, 111.6986),
    "郴州": (25.7706, 113.0147), "深圳": (22.5431, 114.0579), "东莞": (23.0207, 113.7518),
    "佛山": (23.0218, 113.1219), "珠海": (22.2707, 113.5767), "中山": (22.5170, 113.3927),
    "惠州": (23.1116, 114.4162), "汕头": (23.3535, 116.6820), "湛江": (21.2706, 110.3594),
    "韶关": (24.8106, 113.5976), "江门": (22.5787, 113.0823), "肇庆": (23.0470, 112.4655),
    "茂名": (21.6630, 110.9254), "桂林": (25.2736, 110.2900), "柳州": (24.3260, 109.4118),
    "梧州": (23.4767, 111.2791), "北海": (21.4811, 109.1202), "玉林": (22.6541, 110.1809),
    "三亚": (18.2528, 109.5119),
    "绵阳": (31.4677, 104.6796), "南充": (30.8373, 106.1107), "宜宾": (28.7513, 104.6419),
    "泸州": (28.8717, 105.4426), "德阳": (31.1270, 104.3981), "乐山": (29.5521, 103.7654),
    "达州": (31.2090, 107.4681), "广元": (32.4357, 105.8428), "遂宁": (30.5333, 105.5928),
    "内江": (29.5801, 105.0586), "自贡": (29.3392, 104.7784), "遵义": (27.7257, 106.9274),
    "六盘水": (26.5930, 104.8333), "曲靖": (25.4900, 103.7966), "天水": (34.5809, 105.7249),
    "白银": (36.5447, 104.1389), "武威": (37.9283, 102.6346), "张掖": (38.9259, 100.4493),
    "酒泉": (39.7322, 98.4941), "吴忠": (37.9976, 106.1969), "石嘴山": (39.0139, 106.3800),
    "格尔木": (36.4016, 94.9228), "日喀则": (29.2671, 88.8808), "克拉玛依": (45.5799, 84.8892),
    "喀什": (39.4704, 75.9898), "伊宁": (43.9168, 81.3242),
}


# ----------------------------------------------------------------------------
# 农历（1900-2099，经典查表法）
# ----------------------------------------------------------------------------
LUNAR_INFO = [
    0x04BD8, 0x04AE0, 0x0A570, 0x054D5, 0x0D260, 0x0D950, 0x16554, 0x056A0, 0x09AD0, 0x055D2,
    0x04AE0, 0x0A5B6, 0x0A4D0, 0x0D250, 0x1D255, 0x0B540, 0x0D6A0, 0x0ADA2, 0x095B0, 0x14977,
    0x04970, 0x0A4B0, 0x0B4B5, 0x06A50, 0x06D40, 0x1AB54, 0x02B60, 0x09570, 0x052F2, 0x04970,
    0x06566, 0x0D4A0, 0x0EA50, 0x06E95, 0x05AD0, 0x02B60, 0x186E3, 0x092E0, 0x1C8D7, 0x0C950,
    0x0D4A0, 0x1D8A6, 0x0B550, 0x056A0, 0x1A5B4, 0x025D0, 0x092D0, 0x0D2B2, 0x0A950, 0x0B557,
    0x06CA0, 0x0B550, 0x15355, 0x04DA0, 0x0A5B0, 0x14573, 0x052B0, 0x0A9A8, 0x0E950, 0x06AA0,
    0x0AEA6, 0x0AB50, 0x04B60, 0x0AAE4, 0x0A570, 0x05260, 0x0F263, 0x0D950, 0x05B57, 0x056A0,
    0x096D0, 0x04DD5, 0x04AD0, 0x0A4D0, 0x0D4D4, 0x0D250, 0x0D558, 0x0B540, 0x0B6A0, 0x195A6,
    0x095B0, 0x049B0, 0x0A974, 0x0A4B0, 0x0B27A, 0x06A50, 0x06D40, 0x0AF46, 0x0AB60, 0x09570,
    0x04AF5, 0x04970, 0x064B0, 0x074A3, 0x0EA50, 0x06B58, 0x055C0, 0x0AB60, 0x096D5, 0x092E0,
    0x0C960, 0x0D954, 0x0D4A0, 0x0DA50, 0x07552, 0x056A0, 0x0ABB7, 0x025D0, 0x092D0, 0x0CAB5,
    0x0A950, 0x0B4A0, 0x0BAA4, 0x0AD50, 0x055D9, 0x04BA0, 0x0A5B0, 0x15176, 0x052B0, 0x0A930,
    0x07954, 0x06AA0, 0x0AD50, 0x05B52, 0x04B60, 0x0A6E6, 0x0A4E0, 0x0D260, 0x0EA65, 0x0D530,
    0x05AA0, 0x076A3, 0x096D0, 0x04AFB, 0x04AD0, 0x0A4D0, 0x1D0B6, 0x0D250, 0x0D520, 0x0DD45,
    0x0B5A0, 0x056D0, 0x055B2, 0x049B0, 0x0A577, 0x0A4B0, 0x0AA50, 0x1B255, 0x06D20, 0x0ADA0,
    0x14B63, 0x09370, 0x049F8, 0x04970, 0x064B0, 0x168A6, 0x0EA50, 0x06B20, 0x1A6C4, 0x0AAE0,
    0x0A2E0, 0x0D2E3, 0x0C960, 0x0D557, 0x0D4A0, 0x0DA50, 0x05D55, 0x056A0, 0x0A6D0, 0x055D4,
    0x052D0, 0x0A9B8, 0x0A950, 0x0B4A0, 0x0B6A6, 0x0AD50, 0x055A0, 0x0ABA4, 0x0A5B0, 0x052B0,
    0x0B273, 0x06930, 0x0733F, 0x06AA0, 0x0AD50, 0x14B55, 0x04B60, 0x0A570, 0x054E4, 0x0D160,
    0x0E968, 0x0D520, 0x0DAA0, 0x16AA6, 0x056D0, 0x04AE0, 0x0A9D4, 0x0A2D0, 0x0D150, 0x0F252,
    0x0D520,
]
LUNAR_MONTH = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
LUNAR_DAY1 = ["初", "十", "廿", "卅"]
LUNAR_DAYN = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]


def _lunar_leap_month(y):
    return LUNAR_INFO[y - 1900] & 0xF


def _lunar_leap_days(y):
    if _lunar_leap_month(y):
        return 30 if (LUNAR_INFO[y - 1900] >> 16) & 0xF else 29
    return 0


def _lunar_month_days(y, m):
    return 30 if (LUNAR_INFO[y - 1900] & (0x10000 >> m)) else 29


def _lunar_year_days(y):
    total = 348
    i = 0x8000
    while i > 0x8:
        if LUNAR_INFO[y - 1900] & i:
            total += 1
        i >>= 1
    return total + _lunar_leap_days(y)


def solar_to_lunar(d):
    base = dt.date(1900, 1, 31)
    offset = (dt.date(d.year, d.month, d.day) - base).days
    year = 1900
    while year < 2100:
        ydays = _lunar_year_days(year)
        if offset < ydays:
            break
        offset -= ydays
        year += 1
    leap = _lunar_leap_month(year)
    seq = []
    for m in range(1, 13):
        seq.append((m, False))
        if leap > 0 and m == leap:
            seq.append((m, True))
    month, is_leap = 1, False
    for m, lp in seq:
        mdays = _lunar_leap_days(year) if lp else _lunar_month_days(year, m)
        if offset < mdays:
            month, is_leap = m, lp
            break
        offset -= mdays
    return year, month, offset + 1, is_leap


def lunar_day_str(day):
    if day == 10:
        return "初十"
    if day == 20:
        return "二十"
    if day == 30:
        return "三十"
    return LUNAR_DAY1[(day - 1) // 10] + LUNAR_DAYN[(day - 1) % 10]


def lunar_month_str(month, is_leap):
    return ("闰" if is_leap else "") + LUNAR_MONTH[month - 1] + "月"


SOLAR_FESTIVAL = {
    (1, 1): "元旦", (2, 14): "情人节", (3, 8): "妇女节", (3, 12): "植树节",
    (4, 1): "愚人节", (5, 1): "劳动节", (5, 4): "青年节", (6, 1): "儿童节",
    (7, 1): "建党节", (8, 1): "建军节", (9, 10): "教师节", (10, 1): "国庆节",
    (12, 24): "平安夜", (12, 25): "圣诞节",
}
LUNAR_FESTIVAL = {
    (1, 1): "春节", (1, 15): "元宵节", (2, 2): "龙抬头", (5, 5): "端午节",
    (7, 7): "七夕", (7, 15): "中元节", (8, 15): "中秋节", (9, 9): "重阳节",
    (12, 8): "腊八节", (12, 23): "北方小年", (12, 24): "南方小年",
}


def chuxi_check(d):
    try:
        _, lm, _, leap = solar_to_lunar(d)
        if leap or lm != 12:
            return False
        nxt = d + dt.timedelta(days=1)
        _, nm, nd, _ = solar_to_lunar(nxt)
        return nm == 1 and nd == 1
    except Exception:
        return False


def lunar_line(d):
    try:
        y, lm, ld, leap = solar_to_lunar(d)
        text = "农历" + lunar_month_str(lm, leap) + lunar_day_str(ld)
        fest = SOLAR_FESTIVAL.get((d.month, d.day))
        if not fest:
            fest = LUNAR_FESTIVAL.get((lm, ld)) if not leap else None
        if not fest and chuxi_check(d):
            fest = "除夕"
        if fest:
            text += " · " + fest
        return text
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# 配置
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# 密钥来源：环境变量兜底 + Windows DPAPI 加密存储
# ----------------------------------------------------------------------------
SECRET_FIELDS = ("api_key", "ak", "sk")
ENC_PREFIX = "enc:"

# 平台密钥对应的环境变量名（按顺序取第一个非空的）
ENV_KEY_MAP = {
    "deepseek": {"api_key": ("DEEPSEEK_API_KEY", "DEEPSEEK_KEY")},
    "volc": {"ak": ("VOLC_ACCESS_KEY_ID",),
             "sk": ("VOLC_SECRET_ACCESS_KEY",)},
    "siliconflow": {"api_key": ("SILICONFLOW_API_KEY",)},
    "moonshot": {"api_key": ("MOONSHOT_API_KEY", "KIMI_API_KEY")},
    "zhipu": {"api_key": ("ZHIPU_API_KEY", "ZHIPUAI_API_KEY")},
    "openrouter": {"api_key": ("OPENROUTER_API_KEY",)},
}


def env_key_for(ptype, field):
    """取该平台该字段对应的环境变量值；没有就返回空串。"""
    names = ENV_KEY_MAP.get(ptype or "", {}).get(field) or ()
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def apply_env_keys(cfg):
    """给「还没填密钥」的账户补上环境变量里的值，返回补了哪些字段。

    只补空字段，绝不覆盖用户在界面里填过的值。
    """
    filled = []
    for p in (cfg.get("profiles") or []):
        for field in SECRET_FIELDS:
            if (p.get(field) or "").strip():
                continue
            val = env_key_for(p.get("type"), field)
            if val:
                p[field] = val
                filled.append((p.get("id"), field))
    return filled


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_in(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def dpapi_crypt(data, protect):
    """调用 Windows DPAPI（当前用户级）。失败返回 None。"""
    try:
        blob_in = _blob_in(data)
        blob_out = DATA_BLOB()
        fn = (ctypes.windll.crypt32.CryptProtectData if protect
              else ctypes.windll.crypt32.CryptUnprotectData)
        if not fn(ctypes.byref(blob_in), None, None, None, None, 0,
                  ctypes.byref(blob_out)):
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    except Exception:
        return None


def encrypt_secret(value):
    """把明文密钥加密成 enc:xxx；加密不可用时原样返回（保证功能不挂）。"""
    if not value or not isinstance(value, str) or value.startswith(ENC_PREFIX):
        return value
    enc = dpapi_crypt(value.encode("utf-8"), True)
    if not enc:
        return value
    return ENC_PREFIX + base64.b64encode(enc).decode("ascii")


def decrypt_secret(value):
    """把 enc:xxx 还原成明文；不是密文就原样返回。"""
    if not isinstance(value, str) or not value.startswith(ENC_PREFIX):
        return value
    try:
        raw = base64.b64decode(value[len(ENC_PREFIX):])
    except Exception:
        return ""
    out = dpapi_crypt(raw, False)
    return out.decode("utf-8", "replace") if out is not None else ""


def decrypt_config_secrets(cfg):
    """读取配置时把密钥字段解密（同时兼容 v1.x 的顶层旧字段）。"""
    for p in (cfg.get("profiles") or []):
        for field in SECRET_FIELDS:
            if field in p:
                p[field] = decrypt_secret(p.get(field))
    for old in ("deepseek_api_key", "volc_access_key_id",
                "volc_secret_access_key"):
        if old in cfg:
            cfg[old] = decrypt_secret(cfg.get(old))


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
            if isinstance(user_cfg, dict):
                cfg.update(user_cfg)
    except Exception as e:
        log_exception(f"读取配置失败: {e}")
    decrypt_config_secrets(cfg)
    migrate_config(cfg)
    apply_env_keys(cfg)          # 界面里没填的密钥，用环境变量兜底
    return cfg


def migrate_config(cfg):
    """兼容 v1.x 单密钥配置，迁移为多账户 profiles。"""
    if not isinstance(cfg.get("profiles"), list):
        cfg["profiles"] = []
    if cfg.get("deepseek_api_key") and not any(
            p.get("type") == "deepseek" for p in cfg["profiles"]):
        cfg["profiles"].append({
            "id": new_id("ds"), "type": "deepseek", "name": "DeepSeek",
            "api_key": cfg["deepseek_api_key"], "threshold": 0})
    if cfg.get("volc_access_key_id") and cfg.get("volc_secret_access_key") and not any(
            p.get("type") == "volc" for p in cfg["profiles"]):
        cfg["profiles"].append({
            "id": new_id("volc"), "type": "volc", "name": "火山方舟",
            "ak": cfg["volc_access_key_id"], "sk": cfg["volc_secret_access_key"],
            "threshold": 0})
    for p in cfg["profiles"]:
        p.setdefault("threshold", 0)
        p.setdefault("name", platform_label(p.get("type")))
    if cfg.get("theme") not in THEMES:
        cfg["theme"] = "midnight"


def save_config(cfg):
    try:
        out = json.loads(json.dumps(cfg))
        for p in (out.get("profiles") or []):
            for field in SECRET_FIELDS:
                cur = p.get(field)
                # 值来自环境变量就写空，避免把环境变量里的密钥落到磁盘上
                if (isinstance(cur, str) and cur
                        and cur == env_key_for(p.get("type"), field)):
                    p[field] = ""
                else:
                    p[field] = encrypt_secret(cur)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log_exception(f"保存配置失败: {e}")
        return False


# ----------------------------------------------------------------------------
# HTTP 与数据源
# ----------------------------------------------------------------------------
def http_json(url, headers=None, data=None, method=None):
    headers = dict(headers or {})
    if data is not None:
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        method = method or "POST"
    else:
        body = None
        method = method or "GET"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _secret_problem(v, label, ark_check=False):
    """返回密钥中的问题描述；无问题返回空串。文案控制在卡片一行内。"""
    if not v:
        return f"未填写{label}"
    if any(ord(c) < 33 or ord(c) > 126 for c in v):
        return f"{label}含中文或空格，请重新填写"
    if ark_check and "ark-" in v.lower():
        return "需填IAM密钥(AKLT开头)，非ark-Key"
    return ""


def _check_volc_cred(ak, sk):
    for label, v in (("AK", ak), ("SK", sk)):
        problem = _secret_problem(v, label, ark_check=True)
        if problem:
            raise RuntimeError(problem)
    if len(ak) < 20:
        raise RuntimeError("AK格式有误，应为AKLT开头")


def fetch_deepseek(api_key):
    problem = _secret_problem(api_key, "API Key")
    if problem:
        raise RuntimeError(problem)
    data = http_json(
        "https://api.deepseek.com/user/balance",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    infos = data.get("balance_infos") or []
    if not infos:
        raise RuntimeError("返回余额为空")
    pick = next((i for i in infos if i.get("currency") == "CNY"), infos[0])
    return {
        "available": bool(data.get("is_available", True)),
        "currency": pick.get("currency", ""),
        "total": pick.get("total_balance", "0"),
        "granted": pick.get("granted_balance", "0"),
        "topped_up": pick.get("topped_up_balance", "0")}


def _volc_sign(method, host, path, query, service, region, ak, sk,
               body_bytes=b"", content_type="application/json; charset=utf-8"):
    def hmac_sha(key, msg):
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    x_date = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    safe = "-_.~"
    canonical_query = "&".join(
        f"{urllib.parse.quote(str(k), safe=safe)}={urllib.parse.quote(str(v), safe=safe)}"
        for k, v in sorted(query.items()))
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        f"content-type:{content_type}\nhost:{host}\n"
        f"x-content-sha256:{payload_hash}\nx-date:{x_date}\n")
    canonical_request = "\n".join([
        method.upper(), path, canonical_query,
        canonical_headers, signed_headers, payload_hash])
    scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()])
    k = hmac_sha(sk.encode("utf-8"), short_date)
    k = hmac_sha(k, region)
    k = hmac_sha(k, service)
    k = hmac_sha(k, "request")
    signature = hmac.new(k, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()
    headers = {
        "Host": host, "Content-Type": content_type,
        "X-Date": x_date, "X-Content-Sha256": payload_hash,
        "Authorization": f"HMAC-SHA256 Credential={ak}/{scope}, "
                         f"SignedHeaders={signed_headers}, Signature={signature}"}
    return headers, canonical_query


def _volc_request(method, host, path, query, service, region, ak, sk, body=None):
    body_bytes = json.dumps(
        body, separators=(",", ":"), ensure_ascii=False).encode("utf-8") if body is not None else b""
    headers, cq = _volc_sign(method, host, path, query, service, region,
                             ak, sk, body_bytes)
    url = f"https://{host}{path}?{cq}"
    req = urllib.request.Request(
        url, data=body_bytes if body is not None else None,
        headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {detail[:160]}")


def fetch_volc_balance(ak, sk):
    _check_volc_cred(ak, sk)
    data = _volc_request(
        "GET", "open.volcengineapi.com", "/",
        {"Action": "QueryBalanceAcct", "Version": "2022-01-01"},
        "billing", "cn-north-1", ak, sk)
    r = data.get("Result") or {}
    if not r:
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:160]}")
    return {
        "available": r.get("AvailableBalance", "0"),
        "cash": r.get("CashBalance", "0"),
        "credit": r.get("CreditLimit", "0"),
        "freeze": r.get("FreezeAmount", "0"),
        "arrears": r.get("ArrearsBalance", "0"),
        "account_id": r.get("AccountID", "")}


def fetch_ark_usage(ak, sk):
    _check_volc_cred(ak, sk)
    today = now_cst().strftime("%Y-%m-%d")
    data = _volc_request(
        "POST", "ark.cn-beijing.volcengineapi.com", "/",
        {"Action": "GetInferenceUsage", "Version": "2024-01-01"},
        "ark", "cn-beijing", ak, sk,
        body={"QueryInterval": "Hour", "StartTime": today, "EndTime": today})
    r = data.get("Result") or {}
    fields = [f.get("Name") for f in (r.get("Fields") or [])]
    rows = r.get("Data") or []
    totals = {"input": 0, "output": 0, "total": 0, "req": 0}
    if fields and rows:
        idx = {name: i for i, name in enumerate(fields)}
        for row in rows:
            def g(name):
                i = idx.get(name)
                if i is None or i >= len(row) or row[i] in (None, ""):
                    return 0
                try:
                    return int(float(row[i]))
                except Exception:
                    return 0
            totals["input"] += g("InputTokens")
            totals["output"] += g("OutputTokens")
            totals["total"] += g("TotalTokens")
            totals["req"] += g("ReqCnt")
    return totals


def fetch_siliconflow(api_key):
    problem = _secret_problem(api_key, "API Key")
    if problem:
        raise RuntimeError(problem)
    data = http_json(
        "https://api.siliconflow.cn/v1/user/info",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    d = data.get("data") or {}
    if not d or "balance" not in d:
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:120]}")
    return {
        "available": True, "currency": "CNY",
        "total": str(d.get("balance", "0")),
        "topped_up": str(d.get("chargeBalance", "0")),
        "granted": str(d.get("giftBalance", "0"))}


def fetch_moonshot(api_key):
    problem = _secret_problem(api_key, "API Key")
    if problem:
        raise RuntimeError(problem)
    data = http_json(
        "https://api.moonshot.cn/v1/users/me/balance",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    d = data.get("data") or {}
    if not d or "available_balance" not in d:
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:120]}")
    return {
        "available": float(d.get("available_balance") or 0) > 0, "currency": "CNY",
        "total": str(d.get("available_balance", "0")),
        "topped_up": str(d.get("cash_balance", "0")),
        "granted": str(d.get("voucher_balance", "0"))}


def fetch_zhipu(api_key):
    """智谱 GLM：无公开余额接口，读取 Token 配额用量（实验性）。"""
    problem = _secret_problem(api_key, "API Key")
    if problem:
        raise RuntimeError(problem)
    data = http_json(
        "https://bigmodel.cn/api/monitor/usage/quota/limit",
        headers={"Authorization": api_key, "Accept": "application/json"})
    limits = (data.get("data") or {}).get("limits") or []
    if not limits:
        # 智谱鉴权失败时也返回 HTTP 200，错误在 JSON 里
        code = data.get("code")
        msg = str(data.get("msg") or "").strip()
        if code not in (None, 0, 200, "0", "200"):
            extra = ("（智谱 Key 形如 ID.密钥 两段，检查是否整段复制）"
                     if str(code) == "1000" else "")
            raise RuntimeError(f"平台返回：{msg or code}{extra}")
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:120]}")
    tok = next((l for l in limits if l.get("type") == "TOKENS_LIMIT"),
               limits[0])
    return {
        "usage": int(tok.get("currentValue") or 0),
        "quota": int(tok.get("usage") or 0),
        "percentage": int(tok.get("percentage") or 0),
        "kind": "quota"}


UPDATE_REPO = "youkangduan2008-sys/DeskPulse"


def version_tuple(s):
    """把 "2.1.0" / "v2.1" 变成可比较的元组；解析不出来返回 ()。"""
    parts = []
    for chunk in str(s or "").strip().lstrip("vV").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            return ()
        parts.append(int(digits))
    return tuple(parts)


def fetch_latest_release():
    """查 GitHub 上最新 Release 的版本号与页面地址。"""
    data = http_json(
        f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "DeskPulse-UpdateCheck"})
    tag = str(data.get("tag_name") or "").strip().lstrip("vV")
    url = str(data.get("html_url")
              or f"https://github.com/{UPDATE_REPO}/releases")
    if not version_tuple(tag):
        raise RuntimeError("返回里没有可用的版本号")
    return tag, url


def fetch_openrouter(api_key):
    """OpenRouter：credits 给出累计充值与累计消耗，相减即剩余额度（美元）。"""
    problem = _secret_problem(api_key, "API Key")
    if problem:
        raise RuntimeError(problem)
    data = http_json(
        "https://openrouter.ai/api/v1/credits",
        headers={"Authorization": f"Bearer {api_key}",
                 "Accept": "application/json"})
    d = data.get("data") or {}
    if "total_credits" not in d:
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:120]}")
    total = float(d.get("total_credits") or 0)
    used = float(d.get("total_usage") or 0)
    return {"available": True, "currency": "USD",
            "total": f"{total - used:.2f}",
            "sub": f"累计充值 ${total:.2f} · 已用 ${used:.2f}"}


# ---- Windows 系统通知（托盘气泡，不依赖第三方库） ----
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_ICON, NIF_TIP, NIF_INFO = 0x02, 0x04, 0x10
NIIF_INFO = 0x01
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
IDI_APPLICATION = 32512


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                ("hWnd", ctypes.wintypes.HWND),
                ("uID", ctypes.wintypes.UINT),
                ("uFlags", ctypes.wintypes.UINT),
                ("uCallbackMessage", ctypes.wintypes.UINT),
                ("hIcon", ctypes.wintypes.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.wintypes.DWORD),
                ("dwStateMask", ctypes.wintypes.DWORD),
                ("szInfo", ctypes.c_wchar * 256),
                ("uTimeout", ctypes.wintypes.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.wintypes.DWORD)]


class Balloon:
    """在托盘挂一个图标，用它弹系统通知；退出前需要 close() 否则图标会残留。"""

    UID = 0xD35C

    def __init__(self):
        self.hwnd = None
        self.hicon = None
        self.active = False

    def attach(self, hwnd):
        self.hwnd = hwnd
        path = os.path.join(BASE_DIR, "app.ico")
        for args in ((path, LR_LOADFROMFILE), ):
            try:
                self.hicon = ctypes.windll.user32.LoadImageW(
                    None, args[0], IMAGE_ICON, 0, 0, args[1] | LR_DEFAULTSIZE)
            except Exception:
                self.hicon = None
            if self.hicon:
                break
        if not self.hicon:
            self.hicon = ctypes.windll.user32.LoadImageW(
                None, ctypes.c_wchar_p(IDI_APPLICATION), IMAGE_ICON, 0, 0, 0)
        nid = self._nid()
        nid.uFlags = NIF_ICON | NIF_TIP
        nid.szTip = "DeskPulse 桌面看板"
        self.active = bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_ADD,
                                                                   ctypes.byref(nid)))

    def _nid(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = self.UID
        nid.hIcon = self.hicon
        return nid

    def notify(self, title, text):
        if not self.active:
            return False
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = NIIF_INFO
        nid.uTimeout = 10000
        return bool(ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY,
                                                            ctypes.byref(nid)))

    def close(self):
        if not self.active:
            return
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE,
                                                    ctypes.byref(self._nid()))
        except Exception:
            pass
        self.active = False


WB_INFO_PATHS = [
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 "CodeBuddyExtension", "Data", "Public", "auth",
                 "workbuddy-desktop.info"),
    os.path.join(os.environ.get("APPDATA", ""),
                 "CodeBuddyExtension", "Data", "Public", "auth",
                 "workbuddy-desktop.info"),
]


def fetch_workbuddy():
    """WorkBuddy 积分：读取本机桌面端登录态（明文），调 Buddy加油站状态接口。

    桌面端 5.6.0+ 把 accessToken 改为 $wbEncrypted 加密信封，本工具不破解、
    不绕过，检测到加密格式直接报「暂不支持」。
    """
    info = None
    for path in WB_INFO_PATHS:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    info = json.load(f)
            except Exception:
                continue
            break
    if not info:
        raise RuntimeError("未找到桌面端登录态，请先登录 WorkBuddy")
    auth = info.get("auth") or {}
    token, domain = auth.get("accessToken"), auth.get("domain")
    if isinstance(token, dict) and token.get("$wbEncrypted"):
        raise RuntimeError("桌面端5.6+凭据已加密，暂不支持自动读取")
    if not token or not domain:
        raise RuntimeError("登录态不完整，请打开 WorkBuddy 桌面端登录")
    data = http_json(f"https://{domain}/v2/billing/meter/checkin-activity-status",
                     headers={"Authorization": f"Bearer {token}",
                              "Content-Type": "application/json"}, data={})
    d = data.get("data") or {}
    if "total_credits" not in d:
        raise RuntimeError(f"返回异常: {json.dumps(data, ensure_ascii=False)[:120]}")
    streak = d.get("streak_days") or 0
    checked = d.get("today_checked_in")
    sub = f"连续签到 {streak} 天 · " + ("今日已签" if checked else "今日未签")
    return {"available": True, "currency": "PTS",
            "total": d.get("total_credits") or 0, "sub": sub}


# ----------------------------------------------------------------------------
# 平台注册表（新增平台 = 加一条配置 + 一个 fetch 函数）
# ----------------------------------------------------------------------------
PLATFORMS = {
    "deepseek": {
        "label": "DeepSeek", "brand": "ds", "id_prefix": "ds",
        "fields": [("api_key", "API Key", True)],
        "keys_url": DS_KEYS_URL, "topup_url": DS_TOPUP_URL,
        "hint": "获取 Key：platform.deepseek.com/api_keys",
        "fetch": fetch_deepseek},
    "volc": {
        "label": "火山方舟", "brand": "volc", "id_prefix": "volc",
        "fields": [("ak", "Access Key ID", False),
                   ("sk", "Secret Access Key", True)],
        "keys_url": VOLC_AK_URL, "topup_url": VOLC_TOPUP_URL,
        "hint": "获取 AK/SK：console.volcengine.com/iam（AKLT 开头）",
        "fetch": None},
    "siliconflow": {
        "label": "硅基流动", "brand": "sf", "id_prefix": "sf",
        "fields": [("api_key", "API Key", True)],
        "keys_url": SF_KEYS_URL, "topup_url": SF_TOPUP_URL,
        "hint": "获取 Key：cloud.siliconflow.cn → 账户 → API 密钥",
        "fetch": fetch_siliconflow},
    "moonshot": {
        "label": "Kimi", "brand": "ms", "id_prefix": "ms",
        "fields": [("api_key", "API Key", True)],
        "keys_url": MS_KEYS_URL, "topup_url": MS_TOPUP_URL,
        "hint": "获取 Key：platform.moonshot.cn → API Key 管理",
        "fetch": fetch_moonshot},
    "zhipu": {
        "label": "智谱GLM(配额)", "brand": "zp", "id_prefix": "zp",
        "fields": [("api_key", "API Key", True)],
        "keys_url": ZP_KEYS_URL, "topup_url": ZP_TOPUP_URL,
        "hint": "获取 Key：bigmodel.cn → API Keys（显示配额用量）",
        "fetch": fetch_zhipu},
    "openrouter": {
        "label": "OpenRouter", "brand": "or", "id_prefix": "or",
        "fields": [("api_key", "API Key", True)],
        "keys_url": "https://openrouter.ai/settings/keys",
        "topup_url": "https://openrouter.ai/credits",
        "hint": "获取 Key：openrouter.ai/settings/keys",
        "fetch": fetch_openrouter},
    "workbuddy": {
        "label": "WorkBuddy积分", "brand": "wb", "id_prefix": "wb",
        "fields": [],
        "keys_url": "https://www.workbuddy.cn", "topup_url": "https://www.workbuddy.cn",
        "hint": "自动读取本机桌面端登录态，无需填写 Key",
        "fetch": None},
}


def platform_label(ptype):
    return PLATFORMS.get(ptype, {}).get("label", ptype or "未知平台")


def balance_main_value(ptype, bal):
    """余额字典里用于'大数字'和'较上次'对比的主数值（float），取不出返回 None。"""
    if not isinstance(bal, dict):
        return None
    key = "available" if ptype == "volc" else "total"
    try:
        return float(bal.get(key))
    except (TypeError, ValueError):
        return None


def load_balance_cache():
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def save_balance_cache(cache):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# 开机自启（HKCU Run 键，无需管理员权限）
# ----------------------------------------------------------------------------
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "DeskPulse"


def _autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{os.path.abspath(__file__)}"'


def autostart_get():
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as k:
            v, _ = winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return bool(v)
    except Exception:
        return False


def autostart_set(enable):
    """返回 (成功与否, 提示信息)。"""
    if not winreg:
        return False, "当前环境不支持注册表操作"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ,
                                  _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        return False, str(e)


def fetch_weather(city):
    q = city.strip()
    if q in CITY_COORDS:
        lat, lon = CITY_COORDS[q]
        loc_name, admin1 = q, ""
    else:
        enc = urllib.parse.quote(q)
        geo = http_json(
            f"https://geocoding-api.open-meteo.com/v1/search?name={enc}&count=1"
            "&language=zh&format=json")
        results = geo.get("results") or []
        if not results:
            raise RuntimeError(f"未找到城市「{city}」，可尝试拼音")
        loc = results[0]
        lat, lon = loc["latitude"], loc["longitude"]
        loc_name, admin1 = loc.get("name", city), loc.get("admin1") or ""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
        "weather_code,precipitation,wind_speed_10m,wind_direction_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&timezone=Asia%2FShanghai&forecast_days=1&wind_speed_unit=kmh")
    w = http_json(url)
    cur, daily = w.get("current") or {}, w.get("daily") or {}
    return {
        "city": loc_name, "admin1": admin1,
        "temp": cur.get("temperature_2m"), "feels": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "is_day": cur.get("is_day", 1), "code": cur.get("weather_code", 0),
        "precip": cur.get("precipitation", 0),
        "wind": cur.get("wind_speed_10m", 0), "wind_dir": cur.get("wind_direction_10m", 0),
        "tmax": (daily.get("temperature_2m_max") or [None])[0],
        "tmin": (daily.get("temperature_2m_min") or [None])[0],
        "pop": (daily.get("precipitation_probability_max") or [None])[0]}


# ----------------------------------------------------------------------------
# 取 Key / 充值的入口文案（2026-10 校准）
#   keys_path / topup_path：登录后点哪几步——平台改版也能照着找到，是主指引
#   key_tip：这个平台最容易复制错的地方
# 说明：DeepSeek / 智谱 / OpenRouter 的控制台是单页应用，任意路径都返回同一个
#      页面外壳，程序无法判定深链接是否有效，因此以「文字路径」为主、直达按钮为辅。
# ----------------------------------------------------------------------------
PLATFORM_GUIDE = {
    "deepseek": {
        "keys_path": "登录 platform.deepseek.com → 左侧「API keys」→ 创建 API key",
        "topup_path": "同一控制台左侧「充值 / Top up」",
        "key_tip": "创建后弹窗里的整串（sk- 开头）只显示一次，要整段复制；"
                   "列表里的「名称」和 ID 都不是 Key。",
    },
    "volc": {
        "keys_path": "控制台 → 访问控制 → API 访问密钥 → 创建 Access Key",
        "topup_path": "控制台 → 费用中心 → 充值",
        "key_tip": "要填两个值：Access Key ID（AKLT 开头）+ Secret Access Key"
                   "（只在创建时显示一次）。ark- 开头的是推理 Key，查不了余额。",
    },
    "siliconflow": {
        "keys_path": "登录 cloud.siliconflow.cn → 账户 → API 密钥",
        "topup_path": "账户 → 费用 / 充值",
        "key_tip": "sk- 开头整串，别只复制列表里的「名称」。",
    },
    "moonshot": {
        "keys_path": "登录 platform.kimi.com → API Key 管理 → 新建",
        "topup_path": "控制台 → 账户 → 充值",
        "key_tip": "注意别复制成「Key ID」那一列；旧域名 platform.moonshot.cn "
                   "会自动跳到 kimi.com。",
    },
    "zhipu": {
        "keys_path": "登录 bigmodel.cn → 个人中心 → API Keys / 项目管理",
        "topup_path": "个人中心 → 财务 / 充值",
        "key_tip": "智谱的 Key 是「ID.密钥」两段式，中间有一个点号，必须整段复制；"
                   "只复制点号前面那半截是最常见的错误（接口会报「身份验证失败」）。",
    },
    "openrouter": {
        "keys_path": "登录 openrouter.ai → Settings → Keys → Create Key",
        "topup_path": "Settings → Credits（充值后额度即到账）",
        "key_tip": "sk-or-v1- 开头整串；创建后只显示一次。",
    },
    "workbuddy": {
        "keys_path": "无需 Key，登录桌面端即可自动读取积分",
        "topup_path": "在客户端内完成签到 / 充值",
        "key_tip": "桌面端 5.6.0 起本地凭据已加密，本工具不破解，会如实提示不支持。",
    },
}

for _ptype, _g in PLATFORM_GUIDE.items():
    if _ptype in PLATFORMS:
        PLATFORMS[_ptype].update(_g)


# ----------------------------------------------------------------------------
# 后台刷新线程
# ----------------------------------------------------------------------------
class RefreshThread(threading.Thread):
    def __init__(self, cfg_getter, out_queue):
        super().__init__(daemon=True)
        self.cfg_getter = cfg_getter
        self.q = out_queue
        self.evt_weather = threading.Event()
        self.evt_platforms = threading.Event()
        self.stop_event = threading.Event()
        self.weather_due = 0.0
        self.update_due = time.time() + 15
        self.platforms_due = 0.0

    def refresh_weather(self):
        self.evt_weather.set()

    def refresh_platforms(self):
        self.evt_platforms.set()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                now = time.time()
                if self.evt_weather.is_set() or now >= self.weather_due:
                    self.evt_weather.clear()
                    self._do_weather()
                    self.weather_due = time.time() + 30 * 60
                if now >= self.update_due:
                    self.update_due = time.time() + 12 * 3600
                    if self.cfg_getter().get("check_update", True):
                        try:
                            tag, url = fetch_latest_release()
                            self.q.put(("update", tag, url))
                        except Exception:
                            # 离线 / 限流 / 被墙都静默跳过，不打扰用户
                            pass
                if self.evt_platforms.is_set() or now >= self.platforms_due:
                    self.evt_platforms.clear()
                    self._do_platforms()
                    cfg = self.cfg_getter()
                    interval = min(1440, max(1, int(cfg.get("refresh_minutes", 10)))) * 60
                    self.platforms_due = time.time() + interval
            except Exception as e:
                log_exception(f"刷新线程异常: {e}")
            self.stop_event.wait(10)

    def _do_weather(self):
        cfg = self.cfg_getter()
        city = (cfg.get("city") or "").strip()
        if not city:
            self.q.put(("weather", {"error": "未配置城市"}))
            return
        try:
            self.q.put(("weather", fetch_weather(city)))
        except Exception as e:
            self.q.put(("weather", {"error": self._err(e)}))

    def _do_platforms(self):
        cfg = self.cfg_getter()
        for p in cfg.get("profiles", []):
            ptype, pid = p.get("type"), p.get("id")
            if ptype == "workbuddy":
                try:
                    self.q.put(("prof", pid, "balance", fetch_workbuddy()))
                except Exception as e:
                    self.q.put(("prof", pid, "error_b", self._err(e)))
                continue
            if ptype == "volc":
                ak = (p.get("ak") or "").strip()
                sk = (p.get("sk") or "").strip()
                if not ak or not sk:
                    self.q.put(("prof", pid, "error_b", "未填写 AK/SK"))
                    continue
                pre = (_secret_problem(ak, "AK", ark_check=True)
                       or _secret_problem(sk, "SK", ark_check=True))
                if not pre and len(ak) < 20:
                    pre = "AK格式有误，应为AKLT开头"
                if pre:
                    self.q.put(("prof", pid, "error_b", pre))
                    continue
                try:
                    self.q.put(("prof", pid, "balance", fetch_volc_balance(ak, sk)))
                except Exception as e:
                    self.q.put(("prof", pid, "error_b", self._err(e)))
                try:
                    self.q.put(("prof", pid, "usage", fetch_ark_usage(ak, sk)))
                except Exception as e:
                    self.q.put(("prof", pid, "error_u", self._err(e)))
                continue
            plat = PLATFORMS.get(ptype)
            fetcher = plat.get("fetch") if plat else None
            if not fetcher:
                self.q.put(("prof", pid, "error_b", f"暂不支持的平台: {ptype}"))
                continue
            key = (p.get("api_key") or "").strip()
            if not key:
                self.q.put(("prof", pid, "error_b", "未填写 API Key"))
                continue
            pre = _secret_problem(key, "API Key")
            if pre:
                self.q.put(("prof", pid, "error_b", pre))
                continue
            try:
                self.q.put(("prof", pid, "balance", fetcher(key)))
            except Exception as e:
                self.q.put(("prof", pid, "error_b", self._err(e)))
        self.q.put(("updated_at", now_cst().strftime("%H:%M:%S")))

    @staticmethod
    def _err(e):
        s = str(e)
        low = s.lower()
        if "timed out" in low:
            return "网络超时"
        if "401" in s:
            return "鉴权失败(401)，检查密钥"
        if "403" in s:
            return "无权限(403)，检查授权"
        if "未找到城市" in s:
            return s
        return s[:50] if s else "请求失败"


# ----------------------------------------------------------------------------
# 全局热键（RegisterHotKey + 窗口过程子类化）
# ----------------------------------------------------------------------------
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
_HK_MODS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
            "shift": MOD_SHIFT, "win": MOD_WIN}
WNDPROCTYPE = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.wintypes.HWND, ctypes.wintypes.UINT,
    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)


def parse_hotkey(spec):
    """ctrl+alt+s -> (modifiers, vk)；无效返回 (0, None)。"""
    if not spec:
        return 0, None
    mods, vk = 0, None
    for part in str(spec).split("+"):
        q = part.strip().lower()
        if not q:
            continue
        if q in _HK_MODS:
            mods |= _HK_MODS[q]
        elif len(q) == 1 and q.isalnum():
            vk = ord(q.upper())
        else:
            m = re.fullmatch(r"f([1-9]|1[0-2])", q)
            if m:
                vk = 0x70 + int(m.group(1)) - 1
    if vk is None or mods == 0:
        return 0, None
    return mods, vk


def read_hotkey_event(event):
    """从 tk 按键事件生成规范热键字符串；不满足 修饰键+主键 时返回 None。"""
    st = event.state or 0
    mods = []
    if st & 0x4:
        mods.append("ctrl")
    if st & 0x8:
        mods.append("alt")
    if st & 0x1:
        mods.append("shift")
    ks = (event.keysym or "").lower()
    if ks in ("control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
              "win_l", "win_r", "meta_l", "meta_r", "caps_lock", "space"):
        return None
    main = None
    if len(ks) == 1 and ks.isalnum():
        main = ks
    elif re.fullmatch(r"f([1-9]|1[0-2])", ks):
        main = ks
    if not mods or main is None:
        return None
    return "+".join(mods + [main])


class HotkeyManager:
    def __init__(self, hwnd, out_queue):
        self.u = ctypes.windll.user32
        self.hwnd = hwnd
        self.q = out_queue
        self.regs = {}            # id -> (name, spec)
        self.next_id = 9000
        self.old_proc = 0
        self.proc = WNDPROCTYPE(self._wndproc)   # 必须保持引用
        # 64 位下必须显式声明原型，否则指针按 32 位截断导致访问冲突
        self.u.CallWindowProcW.restype = ctypes.c_ssize_t
        self.u.CallWindowProcW.argtypes = [
            ctypes.c_void_p, ctypes.wintypes.HWND, ctypes.wintypes.UINT,
            ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        self.u.RegisterHotKey.restype = ctypes.wintypes.BOOL
        self.u.RegisterHotKey.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int,
            ctypes.wintypes.UINT, ctypes.wintypes.UINT]
        self.u.UnregisterHotKey.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        self._set_long = getattr(self.u, "SetWindowLongPtrW", None)
        if self._set_long is None:
            self._set_long = self.u.SetWindowLongW
        self._set_long.restype = ctypes.c_void_p
        self._set_long.argtypes = [
            ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_void_p]

    def attach(self):
        self.old_proc = self._set_long(
            self.hwnd, -4, ctypes.cast(self.proc, ctypes.c_void_p))

    def register(self, name, spec):
        self.unregister(name)
        spec = (spec or "").strip().lower()
        if not spec:
            return ""
        mods, vk = parse_hotkey(spec)
        if vk is None:
            return f"快捷键格式无效：{fmt_hk(spec)}"
        hid = self.next_id
        self.next_id += 1
        if not self.u.RegisterHotKey(self.hwnd, hid, MOD_NOREPEAT | mods, vk):
            return f"快捷键 {fmt_hk(spec)} 已被其他程序占用或无效"
        self.regs[hid] = (name, spec)
        return ""

    def unregister(self, name):
        for hid, v in list(self.regs.items()):
            if v[0] == name:
                self.u.UnregisterHotKey(self.hwnd, hid)
                del self.regs[hid]

    def detach(self):
        for hid in list(self.regs):
            self.u.UnregisterHotKey(self.hwnd, hid)
        self.regs.clear()
        if self.old_proc:
            self._set_long(self.hwnd, -4, self.old_proc)
        self.old_proc = 0

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_HOTKEY and wparam in self.regs:
            self.q.put(("hotkey", self.regs[wparam][0]))
            return 0
        return self.u.CallWindowProcW(self.old_proc, hwnd, msg, wparam, lparam)


# ----------------------------------------------------------------------------
# Canvas 缩放包装
# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# 自绘扁平控件
#   Tk 自带控件的系统外观（OptionMenu 的 3D 边框、默认复选框、带箭头滚动条、
#   方块按钮）放在深色界面里很像二十年前的软件，这里统一换成自绘控件。
# ----------------------------------------------------------------------------
def rr_points(x1, y1, x2, y2, r):
    """圆角矩形顶点（配合 smooth=True 使用）。"""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def setup_ttk_style():
    """让 ttk 滚动条变成贴合深色主题的细条（默认样式带箭头，太旧）。"""
    try:
        st = ttk.Style()
        st.theme_use("clam")
        st.configure("Ds.Vertical.TScrollbar", gripcount=0,
                     background="#3A4258", darkcolor="#2A3040",
                     lightcolor="#2A3040", troughcolor="#1E2230",
                     bordercolor="#1E2230", relief="flat", width=9)
        st.map("Ds.Vertical.TScrollbar",
               background=[("active", "#5B8DEF"), ("pressed", "#4A7DE0")])
    except Exception:
        pass


def section_title(parent, title, subtitle="", bg="#1E2230"):
    """区块标题 + 细分隔线（替代带 3D 边框的 LabelFrame）。"""
    head = tk.Frame(parent, bg=bg)
    head.pack(fill="x", padx=14, pady=(12, 4))
    tk.Label(head, text=title, bg=bg, fg="#EDF0F6",
             font=(UI_FONT, 10, "bold")).pack(side="left")
    if subtitle:
        tk.Label(head, text=subtitle, bg=bg, fg="#6B7488",
                 font=(UI_FONT, 8)).pack(side="left", padx=(8, 0))
    tk.Frame(parent, bg="#2A3040", height=1).pack(fill="x", padx=14, pady=(0, 8))
    box = tk.Frame(parent, bg=bg)
    box.pack(fill="x")
    return box


class FlatButton(tk.Canvas):
    """圆角扁平按钮（替代 tk.Button，去掉直角与系统边框）。"""

    KINDS = {
        "primary": ("#5B8DEF", "#6E9BF2", "#FFFFFF"),
        "secondary": ("#2A3040", "#3A4258", "#EDF0F6"),
        "accent": ("#2E4A63", "#3A5C7C", "#BFE0FF"),
        "danger": ("#5A2E36", "#6E3A44", "#FFB3B3"),
        "warn": ("#5A3A1E", "#6E4A28", "#FFC98A"),
        "ghost": ("#1E2230", "#2A3040", "#9AA3B8"),
    }

    def __init__(self, parent, text, command=None, kind="secondary",
                 width=96, height=30, radius=8, size=9, bg=None):
        parent_bg = bg or parent.cget("bg")
        super().__init__(parent, width=width, height=height, bg=parent_bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._base, self._hover, self._fg = self.KINDS.get(
            kind, self.KINDS["secondary"])
        self._cmd = command
        self._shape = self.create_polygon(
            rr_points(1, 1, width - 1, height - 1, radius),
            smooth=True, splinesteps=20, fill=self._base, outline="")
        self.create_text(width // 2, height // 2, text=text, fill=self._fg,
                         font=(UI_FONT, size))
        self.bind("<Enter>", lambda e: self.itemconfig(self._shape, fill=self._hover))
        self.bind("<Leave>", lambda e: self.itemconfig(self._shape, fill=self._base))
        self.bind("<Button-1>", lambda e: self._cmd() if self._cmd else None)


class PillSwitch(tk.Canvas):
    """胶囊开关，替代 tk.Checkbutton（绑定一个 IntVar）。"""

    def __init__(self, parent, text, variable, width=430, height=26,
                 bg="#1E2230", fg="#9AA3B8", on="#5B8DEF", off="#3A4258"):
        super().__init__(parent, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.var = variable
        self._on, self._off = on, off
        self._tw, self._th, self._h = 34, 18, height
        y0 = (height - self._th) // 2
        self._track = self.create_polygon(
            rr_points(2, y0, 2 + self._tw, y0 + self._th, self._th / 2),
            smooth=True, splinesteps=20, fill=off, outline="")
        self._knob = self.create_oval(0, 0, 0, 0, fill="#FFFFFF", outline="")
        self.create_text(self._tw + 12, height // 2, text=text, anchor="w",
                         fill=fg, font=(UI_FONT, 9))
        self.bind("<Button-1>", self._toggle)
        self._sync()

    def _sync(self):
        on = bool(int(self.var.get() or 0))
        d = self._th - 4
        y0 = (self._h - self._th) // 2
        x = (self._tw - d) if on else 4
        self.coords(self._knob, x, y0 + 2, x + d, y0 + 2 + d)
        self.itemconfig(self._track, fill=self._on if on else self._off)

    def _toggle(self, _e=None):
        self.var.set(0 if int(self.var.get() or 0) else 1)
        self._sync()


class FlatSelect(tk.Frame):
    """扁平下拉，替代 tk.OptionMenu。"""

    def __init__(self, parent, variable, choices, labels=None, width=210,
                 height=28, bg="#1E2230"):
        super().__init__(parent, bg=bg)
        self.var = variable
        self.choices = list(choices)
        self.labels = list(labels) if labels else list(choices)
        self.cv = tk.Canvas(self, width=width, height=height, bg=bg,
                            highlightthickness=0, bd=0, cursor="hand2")
        self.cv.pack()
        self.cv.create_polygon(rr_points(1, 1, width - 1, height - 1, 8),
                               smooth=True, splinesteps=20, fill="#2A3040",
                               outline="#3A4258", width=1)
        self._text = self.cv.create_text(12, height // 2, text="", anchor="w",
                                         fill="#EDF0F6", font=(UI_FONT, 9))
        self.cv.create_text(width - 12, height // 2, text="\u25be", anchor="e",
                            fill="#9AA3B8", font=(UI_FONT, 9))
        self.cv.bind("<Button-1>", self._popup)
        try:
            self.var.trace_add("write", lambda *a: self._sync())
        except Exception:
            pass
        self._sync()

    def _sync(self):
        val = self.var.get()
        text = self.labels[self.choices.index(val)] if val in self.choices else str(val)
        self.cv.itemconfig(self._text, text=text)

    def _popup(self, _e=None):
        m = tk.Menu(self, tearoff=0, bg="#2A3040", fg="#EDF0F6",
                    activebackground="#5B8DEF", activeforeground="#FFFFFF",
                    font=(UI_FONT, 9), bd=0)
        for c, lb in zip(self.choices, self.labels):
            m.add_command(label=lb, command=lambda c=c: self.var.set(c))
        m.tk_popup(self.cv.winfo_rootx(),
                   self.cv.winfo_rooty() + self.cv.winfo_height())


class CV:
    def __init__(self, canvas, scale):
        self.c = canvas
        self.s = scale

    def px(self, v):
        return int(v * self.s)

    def text(self, x, y, text, size=10, color=None, bold=False, family=UI_FONT,
             anchor="nw", tags=None):
        f = (family, max(1, int(size * self.s)), "bold" if bold else "normal")
        kw = dict(fill=color, font=f, anchor=anchor)
        if tags:
            kw["tags"] = tags
        return self.c.create_text(self.px(x), self.px(y), text=text, **kw)

    def rect(self, x1, y1, x2, y2, tags=None, **kw):
        if tags:
            kw["tags"] = tags
        return self.c.create_rectangle(
            *[int(v * self.s) for v in (x1, y1, x2, y2)], **kw)

    def oval(self, x1, y1, x2, y2, tags=None, **kw):
        if tags:
            kw["tags"] = tags
        return self.c.create_oval(
            *[int(v * self.s) for v in (x1, y1, x2, y2)], **kw)

    def arc(self, x1, y1, x2, y2, tags=None, **kw):
        if tags:
            kw["tags"] = tags
        return self.c.create_arc(
            *[int(v * self.s) for v in (x1, y1, x2, y2)], **kw)

    def line(self, *coords, tags=None, **kw):
        if tags:
            kw["tags"] = tags
        return self.c.create_line(*[int(v * self.s) for v in coords], **kw)

    def polygon(self, pts, tags=None, **kw):
        if tags:
            kw["tags"] = tags
        return self.c.create_polygon(*[int(v * self.s) for v in pts], **kw)

    def round_rect(self, x1, y1, x2, y2, r, tags=None, **kw):
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        if tags:
            kw["tags"] = tags
        return self.c.create_polygon(
            *[int(v * self.s) for v in pts], smooth=True, splinesteps=20, **kw)


def draw_weather_icon(cv, cx, cy, kind, t, k=1.0):
    sun_c, cloud_c = t["sun"], t["cloud"]
    wdt = max(1, int(2 * cv.s * k))

    def sun(x, y, r=13):
        r *= k
        for a in range(0, 360, 45):
            rad = math.radians(a)
            cv.line(x + math.cos(rad) * (r + 3 * k), y + math.sin(rad) * (r + 3 * k),
                    x + math.cos(rad) * (r + 8 * k), y + math.sin(rad) * (r + 8 * k),
                    fill=sun_c, width=wdt, capstyle="round")
        cv.oval(x - r, y - r, x + r, y + r, fill=sun_c, outline="")

    def cloud(x, y, scale=1.0, color=cloud_c):
        sc = scale * k
        cv.oval(x - 16 * sc, y - 4 * sc, x - 2 * sc, y + 10 * sc,
                fill=color, outline="")
        cv.oval(x - 8 * sc, y - 12 * sc, x + 8 * sc, y + 6 * sc,
                fill=color, outline="")
        cv.oval(x + 2 * sc, y - 8 * sc, x + 16 * sc, y + 8 * sc,
                fill=color, outline="")
        cv.rect(x - 16 * sc, y - 2 * sc, x + 16 * sc, y + 10 * sc,
                fill=color, outline="")

    if kind == "sun":
        sun(cx, cy)
    elif kind == "partly":
        sun(cx - 9 * k, cy - 9 * k, r=10)
        cloud(cx + 5 * k, cy + 7 * k, 0.9)
    elif kind == "cloud":
        cloud(cx, cy)
    elif kind == "overcast":
        cloud(cx - 4 * k, cy - 5 * k, 0.85, t["text3"])
        cloud(cx + 5 * k, cy + 5 * k, 0.95)
    elif kind == "fog":
        cloud(cx, cy - 6 * k, 0.9, t["text3"])
        for i, yy in enumerate((6, 12, 18)):
            wline = (26 - i * 6) * k
            cv.line(cx - wline / 2, cy + yy * k, cx + wline / 2, cy + yy * k,
                    fill=t["text2"], width=wdt, capstyle="round")
    elif kind == "rain":
        cloud(cx, cy - 8 * k)
        for dx in (-10, 0, 10):
            cv.line(cx + dx * k, cy + 6 * k, cx + dx * k - 3 * k, cy + 15 * k,
                    fill=t["rain"], width=wdt, capstyle="round")
    elif kind == "snow":
        cloud(cx, cy - 8 * k)
        for dx in (-10, 0, 10):
            cv.oval(cx + dx * k - 2 * k, cy + 8 * k,
                    cx + dx * k + 2 * k, cy + 12 * k, fill=t["snow"], outline="")
    elif kind == "storm":
        cloud(cx, cy - 8 * k, color=t["text3"])
        xs = [-2, -7, -2, -5, 6, 1, 4]
        ys = [5, 14, 14, 22, 11, 11, 5]
        bolt = []
        for xx, yy in zip(xs, ys):
            bolt += [cx + xx * k, cy + yy * k]
        cv.polygon(bolt, fill=sun_c, outline="")
    else:
        cloud(cx, cy)


# ----------------------------------------------------------------------------
# 添加 / 编辑账户对话框
# ----------------------------------------------------------------------------
# 建议在平台侧给 Key 起的名字（ASCII，任何平台的名称栏都吃得下）
PLATFORM_KEY_NAME = {
    "deepseek": "DeepSeek", "volc": "Volc", "siliconflow": "SiliconFlow",
    "moonshot": "Kimi", "zhipu": "Zhipu", "openrouter": "OpenRouter",
}


def suggest_key_name(ptype):
    """建议的 Key 名称，例如 DeskPulse-DeepSeek-202610。

    带上工具名和年月，以后在平台后台一眼就能认出这是给谁的；
    要停用或吊销时也不会误删别处正在用的那把。
    """
    short = PLATFORM_KEY_NAME.get(ptype)
    if not short:
        short = re.sub(r"[^A-Za-z0-9]+", "", str(ptype or "")) or "AI"
    return f"DeskPulse-{short}-{now_cst().strftime('%Y%m')}"


def fmt_hist_time(ts):
    try:
        return dt.datetime.fromtimestamp(float(ts), CST).strftime("%m-%d %H:%M")
    except Exception:
        return "--"


def history_stats(series):
    """一组 (时间, 余额) 的统计：当前 / 区间 / 变化 / 近 24 小时消耗速率。"""
    if not series:
        return {}
    vals = [v for _t, v in series]
    t0, t1 = series[0][0], series[-1][0]
    st = {"n": len(series), "cur": vals[-1], "hi": max(vals), "lo": min(vals),
          "delta": vals[-1] - vals[0], "t0": t0, "t1": t1}
    recent = [(t, v) for t, v in series if t >= t1 - 86400]
    if len(recent) >= 2 and recent[0][1] > recent[-1][1]:
        hours = max(0.5, (recent[-1][0] - recent[0][0]) / 3600)
        rate = (recent[0][1] - recent[-1][1]) / hours
        st["per_day"] = rate * 24
        st["days_left"] = (vals[-1] / rate) / 24 if rate > 0 else None
    return st


def history_svg(series, title="", width=900, height=460):
    """把余额曲线画成 SVG（纯文本，不依赖任何绘图库）。"""
    pad_l, pad_r, pad_t, pad_b = 96, 28, 34, 46
    vals = [v for _t, v in series]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        hi, lo = hi + 1.0, lo - 1.0
    n = len(series)
    pw = width - pad_l - pad_r
    ph = height - pad_t - pad_b
    pts = []
    for i, (_t, v) in enumerate(series):
        x = pad_l + pw * (i / (n - 1) if n > 1 else 0.5)
        y = pad_t + ph * (1 - (v - lo) / (hi - lo))
        pts.append((x, y))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}">',
           f'<rect width="{width}" height="{height}" fill="#171B26"/>']
    if title:
        out.append(f'<text x="{pad_l}" y="24" fill="#EDF0F6" '
                   f'font-family="Microsoft YaHei" font-size="16">{title}</text>')
    for i in range(5):
        y = pad_t + ph * i / 4
        v = hi - (hi - lo) * i / 4
        out.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                   f'y2="{y:.1f}" stroke="#232838" stroke-width="1"/>')
        out.append(f'<text x="{pad_l - 10}" y="{y + 4:.1f}" fill="#6B7488" '
                   f'font-family="Microsoft YaHei" font-size="12" '
                   f'text-anchor="end">{fmt_money(v)}</text>')
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    out.append(f'<polyline points="{line}" fill="none" stroke="#5B8DEF" '
               f'stroke-width="2.5"/>')
    lx, ly = pts[-1]
    out.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" fill="#5B8DEF"/>')
    out.append(f'<text x="{lx - 8:.1f}" y="{ly - 12:.1f}" fill="#EDF0F6" '
               f'font-family="Microsoft YaHei" font-size="13" '
               f'text-anchor="end">{fmt_money(vals[-1])}</text>')
    out.append(f'<text x="{pad_l}" y="{height - 16}" fill="#6B7488" '
               f'font-family="Microsoft YaHei" font-size="12">{fmt_hist_time(series[0][0])}</text>')
    out.append(f'<text x="{width - pad_r}" y="{height - 16}" fill="#6B7488" '
               f'font-family="Microsoft YaHei" font-size="12" '
               f'text-anchor="end">{fmt_hist_time(series[-1][0])}</text>')
    out.append("</svg>")
    return "\n".join(out)


class HistoryWindow(tk.Toplevel):
    """余额历史：点开才看，看完可关；可导出矢量图 / 表格 / 报告。

    曲线只在需要时打开这个窗口看，不占用卡片版面。
    """

    def __init__(self, parent, cfg, cache, on_close=None):
        super().__init__(parent)
        setup_ttk_style()
        self.cfg, self.cache = cfg, cache
        self.BG = "#1E2230"
        self.configure(bg=self.BG)
        self.title("DeskPulse · 余额历史")
        self.resizable(False, False)
        self.transient(parent)
        self.pids = [p for p in cfg.get("profiles", []) if self._hist(p.get("id"))]
        self.cur = self.pids[0]["id"] if self.pids else None

        head = tk.Frame(self, bg=self.BG)
        head.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(head, text="余额历史", bg=self.BG, fg="#EDF0F6",
                 font=(UI_FONT, 11, "bold")).pack(side="left")
        if self.pids:
            names = [p.get("name") or platform_label(p.get("type"))
                     for p in self.pids]
            self.sel = FlatSelect(head, tk.StringVar(value=self.cur),
                                  [p["id"] for p in self.pids], names,
                                  width=168, height=26, bg=self.BG)
            self.sel.pack(side="right")
            self.sel.var.trace_add("write", lambda *a: self._redraw())
        self.cv = tk.Canvas(self, width=520, height=232, bg="#171B26",
                            highlightthickness=0, bd=0)
        self.cv.pack(padx=16, pady=(6, 6))
        self.stat = tk.Label(self, text="", bg=self.BG, fg="#9AA3B8",
                             font=(UI_FONT, 9), justify="left", anchor="w",
                             wraplength=510)
        self.stat.pack(fill="x", padx=16)
        btns = tk.Frame(self, bg=self.BG)
        btns.pack(pady=12)
        for text, cmd, kind in (("导出矢量图", self._export_svg, "secondary"),
                                ("导出表格", self._export_csv, "secondary"),
                                ("导出报告", self._export_html, "accent"),
                                ("关闭", self._close, "primary")):
            FlatButton(btns, text, cmd, kind=kind, width=100, height=31,
                       size=9, bg=self.BG).pack(side="left", padx=5)
        self._redraw()
        self.update_idletasks()
        try:
            clamp_window_pos(self, parent.winfo_rootx() + 60,
                             parent.winfo_rooty() + 40)
            keep_on_screen(self)
        except Exception:
            pass
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ---------- 数据 ----------
    def _hist(self, pid):
        h = (self.cache.get(pid) or {}).get("hist")
        if not isinstance(h, list):
            return []
        out = []
        for item in h:
            try:
                out.append((float(item[0]), float(item[1])))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    def _series(self):
        return self._hist(self.cur) if self.cur else []

    def _cur_name(self):
        for p in self.pids:
            if p["id"] == self.cur:
                return p.get("name") or platform_label(p.get("type"))
        return ""

    def _close(self):
        try:
            if self.on_close_cb if False else None:
                pass
        except Exception:
            pass
        self.destroy()

    # ---------- 绘制 ----------
    def _redraw(self):
        cv = self.cv
        cv.delete("all")
        s = self._series()
        if len(s) < 2:
            cv.create_text(260, 116, text="数据点还不够（至少需要两次刷新）",
                           fill="#6B7488", font=(UI_FONT, 10))
            self.stat.config(text="程序每次刷新余额时会记一个点，多跑一会儿再回来看。")
            return
        w, h = 520, 232
        pl, pr, pt, pb = 62, 18, 14, 28
        vals = [v for _t, v in s]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            hi, lo = hi + 1.0, lo - 1.0
        n = len(s)
        pts = []
        for i, (_t, v) in enumerate(s):
            x = pl + (w - pl - pr) * (i / (n - 1) if n > 1 else 0.5)
            y = pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo))
            pts.append((x, y))
        for i in range(5):
            y = pt + (h - pt - pb) * i / 4
            v = hi - (hi - lo) * i / 4
            cv.create_line(pl, y, w - pr, y, fill="#232838")
            cv.create_text(pl - 8, y, text=fmt_money(v), anchor="e",
                           fill="#6B7488", font=(UI_FONT, 8))
        cv.create_line(*[c for p in pts for c in p], fill="#5B8DEF", width=2)
        x, y = pts[-1]
        cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill="#5B8DEF", outline="")
        cv.create_text(x - 8, y - 14, text=fmt_money(s[-1][1]), anchor="e",
                       fill="#EDF0F6", font=(UI_FONT, 9))
        cv.create_text(pl, h - 12, text=fmt_hist_time(s[0][0]), anchor="w",
                       fill="#6B7488", font=(UI_FONT, 8))
        cv.create_text(w - pr, h - 12, text=fmt_hist_time(s[-1][0]), anchor="e",
                       fill="#6B7488", font=(UI_FONT, 8))
        st = history_stats(s)
        parts = [f"当前 {fmt_money(st['cur'])}",
                 f"区间 {fmt_money(st['lo'])} ~ {fmt_money(st['hi'])}",
                 f"{st['n']} 个点"]
        if st.get("per_day"):
            parts.append(f"近 24 小时约 {fmt_money(st['per_day'])}/天")
        if st.get("days_left"):
            parts.append(f"按此速度还能用 {st['days_left']:.1f} 天")
        self.stat.config(text=" ｜ ".join(parts))

    # ---------- 导出 ----------
    def _ask(self, ext, desc):
        if not self.cur:
            messagebox.showinfo("提示", "还没有历史数据", parent=self)
            return None
        today = now_cst().strftime("%Y%m%d")
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", self._cur_name())[:24]
        return filedialog.asksaveasfilename(
            parent=self, defaultextension=ext, filetypes=[(desc, "*" + ext)],
            initialfile=f"DeskPulse-余额历史-{safe}-{today}{ext}")

    def _export_svg(self):
        path = self._ask(".svg", "矢量图")
        if not path:
            return
        s = self._series()
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(history_svg(s, f"{self._cur_name()} 余额历史"))
            messagebox.showinfo("导出完成", f"已保存：\n{path}", parent=self)
        except Exception as e:
            messagebox.showwarning("导出失败", str(e)[:200], parent=self)

    def _export_csv(self):
        path = self._ask(".csv", "表格")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write("账户,时间,余额\n")
                for t, v in self._series():
                    f.write(f"{self._cur_name()},{fmt_hist_time(t)},{v:.4f}\n")
            messagebox.showinfo("导出完成", f"已保存：\n{path}", parent=self)
        except Exception as e:
            messagebox.showwarning("导出失败", str(e)[:200], parent=self)

    def _export_html(self):
        path = self._ask(".html", "报告")
        if not path:
            return
        s = self._series()
        st = history_stats(s)
        rows = "".join(f"<tr><td>{fmt_hist_time(t)}</td><td>{v:.4f}</td></tr>"
                       for t, v in reversed(s))
        html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>DeskPulse 余额历史 · {self._cur_name()}</title>
<style>
body{{background:#12151d;color:#EDF0F6;font-family:"Microsoft YaHei",sans-serif;
     margin:0;padding:28px}}
h1{{font-size:20px;font-weight:600;margin:0 0 4px}}
.sub{{color:#9AA3B8;font-size:13px;margin-bottom:18px}}
.card{{background:#1E2230;border-radius:12px;padding:16px;margin-bottom:16px}}
table{{border-collapse:collapse;font-size:13px;width:100%}}
td,th{{padding:6px 10px;border-bottom:1px solid #2A3040;text-align:left}}
th{{color:#9AA3B8;font-weight:500}}
.kv{{display:inline-block;margin-right:22px;color:#9AA3B8;font-size:13px}}
.kv b{{color:#EDF0F6;font-weight:600}}
</style></head><body>
<h1>{self._cur_name()} · 余额历史</h1>
<div class="sub">导出时间 {now_cst().strftime('%Y-%m-%d %H:%M')} · 数据来自 DeskPulse 本地记录</div>
<div class="card">{history_svg(s, '')}</div>
<div class="card">
  <span class="kv">当前余额 <b>{fmt_money(st.get('cur', 0))}</b></span>
  <span class="kv">区间最低 <b>{fmt_money(st.get('lo', 0))}</b></span>
  <span class="kv">区间最高 <b>{fmt_money(st.get('hi', 0))}</b></span>
  <span class="kv">数据点 <b>{st.get('n', 0)}</b></span>
  {f"<span class='kv'>近 24 小时 <b>{fmt_money(st.get('per_day', 0))}/天</b></span>" if st.get('per_day') else ""}
</div>
<div class="card"><table><tr><th>时间</th><th>余额</th></tr>{rows}</table></div>
</body></html>"""
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            messagebox.showinfo("导出完成", f"已保存：\n{path}", parent=self)
        except Exception as e:
            messagebox.showwarning("导出失败", str(e)[:200], parent=self)


class AccountDialog(tk.Toplevel):
    def __init__(self, parent, ptype, on_save, initial=None, default_name=None):
        super().__init__(parent)
        self.ptype = ptype
        self.plat = PLATFORMS.get(ptype, PLATFORMS["deepseek"])
        self.on_save = on_save
        self.initial = initial
        self.default_name = default_name
        self.title("编辑账户" if initial else f"添加 {self.plat['label']} 账户")
        self.resizable(False, False)
        self.transient(parent)
        self.configure(bg="#1E2230")
        row = [0]
        BG, FG, F2 = "#1E2230", "#EDF0F6", "#9AA3B8"

        def head(label):
            tk.Label(self, text=label, bg=BG, fg=FG,
                     font=(UI_FONT, 10, "bold")).grid(
                row=row[0], column=0, columnspan=2, sticky="w", padx=16,
                pady=(14 if row[0] == 0 else 10, 2))
            row[0] += 1

        def field(label, secret=False):
            tk.Label(self, text=label, bg=BG, fg=F2, font=(UI_FONT, 9)).grid(
                row=row[0], column=0, sticky="w", padx=(16, 8), pady=3)
            e = tk.Entry(self, width=34, font=(UI_FONT, 10),
                         show="*" if secret else "", bg="#2A3040", fg=FG,
                         insertbackground=FG, relief="flat", highlightthickness=1,
                         highlightbackground="#3A4258", highlightcolor="#5B8DEF")
            e.grid(row=row[0], column=1, padx=(0, 16), pady=3, ipady=3)
            row[0] += 1
            return e

        head(self.plat["label"])
        self.e_name = field("显示名称")
        if (initial or {}).get("name"):
            self.e_name.insert(0, initial["name"])
        elif default_name:
            self.e_name.insert(0, default_name)
        self.entries = {}
        for key, label, secret in self.plat["fields"]:
            self.entries[key] = field(label, secret=secret)
        # 新增账户时若环境变量里有对应密钥，直接预填，省一次手输
        env_prefilled = []
        if not initial:
            for key, _label, _secret in self.plat["fields"]:
                val = env_key_for(ptype, key)
                if val:
                    self.entries[key].insert(0, val)
                    env_prefilled.append(key)
        if ptype == "volc":
            bad_initial = initial and (
                _secret_problem(initial.get("ak", ""), "AK")
                or _secret_problem(initial.get("sk", ""), "SK"))
            if bad_initial:
                hint = "当前密钥有误（含中文或为ark-开头），请重新填写"
            elif initial:
                hint = "留空表示不修改原密钥"
            elif env_prefilled:
                hint = "已从环境变量预填，直接保存即可"
            else:
                hint = self.plat["hint"]
        elif env_prefilled:
            hint = "已从环境变量预填，直接保存即可"
        else:
            hint = "留空表示不修改原 Key" if initial else self.plat["hint"]
        link = tk.Label(self, text=hint, bg=BG, fg="#6B7488",
                        cursor="xterm" if initial else "hand2",
                        font=(UI_FONT, 8))
        link.grid(row=row[0], column=1, sticky="w", padx=16, pady=(0, 2))
        if not initial:
            link.bind("<Button-1>",
                      lambda e: open_url_async(self.plat["keys_url"]))
        row[0] += 1
        if not initial and self.plat.get("keys_path"):
            tk.Label(self, text=f"取 Key 路径：{self.plat['keys_path']}",
                     bg=BG, fg="#8FA0BF", font=(UI_FONT, 8), wraplength=420,
                     justify="left", anchor="w").grid(
                row=row[0], column=0, columnspan=2, sticky="w", padx=16,
                pady=(0, 2))
            row[0] += 1
        if not initial and self.plat.get("key_tip"):
            tk.Label(self, text="常见坑：" + self.plat["key_tip"],
                     bg=BG, fg="#C8A96A", font=(UI_FONT, 8), wraplength=420,
                     justify="left", anchor="w").grid(
                row=row[0], column=0, columnspan=2, sticky="w", padx=16,
                pady=(0, 2))
            row[0] += 1
        if not initial:
            kname = suggest_key_name(ptype)
            knf = tk.Frame(self, bg=BG)
            knf.grid(row=row[0], column=0, columnspan=2, sticky="w",
                     padx=16, pady=(0, 2))
            row[0] += 1
            tk.Label(knf, text="建 Key 时的名称建议：", bg=BG, fg="#8FA0BF",
                     font=(UI_FONT, 8)).pack(side="left")
            tk.Label(knf, text=kname, bg="#2A3040", fg="#EDF0F6",
                     font=(UI_FONT, 8), padx=7, pady=2).pack(side="left",
                                                              padx=(2, 6))
            copied = tk.Label(knf, text="", bg=BG, fg="#7FD8A6",
                              font=(UI_FONT, 8))

            def _copy_name(name=kname, tip=copied, holder=knf):
                try:
                    holder.clipboard_clear()
                    holder.clipboard_append(name)
                    tip.config(text="已复制，粘到平台的名称栏即可")
                except Exception:
                    tip.config(text="复制失败，请手动选中复制")

            FlatButton(knf, "复制", _copy_name, kind="accent", width=52,
                       height=24, size=8, bg=BG).pack(side="left")
            copied.pack(side="left", padx=6)
        if not initial:
            tk.Label(self,
                     text="建议：单独为 DeskPulse 建一把新 Key，不要复用你在别处用的那把，"
                          "并按上面的名字命名——将来想停用时只删这一把，其他工具不受影响。",
                     bg=BG, fg="#6B7488", font=(UI_FONT, 8), wraplength=420,
                     justify="left", anchor="w").grid(
                row=row[0], column=0, columnspan=2, sticky="w", padx=16,
                pady=(0, 2))
            row[0] += 1
        links = tk.Frame(self, bg=BG)
        links.grid(row=row[0], column=0, columnspan=2, sticky="w", padx=16,
                   pady=(2, 0))
        row[0] += 1
        for _text, _url in (("打开取 Key 页面", self.plat.get("keys_url")),
                            ("打开充值页面", self.plat.get("topup_url"))):
            if _url:
                _lb = tk.Label(links, text="· " + _text, bg=BG, fg="#5B8DEF",
                               font=(UI_FONT, 8), cursor="hand2")
                _lb.pack(side="left", padx=(0, 14))
                _lb.bind("<Button-1>", lambda e, u=_url: open_url_async(u))
        self.e_threshold = field("余额预警阈值(元，0=不提醒)")
        self.e_threshold.insert(0, str((initial or {}).get("threshold", 0)))

        btns = tk.Frame(self, bg=BG)
        btns.grid(row=row[0], column=0, columnspan=2, pady=14)
        FlatButton(btns, "保存并立即刷新", self._save, kind="primary",
                   width=140, height=33, size=10, bg=BG).pack(side="left", padx=6)
        FlatButton(btns, "取消", self.destroy, kind="secondary", width=90,
                   height=33, size=10, bg=BG).pack(side="left", padx=6)
        self.update_idletasks()
        _px, _py = parent.winfo_rootx() + 30, parent.winfo_rooty() + 50
        clamp_window_pos(self, _px, _py)
        keep_on_screen(self)
        reclamp_later(self, _px, _py)
        safe_grab(self)

    def destroy(self):
        # 子对话框销毁会清掉全局 grab，需把父设置窗的模态恢复回来
        parent = self.master
        super().destroy()
        try:
            if parent is not None and parent.winfo_exists():
                safe_grab(parent)
        except Exception:
            pass

    def _save(self):
        name = self.e_name.get().strip()
        try:
            threshold = max(0.0, float(self.e_threshold.get().strip() or "0"))
        except ValueError:
            messagebox.showwarning("提示", "预警阈值需为数字（元）", parent=self)
            return
        p = dict(self.initial) if self.initial else {}
        p["type"] = self.ptype
        p["name"] = (name or self.default_name or self.plat["label"])
        p["threshold"] = threshold
        if self.ptype == "volc":
            ak = self.entries["ak"].get().strip()
            sk = self.entries["sk"].get().strip()
            if not self.initial and (not ak or not sk):
                messagebox.showwarning("提示", "请填写 Access Key ID 和 Secret",
                                       parent=self)
                return
            for label, v in (("Access Key ID", ak), ("Secret Access Key", sk)):
                problem = _secret_problem(v, label, ark_check=True) if v else ""
                if problem:
                    messagebox.showwarning(
                        "提示",
                        problem + "。\n\n说明：ark- 开头的是方舟推理 API Key，不能查"
                                  "余额/用量；\n需要 IAM 访问密钥（AKLT 开头），在 "
                                  "console.volcengine.com/iam/keymanage/ 创建。",
                        parent=self)
                    return
            if ak and len(ak) < 20:
                messagebox.showwarning(
                    "提示",
                    "Access Key ID 长度明显不对，IAM 密钥通常以 AKLT 开头，\n"
                    "请在 console.volcengine.com/iam/keymanage/ 创建后复制。",
                    parent=self)
                return
            if ak:
                p["ak"] = ak
            if sk:
                p["sk"] = sk
        else:
            key = self.entries["api_key"].get().strip() if "api_key" in self.entries else ""
            if not self.initial and self.plat["fields"] and not key:
                messagebox.showwarning("提示", "请填写 API Key", parent=self)
                return
            problem = _secret_problem(key, "API Key") if key else ""
            if problem:
                messagebox.showwarning("提示", problem, parent=self)
                return
            if key:
                p["api_key"] = key
        if not self.initial:
            p["id"] = new_id(self.plat["id_prefix"])
        self.on_save(p)
        self.destroy()


# ----------------------------------------------------------------------------
# 设置对话框（账户的添加、编辑、删除、充值入口均在此）
# ----------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, cfg, on_save, on_profiles=None,
                 on_history=None):
        super().__init__(parent)
        self.title(f"DeskPulse 设置 · v{APP_VERSION}")
        setup_ttk_style()
        self.cfg = cfg
        self.on_save = on_save
        self.on_profiles = on_profiles  # 账户增删改即时生效回调
        self.on_history = on_history    # 「余额历史…」按钮回调
        self.resizable(False, False)
        self.transient(parent)
        self.configure(bg="#1E2230")
        self.profiles = [dict(p) for p in cfg.get("profiles", [])]

        BG, FG, F2 = "#1E2230", "#EDF0F6", "#9AA3B8"
        entry_kw = dict(font=(UI_FONT, 10), bg="#2A3040", fg=FG,
                        insertbackground=FG, relief="flat", highlightthickness=1,
                        highlightbackground="#3A4258", highlightcolor="#5B8DEF")

        basic = tk.Frame(self, bg=BG)
        basic.pack(fill="x", padx=14, pady=(14, 6))
        self.vars = {}

        def row(label, key, value):
            f = tk.Frame(basic, bg=BG)
            f.pack(fill="x", pady=3)
            tk.Label(f, text=label, bg=BG, fg=F2, font=(UI_FONT, 9),
                     width=18, anchor="w").pack(side="left")
            e = tk.Entry(f, width=22, **entry_kw)
            e.pack(side="left", ipady=3)
            e.insert(0, str(value))
            self.vars[key] = e

        row("城市", "city", cfg.get("city", ""))
        row("刷新间隔(分钟 1-1440)", "refresh_minutes", cfg.get("refresh_minutes", 10))
        row("不透明度(0.5-1.0)", "opacity", cfg.get("opacity", 0.96))

        opt = tk.Frame(basic, bg=BG)
        opt.pack(fill="x", pady=3)
        tk.Label(opt, text="界面缩放", bg=BG, fg=F2, font=(UI_FONT, 9),
                 width=18, anchor="w").pack(side="left")
        self.scale_var = tk.StringVar(
            value=f"{int(round(float(cfg.get('ui_scale', 1.0)) * 100))}%")
        FlatSelect(opt, self.scale_var,
                   ["85%", "100%", "110%", "125%", "140%", "160%"],
                   width=150, bg=BG).pack(side="left")

        opt2 = tk.Frame(basic, bg=BG)
        opt2.pack(fill="x", pady=3)
        tk.Label(opt2, text="配色主题", bg=BG, fg=F2, font=(UI_FONT, 9),
                 width=18, anchor="w").pack(side="left")
        self.theme_var = tk.StringVar(
            value=THEMES.get(cfg.get("theme"), THEMES["midnight"])["name"])
        FlatSelect(opt2, self.theme_var,
                   [v["name"] for v in THEMES.values()],
                   width=150, bg=BG).pack(side="left")

        checks = tk.Frame(self, bg=BG)
        checks.pack(fill="x", padx=14)
        self.top_var = tk.IntVar(value=1 if cfg.get("always_on_top") else 0)
        self.lock_var = tk.IntVar(value=1 if cfg.get("locked") else 0)
        self.sec_var = tk.IntVar(value=1 if cfg.get("show_seconds", False) else 0)
        self.start_var = tk.IntVar(
            value=1 if autostart_get() or cfg.get("autostart") else 0)
        for text, var in (("窗口始终置顶", self.top_var),
                          ("锁定界面（防误触，锁定后仅可解锁/退出）", self.lock_var),
                          ("时钟显示秒数", self.sec_var),
                          ("开机自动启动", self.start_var)):
            PillSwitch(checks, text, var, width=430, bg=BG,
                       fg=F2).pack(fill="x", pady=1)

        hkf = section_title(self, "全局快捷键", "任意界面下生效，清除即禁用",
                            bg=BG)
        self.hk_vars = {}
        for key, label in (("hotkey_settings", "打开设置"),
                           ("hotkey_quit", "关闭退出")):
            f = tk.Frame(hkf, bg=BG)
            f.pack(fill="x", padx=10, pady=3)
            tk.Label(f, text=label, bg=BG, fg=F2, font=(UI_FONT, 9),
                     width=18, anchor="w").pack(side="left")
            sv = tk.StringVar(value=fmt_hk(cfg.get(key, "")))
            e = tk.Entry(f, width=16, textvariable=sv, font=(UI_FONT, 9),
                         bg="#2A3040", fg=FG, relief="flat", highlightthickness=1,
                         highlightbackground="#3A4258", highlightcolor="#5B8DEF",
                         readonlybackground="#2A3040", state="readonly")
            e.pack(side="left", ipady=2)
            e.bind("<KeyPress>",
                   lambda ev, v=sv: self._capture_hotkey(ev, v))
            FlatButton(f, "清除", lambda v=sv: v.set(""), width=54,
                       height=24, size=8, bg=BG).pack(side="left", padx=6)
            self.hk_vars[key] = sv

        acct = section_title(self, "监控账户", "余额 / 用量 / 充值", bg=BG)
        addrow = tk.Frame(acct, bg=BG)
        addrow.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        acct_canvas = tk.Canvas(acct, bg=BG, highlightthickness=0,
                                height=232, bd=0)
        self.acct_canvas = acct_canvas
        self.acct_sb = ttk.Scrollbar(acct, orient="vertical",
                                     command=acct_canvas.yview,
                                     style="Ds.Vertical.TScrollbar")
        acct_sb = self.acct_sb
        self.acct_box = tk.Frame(acct_canvas, bg=BG)
        self.acct_win = acct_canvas.create_window(
            (0, 0), window=self.acct_box, anchor="nw")
        self.acct_box.bind(
            "<Configure>",
            lambda e: acct_canvas.configure(scrollregion=acct_canvas.bbox("all")))
        acct_canvas.configure(yscrollcommand=acct_sb.set)
        acct_canvas.bind(
            "<Configure>",
            lambda e: acct_canvas.itemconfig(self.acct_win, width=e.width - 4))

        def _acct_wheel(e):
            acct_canvas.yview_scroll(int(-e.delta / 120), "units")
        acct_canvas.bind("<Enter>",
                         lambda e: acct_canvas.bind_all("<MouseWheel>", _acct_wheel))
        acct_canvas.bind("<Leave>",
                         lambda e: acct_canvas.unbind_all("<MouseWheel>"))
        acct_canvas.pack(side="left", fill="x", expand=True,
                         padx=(8, 0), pady=(4, 2))
        acct_sb.pack(side="right", fill="y", pady=(4, 2), padx=(0, 4))
        self.threshold_vars = {}
        self._rebuild_acct_rows()
        # 平台按钮按 4 列换行排布：平台变多以后挤一行会被窗口裁掉
        for i, ptype in enumerate(PLATFORMS):
            FlatButton(addrow, f"+ {platform_label(ptype)}",
                       lambda t=ptype: AccountDialog(
                           self, t, self._add_profile,
                           default_name=self._suggest_name(t)),
                       kind="secondary", width=112, height=27, size=8,
                       bg=BG).grid(row=i // 4, column=i % 4, padx=3, pady=2,
                                   sticky="w")

        env_row = len(PLATFORMS) // 4 + 1
        FlatButton(addrow, "⤓ 从环境变量导入", self._import_env,
                   kind="accent", width=112, height=27, size=8,
                   bg=BG).grid(row=env_row, column=0, padx=3, pady=(6, 2),
                               sticky="w")
        if self.on_history:
            FlatButton(addrow, "余额历史 / 趋势…", self.on_history,
                       kind="secondary", width=112, height=27, size=8,
                       bg=BG).grid(row=env_row, column=1, padx=3, pady=(6, 2),
                                   sticky="w")
        self._env_status = tk.Label(addrow, text="", bg=BG, fg="#6B7488",
                                    font=(UI_FONT, 8), anchor="w")
        self._env_status.grid(row=env_row + 1, column=0, columnspan=4,
                              sticky="w", padx=2)

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=10)
        FlatButton(btns, "保存", self._save, kind="primary", width=110,
                   height=33, size=10, bg=BG).pack(side="left", padx=6)
        FlatButton(btns, "取消", self.destroy, kind="secondary", width=110,
                   height=33, size=10, bg=BG).pack(side="left", padx=6)
        self.update_idletasks()
        _px = parent.winfo_rootx() + 30
        _py = max(20, parent.winfo_rooty() - 40)
        clamp_window_pos(self, _px, _py)
        keep_on_screen(self)
        reclamp_later(self, _px, _py)
        safe_grab(self)

    def _import_env(self):
        """把环境变量里的密钥补进还没填的账户，并给出结果提示。"""
        filled = apply_env_keys({"profiles": self.profiles})
        self._rebuild_acct_rows()
        if filled:
            self._env_status.config(
                text=f"已从环境变量导入 {len(filled)} 项密钥，记得点保存",
                fg="#7FD8A6")
        else:
            self._env_status.config(
                text="没有可导入的项（环境变量里没有，或账户都已填写）",
                fg="#6B7488")

    def _suggest_name(self, ptype):
        n = sum(1 for p in self.profiles if p["type"] == ptype) + 1
        base = platform_label(ptype)
        return base if n == 1 else f"{base} {n}"

    @staticmethod
    def _capture_hotkey(event, var):
        spec = read_hotkey_event(event)
        if spec is not None:
            var.set(fmt_hk(spec))
        return "break"

    def _rebuild_acct_rows(self):
        for w in self.acct_box.winfo_children():
            w.destroy()
        self.threshold_vars = {}
        # 占位，方法末尾统一处理滚动条显隐
        if not self.profiles:
            tk.Label(self.acct_box, text="（暂无账户，点击下方按钮添加）",
                     bg="#1E2230", fg="#6B7488", font=(UI_FONT, 9)).pack(
                anchor="w", pady=2)
            return
        for p in self.profiles:
            f = tk.Frame(self.acct_box, bg="#1E2230")
            f.pack(fill="x", pady=2)
            tk.Label(f, text=str(p.get("name", "")), bg="#1E2230",
                     fg="#EDF0F6", font=(UI_FONT, 9), width=14,
                     anchor="w").pack(side="left")
            tk.Label(f, text="预警¥", bg="#1E2230", fg="#9AA3B8",
                     font=(UI_FONT, 8)).pack(side="left")
            sv = tk.StringVar(value=str(p.get("threshold", 0)))
            tk.Entry(f, width=5, textvariable=sv, font=(UI_FONT, 9),
                     bg="#2A3040", fg="#EDF0F6", insertbackground="#EDF0F6",
                     relief="flat", highlightthickness=1,
                     highlightbackground="#3A4258").pack(side="left", ipady=1, padx=2)
            pid = p.get("id") or f"tmp{len(self.threshold_vars)}"
            p.setdefault("id", pid)
            self.threshold_vars[pid] = sv
            topup = PLATFORMS.get(p.get("type"), {}).get("topup_url", DS_TOPUP_URL)
            for text, cmd, kind in (
                    ("充值", lambda u=topup: open_url_async(u), "warn"),
                    ("编辑", lambda pp=p: AccountDialog(
                        self, pp["type"],
                        lambda np: self._update_profile(pp, np), initial=pp),
                     "secondary"),
                    ("删除", lambda pp=p: self._del_profile(pp), "danger")):
                FlatButton(f, text, cmd, kind=kind, width=52, height=24,
                           size=8, bg="#1E2230").pack(side="left", padx=2)
        self.acct_box.update_idletasks()
        if hasattr(self, "acct_sb"):
            need = self.acct_box.winfo_reqheight()
            if need <= 236:
                self.acct_sb.pack_forget()
                # 账户少的时候把画布收短，避免下面留一大块空白
                try:
                    self.acct_canvas.configure(height=max(40, need + 6))
                except Exception:
                    pass
            else:
                try:
                    self.acct_canvas.configure(height=232)
                except Exception:
                    pass
                if not self.acct_sb.winfo_ismapped():
                    self.acct_sb.pack(side="right", fill="y",
                                      pady=(4, 2), padx=(0, 4))

    def _notify_profiles(self):
        if self.on_profiles:
            self.on_profiles([dict(x) for x in self.profiles])

    def _add_profile(self, p):
        if any(x["id"] == p["id"] for x in self.profiles):
            self._update_profile(next(x for x in self.profiles if x["id"] == p["id"]), p)
        else:
            self.profiles.append(p)
        self._rebuild_acct_rows()
        self._notify_profiles()

    def _update_profile(self, old, new):
        for i, x in enumerate(self.profiles):
            if x["id"] == old["id"]:
                self.profiles[i] = new
        self._rebuild_acct_rows()
        self._notify_profiles()

    def _del_profile(self, p):
        if messagebox.askyesno("确认", f"删除账户「{p.get('name')}」？", parent=self):
            self.profiles = [x for x in self.profiles if x["id"] != p["id"]]
            self._rebuild_acct_rows()
            self._notify_profiles()

    def _save(self):
        cfg = dict(self.cfg)
        cfg["city"] = self.vars["city"].get().strip() or "北京"
        try:
            cfg["refresh_minutes"] = min(
                1440, max(1, int(float(self.vars["refresh_minutes"].get()))))
        except ValueError:
            messagebox.showwarning("提示", "刷新间隔需为数字", parent=self)
            return
        try:
            cfg["opacity"] = min(1.0, max(0.5, float(self.vars["opacity"].get())))
        except ValueError:
            messagebox.showwarning("提示", "不透明度需为 0.5-1.0", parent=self)
            return
        try:
            cfg["ui_scale"] = int(self.scale_var.get().rstrip("%")) / 100.0
        except ValueError:
            cfg["ui_scale"] = 1.0
        theme_name = self.theme_var.get()
        cfg["theme"] = next((k for k, v in THEMES.items() if v["name"] == theme_name),
                            "midnight")
        cfg["always_on_top"] = bool(self.top_var.get())
        cfg["locked"] = bool(self.lock_var.get())
        cfg["show_seconds"] = bool(self.sec_var.get())
        want_start = bool(self.start_var.get())
        if want_start != autostart_get():
            ok, msg = autostart_set(want_start)
            if not ok:
                messagebox.showwarning("开机自启",
                                       f"设置开机自启失败：{msg}", parent=self)
            cfg["autostart"] = want_start if ok else autostart_get()
        else:
            cfg["autostart"] = want_start
        for key in ("hotkey_settings", "hotkey_quit"):
            disp = self.hk_vars[key].get()
            cfg[key] = "+".join(x.strip().lower() for x in disp.split("+")
                                if x.strip())
        for p in self.profiles:
            sv = self.threshold_vars.get(p.get("id"))
            if sv is not None:
                try:
                    p["threshold"] = max(0.0, float(sv.get().strip() or "0"))
                except ValueError:
                    p["threshold"] = 0
        cfg["profiles"] = self.profiles
        self.on_save(cfg)
        self.destroy()


# ----------------------------------------------------------------------------
# 布局尺寸（逻辑坐标）。头部固定，账户区超出屏幕高度时内部滚动
# ----------------------------------------------------------------------------
W = 340
HEAD_H = 160                 # 左时钟 + 右天气（并排布局）
HEAD_MID_X = 170              # 左右两栏分界（点击区域用）
WEATHER_BOTTOM = 152
CLOCK_TOP = 56
CLOCK_BOTTOM = 152
BOTTOM_PAD = 14
ACC_H_SM, VOLC_H, ACC_GAP = 62, 78, 12
ACC_PAD = 14
EMPTY_H = 44
SCREEN_MARGIN = 16


def acc_block_height(p):
    # 所有平台账户卡统一高度（三行结构：名称/余额 + 明细 + 预警），视觉对齐
    # 余额曲线不再画在卡片上，改为「余额历史…」窗口里按需查看
    return VOLC_H


def _clip_name(text, max_units=25):
    text = str(text or "")
    units, out = 0, []
    for ch in text:
        cu = 2 if ord(ch) > 0x2E7F else 1
        if units + cu > max_units:
            return "".join(out) + "…"
        out.append(ch)
        units += cu
    return text


def acc_content_height(profiles):
    if not profiles:
        return EMPTY_H + ACC_PAD * 2
    blocks = -ACC_GAP
    for p in profiles:
        blocks += acc_block_height(p) + ACC_GAP
    return ACC_PAD + blocks + ACC_PAD


def window_layout(profiles, screen_h):
    """返回 (窗口高, 账户可视高, 账户内容高)。"""
    content = acc_content_height(profiles)
    max_mid = screen_h - HEAD_H - BOTTOM_PAD - SCREEN_MARGIN
    mid = int(min(content, max(96, max_mid)))
    return HEAD_H + mid + BOTTOM_PAD, mid, content


# ----------------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------------
class Widget:
    def __init__(self):
        self.cfg = load_config()
        self.weather = None
        self.weather_err = None
        self.prof_data = {}
        self.bal_cache = load_balance_cache()
        self.updated_at = "--:--:--"
        self.weather_icon_items = []
        self._weather_icon_key = None
        self.hover_btn = None
        self.update_info = None      # (新版本号, 下载页) —— 有更新时状态行提示
        self._warn_flags = {}        # pid -> 当前是否处于预警
        self._low_seen = {}          # pid -> 上一次已提醒过的状态
        self._manual_check = False
        self._balloon = None
        self._dirty = True          # 仅在数据变化时重绘界面（降低 CPU）
        self._cursor = ""
        self._press = None
        self._drag_from = None
        self._drag_ts = 0.0
        self.acc_off = 0.0
        self.mid_h = 96
        self.acc_content_h = 0
        self.foot_top = HEAD_H

        dpi_scale = 1.0
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            hdc = ctypes.windll.user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            ctypes.windll.user32.ReleaseDC(0, hdc)
            dpi_scale = max(1.0, dpi / 96.0)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        self.dpi_scale = dpi_scale
        try:
            self.ui_scale = float(self.cfg.get("ui_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            self.ui_scale = 1.0
        self.ui_scale = min(UI_SCALE_MAX, max(UI_SCALE_MIN, self.ui_scale))
        self.scale = self.dpi_scale * self.ui_scale

        self.root = tk.Tk()
        self.root.report_callback_exception = (
            lambda exc_type, exc, tb: log_exception(
                "Tk回调异常:\n" + "".join(
                    traceback.format_exception(exc_type, exc, tb))))
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", bool(self.cfg.get("always_on_top", False)))
        self._apply_opacity()

        self.c = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.c.pack()
        self.cv = CV(self.c, self.scale)
        ww, wh, _ = self._window_px()
        self.root.geometry(f"{ww}x{wh}+{self._init_x(ww)}+{self._init_y(wh)}")
        self._apply_transparent()
        self.c.config(width=ww, height=wh)

        self.q = queue.Queue()
        self.worker = RefreshThread(lambda: self.cfg, self.q)
        self.worker.start()

        self.hotkeys = None
        self._init_hotkeys()
        self._build_menu()
        self._bind_events()
        self._build_ui()

        self.root.after(200, self._poll_queue)
        self.root.after(100, self._tick_clock)
        self.root.after(300, self.worker.refresh_weather)
        self.root.after(300, self.worker.refresh_platforms)

    @property
    def t(self):
        return THEMES.get(self.cfg.get("theme"), THEMES["midnight"])

    def _window_px(self):
        h, mid, content = window_layout(
            self.cfg.get("profiles", []), self.root.winfo_screenheight())
        self.mid_h = mid
        self.acc_content_h = content
        self.foot_top = HEAD_H + mid
        return int(W * self.scale), int(h * self.scale), h

    def _apply_opacity(self):
        """只在透明度 < 100% 时启用半透明（Layered Window）。

        半透明窗口每次重绘都要由 DWM 软件合成整窗，是空闲 CPU 与
        「电源使用情况」偏高的主因；100% 时 Tk 走普通窗口路径。
        """
        try:
            op = float(self.cfg.get("opacity", 1.0))
        except (TypeError, ValueError):
            op = 1.0
        op = min(1.0, max(0.2, op))
        try:
            self.root.wm_attributes("-alpha", 1.0 if op >= 0.999 else op)
        except Exception:
            pass

    def _apply_transparent(self):
        key = trans_key(self.t["bg"])
        self.root.configure(bg=key)
        self.c.configure(bg=key)
        try:
            self.root.wm_attributes("-transparentcolor", key)
        except Exception:
            pass

    def _init_x(self, ww):
        x = int(self.cfg.get("window_x", -1))
        if x >= 0:
            return min(x, max(0, self.root.winfo_screenwidth() - ww - 8))
        return self.root.winfo_screenwidth() - ww - 24

    def _init_y(self, wh):
        y = int(self.cfg.get("window_y", -1))
        if y >= 0:
            return min(y, max(0, self.root.winfo_screenheight() - wh - 8))
        return 24

    # ---------------- 界面分层 ----------------
    def _build_ui(self, scroll_to_bottom=False):
        cv, t = self.cv, self.t
        self.c.delete("all")
        self.weather_icon_items = []
        self._weather_icon_key = None
        _, _, h = self._window_px()

        # 1) 卡片整体背景（最底层）
        cv.round_rect(6, 6, W - 6, h - 6, 18, fill=t["bg"], outline=t["stroke"],
                      width=max(1, int(1.2 * self.scale)), tags=("cardbg",))

        # 2) 账户层（可整体滚动，位于遮罩之下）
        self.prof_items = {}
        scrollable = self.acc_content_h > self.mid_h
        if scroll_to_bottom and scrollable:
            self.acc_off = float(self.mid_h - self.acc_content_h)
        else:
            self.acc_off = 0.0
        self._build_accounts()

        # 3) 头部遮罩 + 天气/时钟（遮住滚入头部的账户内容）
        cv.rect(8, 8, W - 8, HEAD_H + 2, fill=t["bg"], outline="",
                tags=("headmask",))
        self._build_head()

        # 4) 左上角状态行（更新于… + 操作提示），与右上角按钮同排构成工具条
        self._build_status()

        # 5) 顶部按钮（最上层）
        self._redraw_buttons()

        # 6) 滚动条
        self._build_scrollbar()

    def _build_head(self):
        cv, t = self.cv, self.t
        self.items = {}

        # 左栏：时钟 + 日期/农历两行（收窄，避免侵入天气区）
        _d0 = now_cst()
        _wk0 = "周" + "一二三四五六日"[_d0.weekday()]
        _fmt = "%H:%M:%S" if self.cfg.get("show_seconds", False) else "%H:%M"
        self.items["clock"] = cv.text(24, 60, _d0.strftime(_fmt),
                                      size=21, color=t["text"], bold=True,
                                      family=MONO_FONT, anchor="nw")
        self.items["date"] = cv.text(
            24, 98, f"{_d0.month}月{_d0.day}日 {_wk0}",
            size=9.5, color=t["text2"], anchor="nw")
        self.items["lunar"] = cv.text(
            24, 114, lunar_line(_d0.date()),
            size=8.5, color=t["text3"], anchor="nw")

        # 左右分区参考线（视觉分区，弱化处理）
        cv.line(168, 52, 168, 140, fill=t["stroke"], width=1)

        # 右栏：大温度 + 城市行 + 两行详情（右对齐，起点不越过分区线）
        self.items["temp"] = cv.text(W - 24, 52, "", size=24, color=t["text"],
                                     bold=True, family="Segoe UI", anchor="ne")
        self.items["city"] = cv.text(W - 24, 98, "天气加载中…", size=9.5,
                                     color=t["text2"], anchor="ne")
        self.items["wdetail"] = cv.text(W - 24, 114, "", size=8.5,
                                        color=t["text3"], anchor="ne")
        self.items["wday"] = cv.text(W - 24, 130, "", size=8.5,
                                     color=t["text3"], anchor="ne")

        cv.line(22, CLOCK_BOTTOM + 2, W - 22, CLOCK_BOTTOM + 2,
                fill=t["stroke"], width=1)

    def _build_accounts(self):
        cv, t = self.cv, self.t
        profiles = self.cfg.get("profiles", [])
        y = HEAD_H + ACC_PAD + self.acc_off
        if profiles:
            for p in profiles:
                h = acc_block_height(p)
                self._build_profile_block(p, y, h)
                y += h + ACC_GAP
        else:
            cv.round_rect(20, y, W - 20, y + EMPTY_H, 12, fill=t["card_alt"],
                          outline=t["stroke"], width=1, tags=("acc",))
            cv.text(W / 2, y + 9, "尚未添加监控账户", size=10, color=t["text2"],
                    anchor="n", bold=True, tags=("acc",))
            cv.text(W / 2, y + 28, "右键 →「设置…」添加平台账户",
                    size=8.5, color=t["text3"], anchor="n", tags=("acc",))

    def _build_profile_block(self, p, y0, h):
        cv, t = self.cv, self.t
        pid = p["id"]
        brand_key = PLATFORMS.get(p.get("type"), {}).get("brand", "ds")
        brand = t.get(brand_key, t["ds"])
        cv.round_rect(20, y0, W - 20, y0 + h, 12, fill=t["card_alt"],
                      outline=t["stroke"], width=1, tags=("acc",))
        # 左缘品牌色竖条
        cv.round_rect(26, y0 + 12, 30, y0 + h - 12, 2, fill=brand, outline="",
                      tags=("acc",))
        dot = cv.oval(38, y0 + 15, 46, y0 + 23, fill=t["text3"], outline="",
                      tags=("acc",))
        cv.text(52, y0 + 8, _clip_name(p.get("name", "")), size=10.5,
                color=brand, bold=True, tags=("acc",))
        # 余额（右上，收敛字号避免喧宾夺主）
        bal = cv.text(W - 30, y0 + 10, "", size=11.5, color=t["text"], bold=True,
                      family="Segoe UI", anchor="ne", tags=("acc",))
        # 预警胶囊徽章（默认隐藏，与「较上次」互斥）
        cv.round_rect(W - 100, y0 + 31, W - 30, y0 + 49, 9, fill=t["errbg"],
                      outline="", tags=("acc", f"warn_{pid}"))
        cv.text(W - 65, y0 + 34, "余额不足", size=8.5, color=t["err"],
                bold=True, anchor="n", tags=("acc", f"warn_{pid}"))
        self.c.itemconfig(f"warn_{pid}", state="hidden")
        line1 = cv.text(38, y0 + 34, "加载中…", size=9, color=t["text3"],
                        tags=("acc",))
        chg = cv.text(W - 30, y0 + 35, "", size=8.5, color=t["text3"],
                      anchor="ne", tags=("acc",))
        items = {"dot": dot, "bal": bal, "line1": line1, "chg": chg,
                 "y0": y0}
        if h == VOLC_H:
            items["line2"] = cv.text(38, y0 + 52, "", size=8.5,
                                     color=t["text3"], tags=("acc",))
        self.prof_items[pid] = items

    def _hint_text(self):
        if self.cfg.get("locked"):
            return "已锁定"
        if self.acc_content_h > self.mid_h:
            return "滚轮翻页"
        return "右键菜单"

    def _foot_text(self):
        """状态行右侧小字：有新版时优先提示升级，否则显示操作提示。"""
        if self.update_info:
            return f"有新版本 v{self.update_info[0]} · 点此下载"
        return self._hint_text()

    def _foot_color(self):
        return self.t["accent"] if self.update_info else self.t["text3"]

    def _on_foot_click(self, _event=None):
        if self.update_info and self.update_info[1]:
            webbrowser.open(self.update_info[1])

    def _flash_foot(self, text, ms=4000):
        """在状态行短暂显示一条提示，然后恢复。"""
        self.c.itemconfig("foot_hint", text=text, fill=self.t["ok"])
        self.root.after(ms, lambda: self.c.itemconfig(
            "foot_hint", text=self._foot_text(), fill=self._foot_color()))

    def _open_history(self):
        """打开余额历史窗口（点开才看，看完关掉就行）。"""
        win = getattr(self, "_history_win", None)
        try:
            if win is not None and win.winfo_exists():
                win.deiconify()
                win.lift()
                win.focus_force()
                return
        except Exception:
            pass
        try:
            self._history_win = HistoryWindow(self.root, self.cfg,
                                              self.bal_cache)
        except Exception as e:
            log_exception(f"打开余额历史失败: {e}")

    def _manual_update_check(self):
        self._manual_check = True
        self.c.itemconfig("foot_hint", text="正在检查更新…",
                          fill=self.t["text3"])
        threading.Thread(target=self._do_update_check, daemon=True).start()

    def _do_update_check(self):
        try:
            tag, url = fetch_latest_release()
        except Exception:
            tag, url = None, None
        self.q.put(("update", tag, url))

    # ---------------- 余额历史 / 系统通知 ----------------
    def _push_history(self, pid, value):
        """按时间记录余额（最多 200 点；5 分钟内只覆盖最后一个点）。"""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        ent = self.bal_cache.setdefault(pid, {})
        hist = ent.get("hist")
        if not isinstance(hist, list):
            hist = []
        now = time.time()
        if hist and now - float(hist[-1][0]) < 300:
            hist[-1] = [round(now, 1), round(v, 4)]
        else:
            hist.append([round(now, 1), round(v, 4)])
        ent["hist"] = hist[-200:]

    def _notify(self, title, text):
        """弹一条系统通知；失败就安静放弃，不影响卡片。"""
        try:
            if self._balloon is None:
                self._balloon = Balloon()
                self._balloon.attach(self.root.winfo_id())
            self._balloon.notify(title, text)
        except Exception:
            pass

    def _check_low_notify(self, pid, name, items):
        """余额从正常跌到阈值以下时提醒一次（回到正常后可再次提醒）。"""
        low = bool(self._warn_flags.get(pid))
        if pid not in self._low_seen:
            self._low_seen[pid] = low      # 启动时就已预警的状态只记录，不打扰
            return
        if (low and not self._low_seen[pid]
                and self.cfg.get("notify_low_balance", True)):
            bal = self.c.itemcget(items["bal"], "text") or "余额不足"
            self._notify(f"{APP_NAME} · 余额不足",
                         f"{name} 当前 {bal}，已低于预警阈值")
        self._low_seen[pid] = low

    def _build_status(self):
        """左上角状态行：状态点 + 更新时间 + 操作提示/按钮说明（与右上角工具条同排）。"""
        cv, t = self.cv, self.t
        cy = 26
        cv.oval(24, cy - 4, 32, cy + 4, fill=t["ok"], outline="",
                tags=("foot", "status_dot"))
        cv.text(38, cy - 7, "", size=8.5, color=t["text3"], anchor="nw",
                tags=("foot", "foot_updated"))
        cv.text(194, cy - 7, self._foot_text(), size=8.5,
                color=self._foot_color(), anchor="ne",
                tags=("foot", "foot_hint"))

    def _pointer_inside(self):
        try:
            px, py = self.root.winfo_pointerxy()
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            return (x <= px < x + self.root.winfo_width()
                    and y <= py < y + self.root.winfo_height())
        except Exception:
            return False

    def _build_scrollbar(self):
        cv, t = self.cv, self.t
        self.c.delete("scrollbar")
        if self.acc_content_h <= self.mid_h + 1:
            return
        top, bot = HEAD_H + 8, self.foot_top - 8
        slot = bot - top
        thumb_h = max(26, int(slot * self.mid_h / self.acc_content_h))
        span = self.acc_content_h - self.mid_h
        frac = (-self.acc_off / span) if span > 0 else 0
        ty = top + int(frac * (slot - thumb_h))
        cv.round_rect(W - 10, ty, W - 7, ty + thumb_h, 2, fill=t["text3"],
                      outline="", tags=("scrollbar",))

    # ---------------- 顶部按钮 ----------------
    BTN_SPECS = (("btn_refresh", "刷新", "刷新数据", None),
                 ("btn_lock", "锁定", "锁定/解锁界面", "locked"),
                 ("btn_pin", "置顶", "窗口置顶开关", "always_on_top"),
                 ("btn_close", "关闭", "关闭小工具", None))

    def _btn_geometry(self):
        """整簇按钮的几何：一个分组容器 + 4 个等宽分段。"""
        seg_w, seg_h, pad, margin = 29, 30, 3, 18
        group_w = pad * 2 + seg_w * 4
        x0 = W - margin - group_w
        y0 = 26 - (seg_h + pad * 2) / 2
        return x0, y0, seg_w, seg_h, pad

    def _btn_center(self, tag):
        idx = [s[0] for s in self.BTN_SPECS].index(tag)
        x0, y0, seg_w, seg_h, pad = self._btn_geometry()
        return x0 + pad + seg_w * idx + seg_w / 2, y0 + pad + seg_h / 2

    def _redraw_buttons(self):
        for tag, _label, _tip, _flag in self.BTN_SPECS:
            self.c.delete(tag)
            self.c.delete("hit_" + tag)
        self.c.delete("btngroup")
        cv, t = self.cv, self.t
        x0, y0, seg_w, seg_h, pad = self._btn_geometry()
        # 分组容器：一整个圆角工具条，内部 4 等分
        cv.round_rect(x0, y0, W - 18, y0 + seg_h + pad * 2, 10,
                      fill=t["card_alt"], outline=t["stroke"], width=1,
                      tags=("btngroup",))
        # 每个分段既是视觉层也是点击热区
        for tag, _label, _tip, flag in self.BTN_SPECS:
            active = bool(self.cfg.get(flag)) if flag else False
            hovered = self.hover_btn == tag
            if tag == "btn_close" and hovered:
                fill = "#E81123"
            elif active:
                fill = t["accent"]
            elif hovered:
                fill = t["hover"]
            else:
                fill = t["card_alt"]
            cx, cy = self._btn_center(tag)
            cv.round_rect(cx - seg_w / 2 + 1, y0 + pad + 1,
                          cx + seg_w / 2 - 1, y0 + pad + seg_h - 1, 7,
                          fill=fill, outline="", tags=(tag, "hit_" + tag))
        # 分段分隔线（画在高亮之上，保持等分感）
        for i in range(1, 4):
            dx = x0 + pad + seg_w * i
            cv.line(dx, y0 + pad + 6, dx, y0 + pad + seg_h - 6,
                    fill=t["stroke"], width=1, tags=("btngroup",))
        for tag, _label, _tip, _flag in self.BTN_SPECS:
            cx, cy = self._btn_center(tag)
            self._draw_btn_icon(tag, cx, cy)

    def _icon_color_for(self, tag):
        t = self.t
        _tag, _label, _tip, flag = next(s for s in self.BTN_SPECS if s[0] == tag)
        active = bool(self.cfg.get(flag)) if flag else False
        hovered = self.hover_btn == tag
        if active:
            return t["bg"]
        if tag == "btn_close" and hovered:
            return "#FFFFFF"
        return t["text"] if hovered else t["text"]

    def _draw_btn_icon(self, tag, cx, cy):
        """按标签绘制图标（统一 2px 线宽、18px 视觉尺寸，确保辨识度）。"""
        col = self._icon_color_for(tag)
        {
            "btn_refresh": self._icon_refresh,
            "btn_lock": self._icon_lock,
            "btn_pin": self._icon_pin,
            "btn_close": self._icon_close,
        }[tag](cx, cy, col)

    def _icon_stroke(self):
        """图标统一线宽：与卡片文字重量匹配，缩放时按比例取整。"""
        return max(1, int(round(1.9 * self.scale)))

    def _icon_refresh(self, cx, cy, col):
        """顺时针回转箭头：描边圆弧 + 折线箭头。

        旧版用实心三角 + 2px 圆弧，缩到 15px 视觉尺寸后糊成一坨；
        改为全描边后与卡片其余细线元素一致。
        """
        cv, w = self.cv, self._icon_stroke()
        cv.arc(cx - 6, cy - 6, cx + 6, cy + 6, start=62, extent=288, style="arc",
               outline=col, width=w, tags=("btn_refresh",))
        cv.line(cx + 2.0, cy - 6.6, cx + 6.4, cy - 5.4, fill=col, width=w,
                capstyle="round", tags=("btn_refresh",))
        cv.line(cx + 6.4, cy - 5.4, cx + 4.4, cy - 1.6, fill=col, width=w,
                capstyle="round", tags=("btn_refresh",))

    def _icon_lock(self, cx, cy, col):
        """锁：锁体只画轮廓（旧版实心块太重），锁梁按锁定/解锁两种开口。"""
        cv, w = self.cv, self._icon_stroke()
        locked = bool(self.cfg.get("locked"))
        cv.round_rect(cx - 5.5, cy - 0.5, cx + 5.5, cy + 8.5, 2.5, fill="",
                      outline=col, width=w, tags=("btn_lock",))
        if locked:
            cv.arc(cx - 4.5, cy - 10, cx + 4.5, cy + 1, start=0, extent=180,
                   style="arc", outline=col, width=w, tags=("btn_lock",))
            cv.line(cx - 4.5, cy - 4.5, cx - 4.5, cy + 1, fill=col, width=w,
                    tags=("btn_lock",))
            cv.line(cx + 4.5, cy - 4.5, cx + 4.5, cy + 1, fill=col, width=w,
                    tags=("btn_lock",))
        else:
            # 开口锁：锁梁只画左半并向外张开
            cv.arc(cx - 9.0, cy - 10, cx + 0.0, cy + 1, start=310, extent=160,
                   style="arc", outline=col, width=w, tags=("btn_lock",))
            cv.line(cx - 8.2, cy - 4.6, cx - 8.2, cy + 1, fill=col, width=w,
                    tags=("btn_lock",))

    def _icon_pin(self, cx, cy, col):
        """图钉：顶横杆 + 描边钉帽 + 钉针（钉帽由实心改为描边，减轻重量）。"""
        cv, w = self.cv, self._icon_stroke()
        cv.line(cx - 6, cy - 7, cx + 6, cy - 7, fill=col, width=w,
                capstyle="round", tags=("btn_pin",))
        cv.polygon([cx - 3.6, cy - 5.2, cx + 3.6, cy - 5.2, cx + 1.7, cy + 1.0,
                    cx - 1.7, cy + 1.0], fill="", outline=col, width=w,
                   tags=("btn_pin",))
        cv.line(cx, cy + 1.0, cx, cy + 8.6, fill=col, width=w,
                capstyle="round", tags=("btn_pin",))

    def _icon_close(self, cx, cy, col):
        cv, w = self.cv, self._icon_stroke()
        cv.line(cx - 5.5, cy - 5.5, cx + 5.5, cy + 5.5, fill=col, width=w,
                capstyle="round", tags=("btn_close",))
        cv.line(cx + 5.5, cy - 5.5, cx - 5.5, cy + 5.5, fill=col, width=w,
                capstyle="round", tags=("btn_close",))


    def _button_hover(self, tag, entering):
        """悬停：重绘按钮簇，并在左上角状态行右侧显示该按钮的功能名。"""
        if (self.hover_btn == tag) == bool(entering):
            # 状态没变化就不再重绘：重绘会重建光标下的画布项，从而再次
            # 触发 <Enter>，形成 Enter→重绘→Enter 的死循环（100% 单核、界面卡死）
            return
        if getattr(self, "_hover_drawing", False):
            return
        self.hover_btn = tag if entering else None
        self._hover_drawing = True
        try:
            self._redraw_buttons()
        finally:
            self._hover_drawing = False
        if entering:
            tip = next(s[2] for s in self.BTN_SPECS if s[0] == tag)
            self.c.itemconfig("foot_hint", text=tip, fill=self.t["text"])
        else:
            self.c.itemconfig("foot_hint", text=self._foot_text(),
                              fill=self._foot_color())
        self._set_cursor("hand2" if entering else "")

    # ---------------- 全局热键 ----------------
    def _init_hotkeys(self):
        try:
            self.hotkeys = HotkeyManager(self.root.winfo_id(), self.q)
            self.hotkeys.attach()
            self._reload_hotkeys(notify=False)
        except Exception as e:
            log_exception(f"热键初始化失败: {e}")
            self.hotkeys = None

    def _reload_hotkeys(self, notify=True):
        if not self.hotkeys:
            return
        errors = []
        for name, key in (("settings", "hotkey_settings"),
                          ("quit", "hotkey_quit")):
            msg = self.hotkeys.register(name, self.cfg.get(key, ""))
            if msg:
                errors.append(msg)
        if notify and errors:
            messagebox.showwarning("快捷键", "\n".join(errors), parent=self.root)

    def _bring_to_front(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    # ---------------- 菜单与事件 ----------------
    def _build_menu(self):
        self.menu = tk.Menu(self.root, tearoff=0, bg="#1E2230", fg="#EDF0F6",
                            activebackground="#5B8DEF", activeforeground="white",
                            font=(UI_FONT, 9), bd=0)
        self.menu.add_command(label="立即刷新", command=self._manual_refresh)
        self.menu.add_command(label="检查更新", command=self._manual_update_check)
        self.menu.add_command(label="余额历史…", command=self._open_history)
        self.menu.add_command(label="设置…（添加账户 / 充值）",
                              command=self._open_settings)
        paid = [p for p in self.cfg.get("profiles", [])
                if PLATFORMS.get(p.get("type"), {}).get("topup_url")]
        if paid:
            sub = tk.Menu(self.menu, tearoff=0, bg="#1E2230", fg="#EDF0F6",
                          activebackground="#5B8DEF",
                          activeforeground="white", font=(UI_FONT, 9), bd=0)
            for p in paid:
                url = PLATFORMS[p["type"]]["topup_url"]
                sub.add_command(label=p.get("name") or platform_label(p["type"]),
                                command=lambda u=url: open_url_async(u))
            self.menu.add_cascade(label="去充值…", menu=sub)
        self.menu.add_command(label=self._pin_label(), command=self._toggle_pin)
        self.menu.add_separator()
        self.menu.add_command(label="放大界面（Ctrl+滚轮上）", command=self._zoom_in)
        self.menu.add_command(label="缩小界面（Ctrl+滚轮下）", command=self._zoom_out)
        self.menu.add_command(label="重置缩放（100%）", command=self._zoom_reset)
        self.menu.add_command(label="切换配色", command=self._cycle_theme)
        self.menu.add_separator()
        self.menu.add_command(label=self._lock_label(), command=self._toggle_lock)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self._quit)

    def _pin_label(self):
        return "取消置顶" if self.cfg.get("always_on_top") else "窗口置顶"

    def _lock_label(self):
        return "解锁界面" if self.cfg.get("locked") else "锁定界面"

    def _bind_events(self):
        self.c.bind("<Button-1>", self._on_press)
        self.c.bind("<B1-Motion>", self._on_drag)
        self.c.bind("<ButtonRelease-1>", self._on_release)
        self.c.bind("<Button-3>", self._show_menu)
        self.c.tag_bind("foot_hint", "<Button-1>", self._on_foot_click)
        self.c.tag_bind("foot_hint", "<Enter>",
                        lambda e: self._set_cursor("hand2"))
        self.c.tag_bind("foot_hint", "<Leave>",
                        lambda e: self._set_cursor(""))
        self.c.bind("<Motion>", self._on_motion)
        self.c.bind("<MouseWheel>", self._on_wheel)
        self.c.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.root.bind("<Control-MouseWheel>", self._on_ctrl_wheel)
        self.root.bind("<MouseWheel>", self._on_wheel)
        self.root.bind("<Escape>", lambda e: self._quit())
        for tag in ("btn_refresh", "btn_lock", "btn_pin", "btn_close"):
            self.c.tag_bind(tag, "<Button-1>",
                            lambda e, tg=tag: self._on_top_button(tg))
            self.c.tag_bind(tag, "<Enter>",
                            lambda e, tg=tag: self._button_hover(tg, True))
            self.c.tag_bind(tag, "<Leave>",
                            lambda e, tg=tag: self._button_hover(tg, False))

    def _show_menu(self, event):
        locked = bool(self.cfg.get("locked"))
        self.menu.entryconfig(2, label=self._pin_label())
        self.menu.entryconfig(9, label=self._lock_label())
        hs = fmt_hk(self.cfg.get("hotkey_settings", ""))
        hq = fmt_hk(self.cfg.get("hotkey_quit", ""))
        self.menu.entryconfig(1, label=("设置…  " + hs) if hs else "设置…")
        self.menu.entryconfig(11, label=("退出  " + hq) if hq else "退出")
        for idx in (0, 4, 5, 6, 7):
            self.menu.entryconfig(idx, state="disabled" if locked else "normal")
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _set_cursor(self, cur):
        """光标状态缓存：避免每次鼠标移动都发 Tk 调用（Windows 上是同步调用，很贵）。"""
        if getattr(self, "_cursor", "") != cur:
            self._cursor = cur
            self.c.config(cursor=cur)

    def _on_motion(self, event):
        if self._hit_top_button() or (
                not self.cfg.get("locked") and self._current_action()):
            self._set_cursor("hand2")
            return
        if self.cfg.get("locked"):
            self._set_cursor("")
            return
        lx, ly = event.x / self.scale, event.y / self.scale
        if CLOCK_TOP <= ly < CLOCK_BOTTOM and lx < HEAD_MID_X:
            self._set_cursor("hand2")
        elif CLOCK_TOP <= ly < WEATHER_BOTTOM and lx >= HEAD_MID_X:
            self._set_cursor("hand2")
        else:
            self._set_cursor("")

    def _on_wheel(self, event):
        if self.cfg.get("locked") or self.acc_content_h <= self.mid_h + 1:
            return
        ly = event.y / self.scale
        if not (HEAD_H < ly < self.foot_top):
            return
        off_min = float(self.mid_h - self.acc_content_h)
        new_off = min(0.0, max(off_min, self.acc_off + event.delta / 120.0 * 26))
        dy = int((new_off - self.acc_off) * self.scale)
        if dy:
            self.c.move("acc", 0, dy)
            self.acc_off = new_off
            self._build_scrollbar()

    def _hit_top_button(self):
        for it in self.c.find_withtag("current"):
            tags = self.c.gettags(it)
            for tg in ("btn_refresh", "btn_lock", "btn_pin", "btn_close"):
                if tg in tags:
                    return tg
        return None

    def _current_action(self):
        for it in self.c.find_withtag("current"):
            for tg in self.c.gettags(it):
                if tg.startswith("act_"):
                    return tg
        return None

    def _on_press(self, event):
        if self._hit_top_button():
            self._press = None
            return
        self._press = (event.x_root, event.y_root, time.time())
        self._drag_from = (event.x_root, event.y_root,
                           self.root.winfo_x(), self.root.winfo_y())
        self._drag_ts = 0.0

    def _apply_drag(self, event):
        if not self._press:
            return
        ox, oy, wx, wy = self._drag_from
        self.root.geometry(
            f"+{wx + event.x_root - ox}+{wy + event.y_root - oy}")

    def _on_drag(self, event):
        if not self._press or self.cfg.get("locked"):
            return
        now = time.time()
        if now - self._drag_ts < 0.04:      # 拖动节流到 25fps，避免整窗重绘
            return
        self._drag_ts = now
        self._apply_drag(event)

    def _on_release(self, event):
        if not self._press:
            return
        if not self.cfg.get("locked"):
            self._apply_drag(event)      # 补上被节流丢掉的最后一帧
        ox, oy, t0 = self._press
        moved = abs(event.x_root - ox) + abs(event.y_root - oy)
        if moved < 6 and time.time() - t0 < 0.6 and not self.cfg.get("locked"):
            act = self._current_action()
            if act:
                self._route_action(act)
            else:
                lx, ly = event.x / self.scale, event.y / self.scale
                if CLOCK_TOP <= ly < CLOCK_BOTTOM and lx < HEAD_MID_X:
                    self._open_datetime()
                elif CLOCK_TOP <= ly < WEATHER_BOTTOM and lx >= HEAD_MID_X:
                    self._open_weather()
        self.cfg["window_x"] = self.root.winfo_x()
        self.cfg["window_y"] = self.root.winfo_y()
        save_config(self.cfg)
        self._press = None

    def _on_top_button(self, tag):
        if self.cfg.get("locked") and tag in ("btn_refresh", "btn_pin"):
            return
        if tag == "btn_close":
            self._quit()
        elif tag == "btn_refresh":
            self._manual_refresh()
        elif tag == "btn_pin":
            self._toggle_pin()
        elif tag == "btn_lock":
            self._toggle_lock()

    def _route_action(self, act):
        if act == "act_weather":
            self._open_weather()
        elif act == "act_clock":
            self._open_datetime()

    def _open_weather(self):
        city = self.cfg.get("city", "")
        open_url_async(
            "https://cn.bing.com/search?q=" + urllib.parse.quote(city + "天气预报"))

    def _open_datetime(self):
        """点击时钟：打开系统日期时间面板。

        关键：sleep / 注入鼠标键盘 / os.startfile 一律不能在 Tk 主线程执行，
        否则会阻塞消息循环，界面直接假死数秒。
        """
        try:
            self._bring_to_front()
        except Exception:
            pass
        threading.Thread(target=self._open_datetime_worker, daemon=True).start()

    def _open_datetime_worker(self):
        # 打开系统日历与时钟面板：优先模拟点击任务栏右下角系统时钟
        try:
            u = ctypes.windll.user32
            r = find_tray_clock_rect()
            if r:
                cx, cy = (r[0] + r[2]) // 2, (r[1] + r[3]) // 2
            else:
                cx = u.GetSystemMetrics(0) - 90
                cy = u.GetSystemMetrics(1) - 16
            u.SetCursorPos(cx, cy)
            time.sleep(0.12)
            u.mouse_event(0x0002, 0, 0, 0, 0)
            time.sleep(0.06)
            u.mouse_event(0x0004, 0, 0, 0, 0)
            return
        except Exception:
            log_exception("点击任务栏时钟失败，尝试 Win+N")
        # 兜底 1：Win+N 日历通知面板
        try:
            u = ctypes.windll.user32
            time.sleep(0.15)
            u.keybd_event(0x5B, 0x5B, 0x0001, 0)
            time.sleep(0.12)
            u.keybd_event(0x4E, 0x31, 0, 0)
            time.sleep(0.12)
            u.keybd_event(0x4E, 0x31, 0x0002, 0)
            u.keybd_event(0x5B, 0x5B, 0x0001 | 0x0002, 0)
            return
        except Exception:
            pass
        # 兜底 2：系统日期时间设置页 / 网页时钟
        try:
            os.startfile("ms-settings:dateandtime")
        except Exception:
            webbrowser.open("https://time.is/zh/")

    def _manual_refresh(self):
        if self.cfg.get("locked"):
            return
        self.worker.refresh_weather()
        self.worker.refresh_platforms()

    def _toggle_pin(self):
        if self.cfg.get("locked"):
            return
        self.cfg["always_on_top"] = not self.cfg.get("always_on_top", False)
        self.root.wm_attributes("-topmost", bool(self.cfg["always_on_top"]))
        save_config(self.cfg)
        self._redraw_buttons()

    def _toggle_lock(self):
        self.cfg["locked"] = not self.cfg.get("locked", False)
        save_config(self.cfg)
        self._rebuild_canvas("topleft")

    # ---------------- 缩放 / 主题 ----------------
    def _on_ctrl_wheel(self, event):
        if self.cfg.get("locked"):
            return
        step = UI_SCALE_STEP if event.delta > 0 else -UI_SCALE_STEP
        self._set_ui_scale(self.ui_scale + step)

    def _zoom_in(self):
        if not self.cfg.get("locked"):
            self._set_ui_scale(self.ui_scale + UI_SCALE_STEP)

    def _zoom_out(self):
        if not self.cfg.get("locked"):
            self._set_ui_scale(self.ui_scale - UI_SCALE_STEP)

    def _zoom_reset(self):
        if not self.cfg.get("locked"):
            self._set_ui_scale(1.0)

    def _set_ui_scale(self, value):
        value = min(UI_SCALE_MAX, max(UI_SCALE_MIN, round(value, 2)))
        if abs(value - self.ui_scale) < 0.001:
            return
        self.ui_scale = value
        self.cfg["ui_scale"] = value
        save_config(self.cfg)
        self._rebuild_canvas("center")

    def _cycle_theme(self):
        if self.cfg.get("locked"):
            return
        keys = list(THEMES.keys())
        cur = self.cfg.get("theme", "midnight")
        nxt = keys[(keys.index(cur) + 1) % len(keys)] if cur in keys else keys[0]
        self.cfg["theme"] = nxt
        save_config(self.cfg)
        self._rebuild_canvas("topleft")

    def _rebuild_canvas(self, anchor="topleft", scroll_to_bottom=False):
        old_ww, old_wh = self.c.winfo_width(), self.c.winfo_height()
        ox, oy = self.root.winfo_x(), self.root.winfo_y()
        self.scale = self.dpi_scale * self.ui_scale
        self.cv = CV(self.c, self.scale)
        ww, wh, _ = self._window_px()
        if anchor == "center":
            x, y = int(ox + (old_ww - ww) / 2), int(oy + (old_wh - wh) / 2)
        else:
            x, y = ox, oy
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = min(max(x, 0), max(0, sw - ww))
        y = min(max(y, 0), max(0, sh - wh))
        self.root.geometry(f"{ww}x{wh}+{x}+{y}")
        self.c.config(width=ww, height=wh)
        self._apply_transparent()
        self._build_ui(scroll_to_bottom=scroll_to_bottom)
        self._render_dynamic()
        self.cfg["window_x"], self.cfg["window_y"] = x, y
        save_config(self.cfg)

    # ---------------- 设置 ----------------
    def _open_settings(self):
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.deiconify()
                    dlg.lift()
                    safe_grab(dlg)
                    dlg.focus_force()
                    return
            except Exception:
                pass
        self._bring_to_front()
        try:
            self._settings_dlg = SettingsDialog(
                self.root, self.cfg, self._apply_settings,
                on_profiles=self._apply_profiles_live,
                on_history=self._open_history)
        except Exception as e:
            log_exception(f"打开设置窗口失败: {e}")

    def _apply_profiles_live(self, profiles):
        """设置对话框里账户的增 / 改 / 删即时生效，无需再点设置窗口保存。"""
        old_n = len(self.cfg.get("profiles", []))
        self.cfg["profiles"] = profiles
        save_config(self.cfg)
        ids = {x.get("id") for x in profiles}
        for dead in [k for k in list(self.prof_data.keys()) if k not in ids]:
            self.prof_data.pop(dead, None)
        self._rebuild_canvas("topleft",
                             scroll_to_bottom=(len(profiles) > old_n))
        self._manual_refresh()

    def _apply_settings(self, new_cfg):
        old_city = self.cfg.get("city")
        old_scale = float(self.cfg.get("ui_scale", 1.0))
        new_scale = float(new_cfg.get("ui_scale", 1.0))
        old_n = len(self.cfg.get("profiles", []))
        new_n = len(new_cfg.get("profiles", []))
        pos = (self.cfg.get("window_x"), self.cfg.get("window_y"))
        self.cfg = new_cfg
        self.cfg["window_x"], self.cfg["window_y"] = pos
        save_config(self.cfg)
        self._apply_opacity()
        self.root.wm_attributes("-topmost", bool(self.cfg.get("always_on_top")))
        self._reload_hotkeys()
        self.ui_scale = min(UI_SCALE_MAX, max(UI_SCALE_MIN, new_scale))
        if old_city != new_cfg.get("city"):
            self.weather = None
        anchor = "center" if abs(new_scale - old_scale) > 0.001 else "topleft"
        self._rebuild_canvas(anchor, scroll_to_bottom=(new_n > old_n))
        self._manual_refresh()

    # ---------------- 数据回流 ----------------
    def _poll_queue(self):
        got_data = False
        try:
            while True:
                msg = self.q.get_nowait()
                got_data = True
                kind = msg[0]
                if kind == "weather":
                    payload = msg[1]
                    if isinstance(payload, dict) and set(payload) == {"error"}:
                        self.weather, self.weather_err = None, payload["error"]
                    else:
                        self.weather, self.weather_err = payload, None
                elif kind == "prof":
                    _, pid, sub, payload = msg
                    d = self.prof_data.setdefault(pid, {})
                    if sub == "balance":
                        ptype = next((x.get("type") for x in
                                      self.cfg.get("profiles", [])
                                      if x.get("id") == pid), None)
                        if ptype == "zhipu":
                            new_main = (payload.get("percentage")
                                        if isinstance(payload, dict) else None)
                        else:
                            new_main = balance_main_value(ptype, payload)
                        old = (self.bal_cache.get(pid) or {}).get("main")
                        d["delta"] = (None if old is None or new_main is None
                                      else round(new_main - old, 4))
                        if new_main is not None:
                            entry = self.bal_cache.setdefault(pid, {})
                            entry["main"] = new_main
                            entry["ts"] = time.time()
                            self._push_history(pid, new_main)
                            save_balance_cache(self.bal_cache)
                        d["balance"], d["err_b"] = payload, None
                    elif sub == "usage":
                        d["usage"], d["err_u"] = payload, None
                    elif sub == "error_b":
                        d["err_b"] = payload
                        d.pop("balance", None)
                    elif sub == "error_u":
                        d["err_u"] = payload
                        d.pop("usage", None)
                elif kind == "update":
                    tag, url = msg[1], msg[2]
                    if tag and version_tuple(tag) > version_tuple(APP_VERSION):
                        self.update_info = (tag, url)
                    else:
                        self.update_info = None
                        if self._manual_check:
                            self._flash_foot(f"已是最新版本 v{APP_VERSION}")
                    self._manual_check = False
                    self.c.itemconfig("foot_hint", text=self._foot_text(),
                                      fill=self._foot_color())
                elif kind == "updated_at":
                    self.updated_at = msg[1]
                elif kind == "hotkey":
                    name = msg[1]
                    if name == "settings":
                        self._bring_to_front()
                        self.root.after(120, self._open_settings)
                    elif name == "quit":
                        self._quit()
        except queue.Empty:
            pass
        valid = {p["id"] for p in self.cfg.get("profiles", [])}
        if set(self.prof_data) != valid:
            self.prof_data = {k: v for k, v in self.prof_data.items() if k in valid}
            got_data = True
        # 只有数据真的变了才重绘（原来每 200ms 无脑全量重绘，白烧 CPU）
        if got_data or self._dirty:
            self._dirty = False
            self._render_dynamic()
        self.root.after(1000, self._poll_queue)

    def _tick_clock(self):
        d = now_cst()
        fmt = "%H:%M:%S" if self.cfg.get("show_seconds", False) else "%H:%M"
        txt = d.strftime(fmt)
        if self.c.itemcget(self.items["clock"], "text") != txt:
            self.c.itemconfig(self.items["clock"], text=txt)
        week = "周" + "一二三四五六日"[d.weekday()]
        date_txt = f"{d.month}月{d.day}日 {week}"
        if self.c.itemcget(self.items["date"], "text") != date_txt:
            self.c.itemconfig(self.items["date"], text=date_txt)
            self.c.itemconfig(self.items["lunar"], text=lunar_line(d.date()))
        if self.cfg.get("show_seconds", False):
            self.root.after(1000, self._tick_clock)
        else:
            # 不显示秒：对齐到下一分钟再刷新，避免每分钟 59 次无谓唤醒
            delay = max(1, int((60 - d.second) * 1000 - d.microsecond / 1000))
            self.root.after(delay, self._tick_clock)

    def _set_dot(self, item, state):
        color = {"ok": self.t["ok"], "err": self.t["err"],
                 "warn": self.t["warn"]}.get(state, self.t["text3"])
        self.c.itemconfig(item, fill=color)

    def _set_warn(self, pid, low):
        self.c.itemconfig(f"warn_{pid}", state="normal" if low else "hidden")
        self._warn_flags[pid] = bool(low)

    # ---------------- 渲染 ----------------
    def _render_dynamic(self):
        cv, t = self.cv, self.t
        w = self.weather
        if w:
            desc, kind = wmo_desc(w.get("code", 0))
            icon_key = (kind, bool(w.get("is_day", 1)), self.cfg.get("theme"))
            self.c.itemconfig(self.items["temp"], text=f"{round(w['temp'])}°")
            self.c.itemconfig(self.items["city"],
                              text=f"{w['city']} · {desc}")
            feels = (f"体感 {round(w['feels'])}°"
                     if w.get("feels") is not None else "")
            hum = f"湿度 {w['humidity']}%" if w.get("humidity") is not None else ""
            self.c.itemconfig(self.items["wdetail"],
                              text=" · ".join(x for x in (feels, hum) if x))
            tmax = f"{round(w['tmax'])}°" if w.get("tmax") is not None else "--"
            tmin = f"{round(w['tmin'])}°" if w.get("tmin") is not None else "--"
            pop2 = f"降水 {w['pop']}%" if w.get("pop") is not None else ""
            self.c.itemconfig(self.items["wday"],
                              text=f"今日 {tmin}~{tmax} · {pop2}".rstrip(" ·"))
            if icon_key != self._weather_icon_key:
                for it in self.weather_icon_items:
                    self.c.delete(it)
                before = set(self.c.find_all())
                draw_weather_icon(cv, 196, 82, kind, t, 0.6)
                self.weather_icon_items = [
                    i for i in self.c.find_all() if i not in before]
                self._weather_icon_key = icon_key
        else:
            if self._weather_icon_key is not None:
                for it in self.weather_icon_items:
                    self.c.delete(it)
                self.weather_icon_items = []
                self._weather_icon_key = None
            self.c.itemconfig(self.items["city"],
                              text=self.weather_err or "天气加载中…")
            self.c.itemconfig(self.items["temp"],
                              text="" if not self.weather_err else "--")
            self.c.itemconfig(self.items["wdetail"], text="")
            self.c.itemconfig(self.items["wday"], text="")

        for p in self.cfg.get("profiles", []):
            pid = p.get("id")
            items = self.prof_items.get(pid)
            if not items:
                continue
            d = self.prof_data.get(pid, {})
            try:
                threshold = float(p.get("threshold", 0) or 0)
            except (TypeError, ValueError):
                threshold = 0.0
            if p.get("type") == "volc":
                self._render_volc(pid, items, d, threshold, t)
            elif p.get("type") == "zhipu":
                self._render_zhipu(pid, items, d, threshold, t)
            else:
                self._render_money(pid, items, d, threshold, t, p)
            self._fit_line1_beside_chg(items)
            self._check_low_notify(pid, p.get("name")
                                   or platform_label(p.get("type")), items)

        self.c.itemconfig("foot_updated", text=f"更新于 {self.updated_at}")

    def _render_chg(self, items, delta, low, t, unit="¥"):
        """「较上次 ±x」提示；预警徽章显示时让位隐藏。"""
        if low or "chg" not in items:
            self.c.itemconfig(items.get("chg", 0), text="")
            return
        if delta is None or abs(delta) < 0.005:
            self.c.itemconfig(items["chg"], text="", fill=t["text3"])
            return
        sign = "+" if delta > 0 else "-"
        col = t["ok"] if delta > 0 else t["err"]
        self.c.itemconfig(items["chg"], text=f"较上次 {sign}{unit}{abs(delta):.2f}",
                          fill=col)

    def _fit_line1_beside_chg(self, items):
        """line1 过长会撞上右侧的「较上次」标签：超宽就截断加省略号，不再重叠。

        用 canvas bbox 实测像素宽度，因此在 85%~160% 缩放下同样准确。
        """
        item, chg = items.get("line1"), items.get("chg")
        if item is None or chg is None:
            return
        if not self.c.itemcget(chg, "text"):
            return
        txt = self.c.itemcget(item, "text")
        if not txt:
            return
        cb, lb = self.c.bbox(chg), self.c.bbox(item)
        if not cb or not lb:
            return
        limit = cb[0] - 8
        if lb[2] <= limit:
            return
        # 先在自然断点（（ 或 ·）处收尾，避免截出「↓…」这种半截内容
        for sep in ("（", " · "):
            k = txt.find(sep)
            if k > 0:
                self.c.itemconfig(item, text=txt[:k].rstrip())
                b = self.c.bbox(item)
                if b and b[2] <= limit:
                    return
        self.c.itemconfig(item, text=txt)
        s = txt
        while s:
            s = s[:-1]
            self.c.itemconfig(item, text=(s + "…") if s else "")
            b = self.c.bbox(item)
            if not b or b[2] <= limit:
                return

    def _render_money(self, pid, items, d, threshold, t, p):
        """DeepSeek / 硅基流动 / Kimi / WorkBuddy积分 等「余额型」平台统一渲染。"""
        bal, err = d.get("balance"), d.get("err_b")
        if bal:
            is_pts = bal.get("currency") == "PTS"
            cur = "¥" if bal.get("currency") == "CNY" else (
                "$" if bal.get("currency") == "USD" else "")
            main = balance_main_value(p.get("type"), bal)
            low = (threshold > 0 and main is not None and main < threshold) \
                or not bal.get("available", True)
            self._set_warn(pid, low)
            if is_pts:
                bal_text = f"{int(main or 0):,} 分"
            else:
                bal_text = f"{cur}{fmt_money(main if main is not None else 0)}"
            self.c.itemconfig(items["bal"], text=bal_text,
                              fill=t["err"] if low else t["text"])
            if bal.get("sub"):
                line1 = bal["sub"]
            else:
                parts = []
                if bal.get("topped_up") is not None:
                    parts.append(f"充值 {cur}{fmt_money(bal.get('topped_up', 0))}")
                if bal.get("granted") is not None:
                    parts.append(f"赠送 {cur}{fmt_money(bal.get('granted', 0))}")
                line1 = " · ".join(parts)
            self.c.itemconfig(items["line1"], text=line1, fill=t["text2"])
            if "line2" in items:
                if threshold > 0:
                    line2 = (f"预警阈值 {int(threshold)} 分" if is_pts
                             else f"预警阈值 {cur}{fmt_money(threshold)}")
                else:
                    line2 = ""
                self.c.itemconfig(items["line2"], text=line2, fill=t["text3"])
            if is_pts:
                delta = d.get("delta")
                if low or delta is None or abs(delta) < 0.5:
                    self.c.itemconfig(items["chg"], text="")
                else:
                    sign = "+" if delta > 0 else "-"
                    col = t["ok"] if delta > 0 else t["err"]
                    self.c.itemconfig(
                        items["chg"],
                        text=f"较上次 {sign}{int(abs(delta))} 分", fill=col)
            else:
                self._render_chg(items, d.get("delta"), low, t)
            self._set_dot(items["dot"], "err" if low else "ok")
        elif err:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text=err, fill=t["err"])
            if "line2" in items:
                self.c.itemconfig(items["line2"], text="")
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "err")
        else:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text="余额加载中…", fill=t["text3"])
            if "line2" in items:
                self.c.itemconfig(items["line2"], text="")
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "warn")

    def _render_zhipu(self, pid, items, d, threshold, t):
        """智谱 GLM：无公开余额接口，显示 Token 配额用量百分比。"""
        bal, err = d.get("balance"), d.get("err_b")
        if bal:
            pct = int(bal.get("percentage") or 0)
            warn_at = int(threshold) if threshold > 0 else 90
            low = pct >= warn_at
            self._set_warn(pid, low)
            self.c.itemconfig(items["bal"], text=f"{pct}%",
                              fill=t["err"] if low else t["text"])
            self.c.itemconfig(
                items["line1"],
                text=f"配额 {fmt_tokens(bal.get('usage'))} / "
                     f"{fmt_tokens(bal.get('quota'))} tokens",
                fill=t["text2"])
            if "line2" in items:
                self.c.itemconfig(items["line2"],
                                  text=f"预警线 {warn_at}%", fill=t["text3"])
            self._render_chg(items, d.get("delta"), low, t, unit="")
            self._set_dot(items["dot"], "err" if low else "ok")
        elif err:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text=err, fill=t["err"])
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "err")
        else:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text="配额加载中…", fill=t["text3"])
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "warn")

    def _render_volc(self, pid, items, d, threshold, t):
        bal, err_b, usage, err_u = (d.get("balance"), d.get("err_b"),
                                    d.get("usage"), d.get("err_u"))
        if bal:
            try:
                arrears = float(bal.get("arrears") or 0)
            except (TypeError, ValueError):
                arrears = 0.0
            main = balance_main_value("volc", bal)
            low = (threshold > 0 and main is not None and main < threshold) \
                or arrears > 0
            self._set_warn(pid, low)
            self.c.itemconfig(
                items["bal"],
                text=f"¥{fmt_money(main if main is not None else 0)}",
                fill=t["err"] if low else t["text"])
            if arrears > 0:
                self.c.itemconfig(
                    items["bal"],
                    text=f"¥{fmt_money(main)}  欠费 ¥{fmt_money(arrears)}")
            self._render_chg(items, d.get("delta"), low, t)
            self._set_dot(items["dot"], "err" if low else "ok")
        elif err_b:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text=err_b, fill=t["err"])
            self.c.itemconfig(items["line2"], text="方舟用量：凭证无效，暂不可查",
                              fill=t["text3"])
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "err")
        else:
            self._set_warn(pid, False)
            self.c.itemconfig(items["bal"], text="", fill=t["text"])
            self.c.itemconfig(items["line1"], text="余额加载中…", fill=t["text3"])
            self._render_chg(items, None, False, t)
            self._set_dot(items["dot"], "warn")
        if bal:
            if usage:
                self.c.itemconfig(
                    items["line1"],
                    text=f"今日 {fmt_tokens(usage['total'])} tokens"
                         f"（↑{fmt_tokens(usage['input'])} "
                         f"↓{fmt_tokens(usage['output'])}）",
                    fill=t["text2"])
                self.c.itemconfig(
                    items["line2"],
                    text=f"现金 ¥{fmt_money(bal.get('cash', 0))} · "
                         f"信控 ¥{fmt_money(bal.get('credit', 0))} · "
                         f"请求 {usage['req']} 次",
                    fill=t["text3"])
            else:
                self.c.itemconfig(
                    items["line1"],
                    text=f"现金 ¥{fmt_money(bal.get('cash', 0))} · "
                         f"信控 ¥{fmt_money(bal.get('credit', 0))}",
                    fill=t["text2"])
                if err_u:
                    self.c.itemconfig(items["line2"], text=f"方舟用量：{err_u}",
                                      fill=t["err"])
                else:
                    self.c.itemconfig(items["line2"], text="方舟用量加载中…",
                                      fill=t["text3"])

    def _quit(self):
        try:
            if self.hotkeys:
                self.hotkeys.detach()
            self.worker.stop()
            self.cfg["window_x"] = self.root.winfo_x()
            self.cfg["window_y"] = self.root.winfo_y()
            save_config(self.cfg)
        except Exception:
            pass
        try:
            if self._balloon:
                self._balloon.close()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()


def _focus_existing_window():
    """已有实例运行时，把它的主窗口调到最前（按进程可执行文件精确匹配，不靠窗口标题）。"""
    try:
        import ctypes
        from ctypes import wintypes as wt
        u = ctypes.windll.user32
        k32 = ctypes.windll.kernel32
        # 64 位下必须显式声明原型，否则句柄被按 32 位截断、并泄漏句柄
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.QueryFullProcessImageNameW.restype = wt.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        me = os.getpid()
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

        def exe_name_of(pid):
            h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(512)
                n = wt.DWORD(512)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    return os.path.basename(buf.value).lower()
                return ""
            finally:
                k32.CloseHandle(h)

        def cb(hwnd, lp):
            pid = wt.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value != me and u.IsWindowVisible(hwnd):
                r = wt.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                ww, wh = r.right - r.left, r.bottom - r.top
                if 200 < ww < 700 and 150 < wh < 900 \
                        and exe_name_of(pid.value) == "deskpulse.exe":
                    found.append(hwnd)
            return True

        u.EnumWindows(WNDENUMPROC(cb), 0)
        for hw in found:
            u.ShowWindow(hw, 5)          # SW_SHOW
            u.BringWindowToTop(hw)
            u.SetForegroundWindow(hw)
        return bool(found)
    except Exception:
        log_exception("唤起已有实例失败")


def _single_instance():
    """单实例判定：占用固定本地端口。

    相比命名互斥量（ctypes 下 GetLastError 取值不可靠，会漏判导致多个实例并存、
    CPU 成倍叠加），端口占用由系统在进程退出时自动释放，既可靠又不会留下残留状态。
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 52381))
        s.listen(1)
        return True, s
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return False, None


def main():
    _install_crash_log()
    log_exception("启动 DeskPulse " + APP_VERSION)
    is_first, _guard = _single_instance()
    if not is_first:
        # 已有实例：把其窗口调到最前后安静退出，避免多实例互相覆盖配置、叠加 CPU。
        # 注意：onefile 打包时子进程启动后立刻退出，会让引导父进程卡在等待里空转，
        # 因此这里先给引导进程留出完成握手的时间，再正常退出（sys.exit 而非 os._exit）。
        log_exception("检测到已有实例，退出")
        for _ in range(4):
            time.sleep(1.5)
            if _focus_existing_window():
                log_exception("已有实例已唤起，退出")
                sys.exit(0)
        # 6 秒内都找不到旧实例的窗口（旧版残留 / 卡死进程占着端口）：
        # 静默退出会让用户以为程序坏了，这里改为继续启动新实例。
        log_exception("已有实例无窗口，改为启动新实例")
    try:
        Widget().run()
    except Exception as e:
        log_exception(f"程序异常: {e}")
        raise


if __name__ == "__main__":
    main()
