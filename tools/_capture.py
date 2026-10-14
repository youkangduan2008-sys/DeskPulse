# DeskPulse 窗口截图工具（内部调试用）
import ctypes, ctypes.wintypes as wt, subprocess, sys, time

u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32

def find_widget():
    out = subprocess.check_output(
        ['tasklist', '/FI', 'IMAGENAME eq DeskPulse.exe', '/FO', 'CSV']
    ).decode('gbk', 'ignore')
    pids = [int(l.split('","')[1]) for l in out.strip().splitlines()[1:] if l]
    hits = []
    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, lp):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(hwnd):
            r = wt.RECT()
            u32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 50:
                hits.append((hwnd, r.left, r.top, r.right, r.bottom))
        return True
    u32.EnumWindows(cb, 0)
    return hits

def shot(hwnd, w, h, out_path):
    hdc_scr = u32.GetDC(0)
    hdc_mem = g32.CreateCompatibleDC(hdc_scr)
    hbmp = g32.CreateCompatibleBitmap(hdc_scr, w, h)
    g32.SelectObject(hdc_mem, hbmp)
    u32.PrintWindow(hwnd, hdc_mem, 3)
    class BMI(ctypes.Structure):
        _fields_ = [('biSize', wt.DWORD), ('biWidth', wt.LONG), ('biHeight', wt.LONG),
                    ('biPlanes', wt.WORD), ('biBitCount', wt.WORD), ('biCompression', wt.DWORD),
                    ('biSizeImage', wt.DWORD), ('biXPPM', wt.LONG), ('biYPPM', wt.LONG),
                    ('biClrUsed', wt.DWORD), ('biClrImportant', wt.DWORD)]
    bmi = BMI(); bmi.biSize = ctypes.sizeof(BMI)
    bmi.biWidth = w; bmi.biHeight = -h; bmi.biPlanes = 1; bmi.biBitCount = 32
    buf = (ctypes.c_char * (w * h * 4))()
    g32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    from PIL import Image
    img = Image.frombuffer('RGBA', (w, h), bytes(buf), 'raw', 'BGRA', 0, 1).convert('RGB')
    img.save(out_path)
    g32.DeleteObject(hbmp); g32.DeleteDC(hdc_mem); u32.ReleaseDC(0, hdc_scr)

if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'widget-shot.png'
    hits = find_widget()
    print('windows:', hits)
    if hits:
        hwnd, l, t, r, b = hits[0]
        shot(hwnd, r - l, b - t, out)
        print('saved', out)
