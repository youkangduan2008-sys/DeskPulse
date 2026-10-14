"""测量 DeskPulse.exe 的实际 CPU 占用（GetProcessTimes 采样，无需 GUI）。

用法：python tools/_cpumeter.py [秒数]
"""
import os
import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import time

SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 12
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(_BASE, "DeskPulse.exe")

k32 = ctypes.windll.kernel32
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]


def ft_to_s(ft):
    return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7


def pid_by_name():
    out = subprocess.check_output(
        ['tasklist', '/FI', 'IMAGENAME eq DeskPulse.exe', '/FO', 'CSV']).decode('gbk', 'ignore')
    pids = [int(l.split('","')[1]) for l in out.strip().splitlines()[1:] if l]
    return pids


pids = pid_by_name()
if not pids:
    print("DeskPulse.exe 未运行")
    sys.exit(1)
print("DeskPulse 进程:", pids)

handles = []
for pid in pids:
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if h:
        handles.append((pid, h))


def sample():
    total, mem = 0.0, 0
    for pid, h in handles:
        c, e, kt, ut = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                               ctypes.byref(kt), ctypes.byref(ut)):
            total += ft_to_s(kt) + ft_to_s(ut)
    return total


t0_cpu = sample()
t0 = time.time()
time.sleep(SECONDS)
t1_cpu = sample()
wall = time.time() - t0
print(f"采样 {wall:.1f}s：进程合计 CPU {t1_cpu - t0_cpu:.2f}s "
      f"→ 单核占用 {(t1_cpu - t0_cpu) / wall * 100:.1f}%")
