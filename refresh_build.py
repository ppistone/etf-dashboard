#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金融仪表盘 · 自动更新管线
---------------------------
1) refresh(): 从原始共享源（sharecode）拉取最新 index.html + 15 个 CSV，存入本地。
   - 任一文件下载失败则保留上一份（保证 build 永远可用）。
2) build():   读取本地 chart.umd.min.js + data/*.csv，重新生成自包含 index.html
   （内联数据 + 内联 Chart.js + fetch 拦截器 + 移除埋点），与原版外观/交互一致。

用法:
    python refresh_build.py          # 先刷新再重建
    python refresh_build.py --refresh # 仅刷新
    python refresh_build.py --build   # 仅重建（用已有本地数据）
"""
import json
import os
import re
import subprocess
import sys
import time

SRC_BASE = "https://etf-dashboard-34244.app.workbuddy.host"
SHARECODE = "FYA2X3V333"

CSV_FILES = [
    "hs300_etf_shares.csv", "margin_data.csv", "aluminum.csv", "coal_data.csv",
    "factory_inventory.csv", "aluminum_warrants.csv", "inventory.csv", "copper.csv",
    "copper_inventory.csv", "lithium.csv", "gold_reserve.csv", "scfi.csv",
    "lithium_warrants.csv", "yangtze_power.csv", "three_gorges_level.csv",
]

FOLDER = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(FOLDER, "data")
CHART_JS = os.path.join(FOLDER, "chart.umd.min.js")
SRC_HTML = os.path.join(FOLDER, "src.html")
OUT_HTML = os.path.join(FOLDER, "index.html")
LAST_UPDATE = os.path.join(FOLDER, "last_update.txt")


def _http_get(url):
    """优先 curl（沙箱内已验证可达），失败回退 urllib。"""
    try:
        r = subprocess.run(
            ["curl", "-sSL", "--max-time", "30", url],
            capture_output=True, text=True, timeout=40,
        )
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _is_blocked(txt):
    """源站对未授权访问返回 403 + 'Forbidden · 数据仅限...'，必须识别并跳过，
    否则会把拦截文案当数据写入，导致页面'加载数据失败'。"""
    if not txt:
        return False
    markers = ("Forbidden", "数据仅限", "403 Forbidden", "Access Denied")
    return any(m in txt for m in markers)


def refresh():
    os.makedirs(DATA_DIR, exist_ok=True)
    ts = int(time.time() * 1000)

    # 1) 源页面 HTML（结构若变化会自动跟随）
    try:
        html = _http_get(f"{SRC_BASE}/?sharecode={SHARECODE}")
        if _is_blocked(html):
            print("[refresh] src.html 被源站拦截(403)，保留旧版")
        elif len(html) > 50000:
            with open(SRC_HTML, "w", encoding="utf-8") as f:
                f.write(html)
            print("[refresh] src.html 更新 OK (%d bytes)" % len(html))
        else:
            print("[refresh] src.html 过小，跳过（保留旧版）")
    except Exception as e:
        print("[refresh] src.html 拉取失败: %s（保留旧版）" % e)

    # 2) 15 个数据 CSV
    ok = 0
    for fn in CSV_FILES:
        url = f"{SRC_BASE}/{fn}?sharecode={SHARECODE}&_={ts}"
        try:
            txt = _http_get(url)
            if _is_blocked(txt):
                print("[refresh] %-26s 被源站拦截(403/Forbidden)，保留上一份成功数据" % fn)
            elif len(txt) > 10:
                with open(os.path.join(DATA_DIR, fn), "w", encoding="utf-8") as f:
                    f.write(txt)
                ok += 1
                print("[refresh] %-26s OK (%d bytes)" % (fn, len(txt)))
            else:
                print("[refresh] %-26s 内容为空，跳过" % fn)
        except Exception as e:
            print("[refresh] %-26s 拉取失败: %s（保留旧版）" % (fn, e))

    with open(LAST_UPDATE, "w", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) + "\n")
        f.write("csv_ok=%d/%d\n" % (ok, len(CSV_FILES)))


def build():
    if not os.path.exists(SRC_HTML):
        raise SystemExit("[build] 缺少 src.html，请先运行 refresh")

    with open(SRC_HTML, encoding="utf-8") as f:
        html = f.read()

    csv_data = {}
    for fn in CSV_FILES:
        p = os.path.join(DATA_DIR, fn)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                csv_data[fn] = f.read().replace("\ufeff", "")
    if not csv_data:
        raise SystemExit("[build] data/ 目录无任何 CSV，终止")
    for fn, content in csv_data.items():
        if _is_blocked(content):
            raise SystemExit(
                "[build] 检测到 %s 含源站拦截内容(403/Forbidden)，终止以免污染页面；"
                "请先恢复 data/ 下真实数据再重建" % fn
            )

    # 内联数据 + fetch 拦截器
    data_obj = json.dumps(csv_data, ensure_ascii=False)
    data_js = "window.__CSV__ = " + data_obj + ";\n"
    shim = (
        "(function(){"
        "var _orig = window.fetch ? window.fetch.bind(window) : null;"
        "window.fetch = function(url, opts){"
        "  if (typeof url === 'string'){"
        "    var key = url.split('?')[0];"
        "    if (window.__CSV__ && Object.prototype.hasOwnProperty.call(window.__CSV__, key)){"
        "      return Promise.resolve(new Response(window.__CSV__[key], {status:200, headers:{'Content-Type':'text/csv'}}));"
        "    }"
        "  }"
        "  if (_orig) return _orig(url, opts);"
        "  return Promise.reject(new Error('network fetch unavailable'));"
        "};"
        "})();"
    )
    head_inject = "<script>\n" + data_js + shim + "\n</script>\n"

    # 内联 Chart.js
    with open(CHART_JS, encoding="utf-8") as f:
        cj = f.read()
    if "</script>" in cj:
        raise SystemExit("[build] chart.umd.min.js 含 </script>，不能内联")

    html = html.replace(
        'src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"',
        'src="chart.umd.min.js"',
    )
    html = re.sub(r'<!-- 访问计数.*?</script>\s*', '', html, flags=re.S)
    html = html.replace('src="chart.umd.min.js"', "<script>\n" + cj + "\n</script>")
    html = html.replace("</head>", head_inject + "</head>", 1)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("[build] index.html 生成 OK (%d bytes), CSV=%d" % (len(html), len(csv_data)))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--build":
        build()
    elif arg == "--refresh":
        refresh()
    else:
        # 原 sharecode 源已失效(返回 403), 先跑一遍(安全 no-op, 保留旧数据),
        # 再改用公网数据源刷新可映射的指标, 最后重建 index.html。
        refresh()
        try:
            from refresh_public import run_public_fetch
            run_public_fetch()
        except Exception as e:
            print("[main] 公网刷新调用失败: %s（保留上一份数据）" % e)
        build()
