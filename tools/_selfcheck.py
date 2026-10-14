"""DeskPulse 自渲染自检：进程内创建界面 → 自身窗口截图 → 像素分析（无需外部进程，无需读图）。

分析点：
1. 右上工具条是否渲染为一整组（单条连续色块，而非 4 个孤立圆点）
2. 左上状态行是否存在（状态点 + 文本）
3. 底部是否还有空白带

依赖：Pillow（仅本调试脚本需要）。用法：python tools/_selfcheck.py [输出png路径]
"""
import os
import importlib.util
import ctypes
import ctypes.wintypes as wt
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dw", os.path.join(_BASE, "desktop_widget.py"))
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

app = dw.Widget()
root = app.root
for _ in range(6):
    root.update_idletasks()
    root.update()

hwnd = root.winfo_id()
u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
r = wt.RECT()
u32.GetWindowRect(hwnd, ctypes.byref(r))
w, h = r.right - r.left, r.bottom - r.top
print("窗口:", (r.left, r.top, w, h))

hdc_scr = u32.GetDC(0)
hdc_mem = g32.CreateCompatibleDC(hdc_scr)
hbmp = g32.CreateCompatibleBitmap(hdc_scr, w, h)
g32.SelectObject(hdc_mem, hbmp)
u32.PrintWindow(hwnd, hdc_mem, 3)


class BMI(ctypes.Structure):
    _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                ('biPlanes', wt.WORD), ('biBitCount', wt.WORD),
                ('biCompression', wt.DWORD), ('biSizeImage', wt.DWORD),
                ('biXPPM', wt.LONG), ('biYPPM', wt.LONG), ('biClrUsed', wt.DWORD),
                ('biClrImportant', wt.DWORD)]


bmi = BMI()
bmi.biSize = ctypes.sizeof(BMI)
bmi.biWidth = w
bmi.biHeight = -h
bmi.biPlanes = 1
bmi.biBitCount = 32
buf = (ctypes.c_char * (w * h * 4))()
g32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
from PIL import Image
img = Image.frombuffer('RGBA', (w, h), bytes(buf), 'raw', 'BGRA', 0, 1).convert('RGB')
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_BASE, "self-render.png")
img.save(out)
g32.DeleteObject(hbmp)
g32.DeleteDC(hdc_mem)
u32.ReleaseDC(0, hdc_scr)
print("已保存:", out, img.size)

bg = img.getpixel((6, 70))
print("卡片背景色:", bg)


def runs_at(y, x0=8, x1=None, tol=20):
    x1 = x1 or img.size[0] - 6
    out, start = [], None
    for x in range(x0, x1):
        diff = sum(abs(a - c) for a, c in zip(img.getpixel((x, y)), bg)) > tol
        if diff and start is None:
            start = x
        elif not diff and start is not None:
            if x - start > 2:
                out.append((start, x - start))
            start = None
    if start is not None:
        out.append((start, x1 - start))
    return out


print()
print("--- 顶部一行（工具条区）非背景色块 ---")
for y in (10, 14, 18, 26, 34):
    print(f"y={y:>2}:", runs_at(y, 140))
print()
print("--- 左上角状态行（y=16..30）---")
for y in (18, 24, 30):
    print(f"y={y:>2}:", runs_at(y, 8, 160))
print()
print("--- 底部（最后 40px 是否空）---")
for y in range(h - 34, h - 6, 8):
    print(f"y={y:>3}:", runs_at(y, 8, w - 6))

print()
print("=== 自检结论 ===")
sys.stdout.flush()

app.root.destroy()
import os
os._exit(0)
