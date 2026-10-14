"""DeskPulse 布局探针：打印关键元素真实包围盒，用于无读图能力时的几何校验。"""
import os
import sys
import importlib.util

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("dw", os.path.join(_BASE, "desktop_widget.py"))
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

app = dw.Widget()
root = app.root
root.update_idletasks()
root.update()


def bbox(tag):
    items = app.c.find_withtag(tag)
    if not items:
        return None
    boxes = [app.c.bbox(i) for i in items if app.c.bbox(i)]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


print("画布逻辑尺寸:", app.c.winfo_width(), "x", app.c.winfo_height())
print("缩放:", app.scale, " UI缩放:", app.ui_scale)
print()
print("--- 顶部按钮（逻辑坐标, 未乘缩放）---")
btn_boxes = []
for tag, label, tip, _f in app.BTN_SPECS:
    b = bbox(tag)
    btn_boxes.append((tag, b))
    print(f"{label:>4} {tag:<12} {b}")
allbtn = (min(b[0] for _, b in btn_boxes if b), min(b[1] for _, b in btn_boxes if b),
          max(b[2] for _, b in btn_boxes if b), max(b[3] for _, b in btn_boxes if b))
print("按钮簇整体:", allbtn)
print()
print("--- 头部元素 ---")
head = {}
for key in ("clock", "date", "lunar", "temp", "city", "wdetail", "wday"):
    item = app.items.get(key)
    b = app.c.bbox(item) if item else None
    head[key] = b
    print(f"{key:<9}", b)
print()
clk = head["clock"]
if clk:
    print("时钟与按钮簇是否重叠:",
          not (clk[2] < allbtn[0] or clk[0] > allbtn[2]
               or clk[3] < allbtn[1] or clk[1] > allbtn[3]))
ml = head["lunar"] or head["date"]
c2 = head["city"]
if ml and c2:
    print("左栏日期/农历与右栏天气是否重叠:",
          not (ml[2] < c2[0] or ml[0] > c2[2]
               or ml[3] < c2[1] or ml[1] > c2[3]))
    print("  左栏右边界:", ml[2], " 右栏左边界:", c2[0])
wd = head["wdetail"]
if c2 and wd:
    print("右栏文字是否越过分区线(168):", c2[0] < 168 or wd[0] < 168,
          f"(city左={c2[0]}, wdetail左={wd[0]})")
print()
print("--- 左上角状态行 ---")
status_boxes = []
for tag in ("status_dot", "foot_updated", "foot_hint"):
    b = bbox(tag)
    status_boxes.append(b)
    print(f"{tag:<14}", b)
sb = [b for b in status_boxes if b]
if sb:
    stx = (min(b[0] for b in sb), min(b[1] for b in sb),
           max(b[2] for b in sb), max(b[3] for b in sb))
    print("状态行整体:", stx)
    print("状态行与按钮簇重叠:", not (stx[2] < allbtn[0] or stx[0] > allbtn[2]
                                        or stx[3] < allbtn[1] or stx[1] > allbtn[3]))
    print("状态行与时钟重叠:", bool(clk and not (
        stx[2] < clk[0] or stx[0] > clk[2] or stx[3] < clk[1] or stx[1] > clk[3])))
print()
print("--- 底部留白 ---")
card_bottom = app.c.winfo_height() - 6 * app.scale
pad = card_bottom - app.foot_top * app.scale
print("卡片底边(y):", card_bottom, " 账户区底边:", app.foot_top * app.scale,
      " 底部留白(显示像素):", round(pad, 1), " 逻辑值:", round(pad / app.scale, 1))

print("--- 账户卡高度（应完全一致）---")
for p in app.cfg.get("profiles", []):
    h = dw.acc_block_height(p)
    print(f"  {p.get('name','')}: 逻辑高 {h}")
print()
print("--- 底栏 ---")
print("BOTTOM_PAD:", dw.BOTTOM_PAD, " 窗口高(逻辑):", app.c.winfo_height() / app.scale)
app.root.destroy()
