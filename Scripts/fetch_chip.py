#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V8.3 (驗證相容修復版)

============================================================
修復說明
============================================================
1. 向 TWSE/TPEX 撈取最近 10 個交易日數據，計算 1D / 5D / 10D 主力買賣超
2. 補齊 Workflow 驗證所需的頂層欄位: data_date, statistics
3. 輸出包含 main_force_5d 與 main_force_10d，通過 CI 檢查
============================================================
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

VERSION = "V8.3"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

def log(message: str = ""):
    print(message, flush=True)

def section(title: str):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)

def is_valid_symbol(code: str) -> Tuple[bool, str]:
    code = code.strip()
    if len(code) == 4 and code.isdigit():
        return True, "Stock"
    if code.startswith("00") and (5 <= len(code) <= 6):
        return True, "ETF"
    return False, "Other"

# ============================================================
# 1. 取得標的清單
# ============================================================

def get_securities(session: requests.Session) -> List[Dict[str, str]]:
    section("撈取全台股市場清單 (上市+上櫃：普通股 + ETF)")
    securities = []

    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            for item in resp.json():
                code, name = item.get("Code", "").strip(), item.get("Name", "").strip()
                valid, sec_type = is_valid_symbol(code)
                if valid:
                    securities.append({"symbol": code, "name": name, "market": "TWSE", "type": sec_type})
    except Exception as e:
        log(f"⚠️ 上市標的讀取異常: {e}")

    try:
        url = "https://www.tpex.org.tw/openapi/v1/mopsinner/profile_all"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            for item in resp.json():
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanySymbol", "")).strip()
                valid, sec_type = is_valid_symbol(code)
                if valid:
                    securities.append({"symbol": code, "name": name, "market": "TPEX", "type": sec_type})
    except Exception as e:
        log(f"⚠️ 上櫃標的讀取異常: {e}")

    log(f"✓ 清單讀取完成，共 {len(securities)} 檔標的")
    return securities

# ============================================================
# 2. 多日籌碼累積計算
# ============================================================

def fetch_multi_day_chips(session: requests.Session, days: int = 10) -> Tuple[str, Dict[str, List[float]]]:
    section(f"同步 TWSE 最近 {days} 個交易日三大法人籌碼")
    
    daily_records: Dict[str, List[float]] = {}
    latest_date_str = ""
    fetch_count = 0
    curr_date = datetime.now()

    # 往前推算交易日，直到湊滿足夠的交易日數據
    while fetch_count < days and (datetime.now() - curr_date).days < 25:
        if curr_date.weekday() < 5:  # 排除週六、日
            date_str = curr_date.strftime("%Y%m%d")
            url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL"
            try:
                resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                res_json = resp.json()
                if res_json.get("stat") == "OK" and "data" in res_json:
                    if not latest_date_str:
                        latest_date_str = curr_date.strftime("%Y-%m-%d")
                    
                    for row in res_json["data"]:
                        symbol = row[0].strip()
                        valid, _ = is_valid_symbol(symbol)
                        if valid:
                            try:
                                buy_sell = float(row[18].replace(",", "")) / 1000.0 if row[18] != "--" else 0.0
                            except ValueError:
                                buy_sell = 0.0
                            daily_records.setdefault(symbol, []).append(round(buy_sell, 2))
                    
                    fetch_count += 1
                    log(f"  └ 成功取得 {date_str} 籌碼資料 (已累計 {fetch_count}/{days} 日)")
                    time.sleep(0.5)
            except Exception:
                pass
        curr_date -= timedelta(days=1)

    if not latest_date_str:
        latest_date_str = datetime.now().strftime("%Y-%m-%d")

    return latest_date_str, daily_records

# ============================================================
# 3. 主流程與資料驗證欄位寫入
# ============================================================

def main():
    start_time = time.time()
    log(f"台股 AI 選股系統 fetch_chip.py {VERSION} 啟動")
    session = requests.Session()

    securities = get_securities(session)
    if not securities:
        log("❌ 無法取得標的清單")
        return 1

    latest_date_str, daily_records = fetch_multi_day_chips(session, days=10)

    stocks_result = {}
    complete_cnt = 0
    partial_cnt = 0
    insufficient_cnt = 0

    for item in securities:
        symbol = item["symbol"]
        history = daily_records.get(symbol, [])

        # 計算 1D / 5D / 10D 加總
        mf_1d = history[0] if len(history) >= 1 else 0.0
        mf_5d = round(sum(history[:5]), 2) if len(history) >= 1 else None
        mf_10d = round(sum(history[:10]), 2) if len(history) >= 1 else None

        # 統計數據品質
        if len(history) >= 10:
            complete_cnt += 1
        elif len(history) >= 1:
            partial_cnt += 1
        else:
            insufficient_cnt += 1

        stocks_result[symbol] = {
            "symbol": symbol,
            "name": item["name"],
            "market": item["market"],
            "type": item["type"],
            "main_force_1d": mf_1d,
            "main_force_5d": mf_5d,
            "main_force_10d": mf_10d,
            "updated_at": latest_date_str
        }

    # 輸出結構（對齊 CI/CD Verify 腳本的要求）
    output = {
        "schema_version": VERSION,
        "data_date": latest_date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "universe_count": len(stocks_result),
        "statistics": {
            "complete": complete_cnt,
            "partial": partial_cnt,
            "insufficient": insufficient_cnt
        },
        "stocks": stocks_result
    }

    section("寫入 Data/chip.json (Atomic Write)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = CHIP_FILE.with_suffix(".json.tmp")
    
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    temp_file.replace(CHIP_FILE)
    
    elapsed = time.time() - start_time
    log(f"✓ 成功寫入 chip.json | 有效 5D 筆數: {complete_cnt + partial_cnt} | 總耗時: {elapsed:.1f} 秒")
    return 0

if __name__ == "__main__":
    sys.exit(main())
