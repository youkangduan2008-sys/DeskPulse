# DeskPulse 外链实测工具（内部调试用）
"""逐个实测各平台「取 Key / 充值」链接：状态码 + 跳转后的最终地址 + 正文长度。

用法：
  python tools/_linkcheck.py            # 全部平台
  python tools/_linkcheck.py kimi       # 只看匹配到的平台

为什么要跑：控制台改版、域名迁移都会让深链接**静默失效**。实测抓到过两处：
  - 硅基流动的 /i/account/ak 会被丢掉深路径，登录后只回到首页（redirect 参数里没有该路径）
  - Kimi 的 platform.moonshot.cn 已 302 迁到 platform.kimi.com

注意：DeepSeek / 智谱 / OpenRouter 的控制台是单页应用，伪路径与真实路径返回的页面
      外壳完全一致（正文长度相同），状态码无法判定深链接是否有效——这类只能人工点开
      确认，脚本会标注「SPA，无法判定」。
"""
import importlib.util
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "dw", os.path.join(os.path.dirname(HERE), "desktop_widget.py"))
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)

# 单页应用：任意路径都返回同一个外壳，状态码判不出深链接是否有效
SPA = ("deepseek", "zhipu", "openrouter")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DeskPulse-LinkCheck"}


def probe(url):
    """返回 (状态码, 最终地址, 正文字节数, 错误名)。"""
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read(400000).decode("utf-8", "ignore")
            return r.status, r.geturl(), len(body), ""
    except urllib.error.HTTPError as e:
        return e.code, url, 0, "HTTPError"
    except Exception as e:                      # 证书、超时、DNS 都会落到这里
        return None, url, 0, type(e).__name__


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else ""
    for ptype, plat in dw.PLATFORMS.items():
        label = plat.get("label", ptype)
        if kw and kw.lower() not in (ptype + label).lower():
            continue
        print("\n=== %s (%s) ===" % (label, ptype))
        for kind, url in (("取 Key", plat.get("keys_url")),
                          ("充值", plat.get("topup_url"))):
            if not url:
                continue
            code, final, size, err = probe(url)
            note = ""
            if code is None:
                note = "  ← 探测失败（%s），可能是本机网络/证书问题，不代表链接失效" % err
            elif ptype in SPA:
                note = "  ← SPA，无法判定，需人工点开确认"
            elif final.rstrip("/") != url.rstrip("/"):
                note = "  ← 发生了跳转，核对最终地址"
            print("[%s] HTTP %s 正文 %s 字节%s" % (kind, code, size, note))
            print("     %s" % url)
            if final.rstrip("/") != url.rstrip("/"):
                print("     -> %s" % final)
    print("\n提示：状态码只能证明「可达」，证明不了「点进去就是那一页」。")


if __name__ == "__main__":
    main()
