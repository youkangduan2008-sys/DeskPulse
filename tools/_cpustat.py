"""DeskPulse CPU 测量：手动泵事件循环，避免依赖 mainloop。

用法：python tools/_cpustat.py [秒数] [motion|idle]
"""
import os
import sys
import importlib.util
import time

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
MODE = sys.argv[2] if len(sys.argv) > 2 else "idle"

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dw", os.path.join(_BASE, "desktop_widget.py"))
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

app = dw.Widget()
root, cv = app.root, app.c
root.update()
print(f"开始取样：{SECONDS}s，模式={MODE}", flush=True)

pts = [(200, 20), (240, 26), (100, 60), (230, 60), (150, 100),
       (150, 150), (150, 200), (60, 250), (250, 90)]
t0 = time.time()
cpu0 = time.process_time()
i = 0
while time.time() - t0 < SECONDS:
    if MODE == "motion":
        x, y = pts[i % len(pts)]
        try:
            cv.event_generate("<Motion>", x=int(x * app.scale), y=int(y * app.scale))
        except Exception as e:
            print("Motion 异常:", e, flush=True)
    i += 1
    root.update()
    time.sleep(0.02)
wall = time.time() - t0
cpu = time.process_time() - cpu0
print(f"循环次数: {i}")
print(f"CPU 时间: {cpu:.2f}s / 墙钟: {wall:.2f}s = {cpu / wall * 100:.1f}%（含本脚本自身的 update 开销）")
sys.stdout.flush()

import os
os._exit(0)
