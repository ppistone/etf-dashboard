#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金融仪表盘 · 公网数据源刷新管线（替代已失效的 sharecode 源）
--------------------------------------------------------------
源站 etf-dashboard-34244.app.workbuddy.host 现已对未授权访问返回 403，
无法再拉取 CSV。本脚本改用公开数据源(akshare)更新**可精确映射**的指标，
其余指标保留历史最后值(冻结)，绝不删除历史。

可更新(精确/近似映射):
  gold_reserve.csv      <- macro_china_foreign_exchange_gold (万盎司->吨)
  copper_inventory.csv  <- futures_inventory_em(沪铜)   [完全匹配]
  copper.csv            <- futures_main_sina(cu0) 主力合约 OHLC
  lithium.csv           <- futures_main_sina(lc0) 主力合约收盘
  aluminum.csv          <- futures_main_sina(al0) 主力合约 OHLC (al_* 部分; ao_* 冻结)

冻结(保留历史最后值): scfi / hs300_etf_shares / aluminum_warrants /
  lithium_warrants / factory_inventory / inventory / coal_data /
  three_gorges_level / yangtze_power / margin_data(单位歧义, 不臆造)

用法:
  python refresh_public.py            # 拉公网数据 + 合并 + 重建 index.html
  python refresh_public.py --fetch    # 仅拉取合并, 不重建
"""
import csv
import os
import subprocess
import sys
import akshare as ak

FOLDER = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(FOLDER, "data")


def merge_csv(filename, new_rows, date_col):
    """把 new_rows(同表头的 dict 列表) 合并进 data/<filename>，按 date_col 去重，
    保留全部历史行，新日期追加，原顺序的字段不被破坏。"""
    path = os.path.join(DATA_DIR, filename)
    fieldnames = None
    existing = {}
    order = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            r = csv.DictReader(f)
            fieldnames = r.fieldnames
            for row in r:
                k = row.get(date_col, "")
                if k:
                    existing[k] = row
                    order.append(k)
    if fieldnames is None:
        # 新文件: 用 new_rows 的键顺序
        fieldnames = list(new_rows[0].keys()) if new_rows else []
    # 应用新行
    added = 0
    for row in new_rows:
        k = row.get(date_col, "")
        if not k:
            continue
        merged = {c: row.get(c, "") for c in fieldnames}
        if k not in existing:
            added += 1
        existing[k] = merged
        if k not in order:
            order.append(k)
    # 写出(尽量按日期排序)
    def sortkey(k):
        return k
    out_order = sorted(order, key=sortkey)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for k in out_order:
            w.writerow({c: existing[k].get(c, "") for c in fieldnames})
    print("[merge] %-26s 现有%d行, 本次新增%d行" % (filename, len(order), added))


def last_values(filename):
    """返回该 CSV 最后一行(用于冻结子序列的延续, 如 ao_*)"""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else {}


def fetch_gold():
    df = ak.macro_china_foreign_exchange_gold()
    df = df.dropna(subset=["黄金储备"])
    rows = []
    prev = None
    last = None
    for _, r in df.iterrows():
        ym = str(r["统计时间"]).replace(".", "-")  # 2026.09 -> 2026-09
        if len(ym) < 7:
            continue
        oz = float(r["黄金储备"])            # 万盎司
        tons = round(oz * 0.311034768, 2)   # 万盎司 -> 吨
        chg = round(tons - prev, 2) if prev is not None else 0.0
        prev = tons
        last = {"date": ym, "gold_oz_wan": oz, "gold_tons": tons, "change_tons": chg}
        rows.append(last)
    return rows


def fetch_copper_inv():
    df = ak.futures_inventory_em(symbol="沪铜")
    rows = []
    for _, r in df.iterrows():
        d = str(r["日期"])
        rows.append({"date": d, "inventory": r["库存"], "change": ("" if str(r["增减"]) in ("nan", "") else r["增减"])})
    return rows


def fetch_futures_ohlc(symbol, cols):
    """通用: 期货主力合约日线 -> {date, *cols}"""
    df = ak.futures_main_sina(symbol=symbol)
    df = df.sort_values("日期").reset_index(drop=True)
    rows = []
    prev_close = None
    for _, r in df.iterrows():
        d = str(r["日期"])
        close = float(r["收盘价"])
        high = float(r["最高价"])
        low = float(r["最低价"])
        avg = round((high + low) / 2, 2)
        chg = round(close - prev_close, 2) if prev_close is not None else 0.0
        prev_close = close
        row = {"date": d}
        for c in cols:
            if c == "avg":
                row[c] = avg
            elif c == "min":
                row[c] = low
            elif c == "max":
                row[c] = high
            elif c == "change":
                row[c] = chg
        rows.append(row)
    return rows


def run_public_fetch():
    """拉取公网数据并合并进 data/ (不含重建)。返回 summary 列表。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    summary = []

    # 1) 黄金储备 (精确)
    try:
        rows = fetch_gold()
        merge_csv("gold_reserve.csv", rows, "date")
        summary.append("gold_reserve: 公网更新 %d 月" % len(rows))
    except Exception as e:
        summary.append("gold_reserve: 失败 %s" % e)

    # 2) 沪铜库存 (精确)
    try:
        rows = fetch_copper_inv()
        merge_csv("copper_inventory.csv", rows, "date")
        summary.append("copper_inventory: 公网更新 %d 日" % len(rows))
    except Exception as e:
        summary.append("copper_inventory: 失败 %s" % e)

    # 3) 铜现货 (期货主力近似)
    try:
        rows = fetch_futures_ohlc("cu0", ["avg", "min", "max", "change"])
        for r in rows:
            r["cu_avg"], r["cu_min"], r["cu_max"], r["cu_change"] = r.pop("avg"), r.pop("min"), r.pop("max"), r.pop("change")
        merge_csv("copper.csv", rows, "date")
        summary.append("copper: 公网更新 %d 日(期货主力)" % len(rows))
    except Exception as e:
        summary.append("copper: 失败 %s" % e)

    # 4) 锂价 (期货主力收盘)
    try:
        rows = fetch_futures_ohlc("lc0", ["avg"])
        for r in rows:
            r["li_price"] = r.pop("avg")
        merge_csv("lithium.csv", rows, "date")
        summary.append("lithium: 公网更新 %d 日(期货主力)" % len(rows))
    except Exception as e:
        summary.append("lithium: 失败 %s" % e)

    # 5) 铝现货 (期货主力; ao_* 冻结为历史最后值)
    try:
        rows = fetch_futures_ohlc("al0", ["avg", "min", "max", "change"])
        last = last_values("aluminum.csv")
        for r in rows:
            r["al_avg"], r["al_min"], r["al_max"], r["al_change"] = r.pop("avg"), r.pop("min"), r.pop("max"), r.pop("change")
            r["ao_avg"] = last.get("ao_avg", "")
            r["ao_min"] = last.get("ao_min", "")
            r["ao_max"] = last.get("ao_max", "")
            r["ao_change"] = last.get("ao_change", "")
        merge_csv("aluminum.csv", rows, "date")
        summary.append("aluminum: 公网更新 %d 日(al_* 期货主力; ao_* 冻结)" % len(rows))
    except Exception as e:
        summary.append("aluminum: 失败 %s" % e)

    print("\n=== 本次公网刷新汇总 ===")
    for s in summary:
        print(" -", s)
    print("\n[info] 以下指标无免费公网源, 已保留历史最后值(冻结):")
    print("       scfi, hs300_etf_shares, aluminum_warrants, lithium_warrants,")
    print("       factory_inventory, inventory, coal_data, three_gorges_level, yangtze_power, margin_data")
    return summary


def main():
    run_public_fetch()
    if "--fetch" not in sys.argv:
        print("\n[build] 调用 refresh_build.build() 重建自包含 index.html ...")
        sys.path.insert(0, FOLDER)
        import refresh_build
        refresh_build.build()
        print("[build] 完成")


if __name__ == "__main__":
    main()
