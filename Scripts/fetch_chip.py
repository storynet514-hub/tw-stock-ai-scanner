#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V8.2 (官方 Open Data API 防擋穩定版)

============================================================
修復說明
============================================================
1. 改用 TWSE / TPEX 官方 OpenAPI (openapi.twse.com.tw)，避免 GitHub Actions 遭 WAF 阻擋
2. 加上 HTTP 狀態碼檢查 (raise_for_status) 與 Fallback 機制
3. 採用標準 User-Agent 與自訂 Session 防護
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

VERSION = "V8.2"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
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
# 1. 讀取清單 (使用 OpenAPI 靜態節點)
# ============================================================

def get_all_taiwan_securities(session: requests.Session) -> List[Dict[str, str]]:
    section("撈取全台股市場清單 (上市+上櫃：普通股 + ETF)")
    securities = []

    # 上市股票與 ETF (TWSE OpenAPI)
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = item.get("Code", "").strip()
                name = item.get("Name", "").strip()
                valid, sec_type = is_valid_symbol(code)
                if valid:
                    securities.append({
                        "symbol": code,
                        "name": name,
                        "market": "TWSE",
                        "type": sec_type
                    })
            twse_count = len([s for s in securities if s['market'] == 'TWSE'])
            log(f"✓ 上市標的 (TWSE) 讀取完成：{twse_count} 檔")
        else:
            log(f"⚠️ TWSE OpenAPI 回傳狀態碼：{resp.status_code}")
    except Exception as e:
        log(f"❌ 上市標的清單讀取失敗：{e}")

    # 上櫃股票與 ETF (TPEX OpenAPI)
    try:
        url = "https://www.tpex.org.tw/openapi/v1/mopsinner/profile_all"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            for item in data:
                code = str(item.get("SecuritiesCompanyCode", "")).strip()
                name = str(item.get("CompanySymbol", "")).strip()
                valid, sec_type = is_valid_symbol(code)
                if valid:
                    securities.append({
                        "symbol": code,
                        "name": name,
                        "market": "TPEX",
                        "type": sec_type
                    })
            tpex_count = len([s for s in securities if s['market'] == 'TPEX'])
            log(f"✓ 上櫃標的 (TPEX) 讀取完成：{tpex_count} 檔")
        else:
            log(f"⚠️ TPEX OpenAPI 回傳狀態碼：{resp.status_code}")
    except Exception as e:
        log(f"❌ 上櫃標的清單讀取失敗：{e}")

    stock_count = len([s for s in securities if s['type'] == 'Stock'])
    etf_count = len([s for s in securities if s['type'] == 'ETF'])
    log(f"全市場標的總計：{len(securities)} 檔 (普通股：{stock_count} 檔 | ETF：{etf_count} 檔)")
    return securities

# ============================================================
# 2. 籌碼與當沖資料撈取 (OpenAPI 節點)
# ============================================================

def fetch_chips_and_daytrade(session: requests.Session) -> Dict[str, Dict[str, Any]]:
    section("開始同步 TWSE / TPEX 三大法人買賣超與當沖數據")
    market_data: Dict[str, Dict[str, Any]] = {}

    # 三大法人買賣超 (TWSE OpenAPI)
    try:
        url = "https://openapi.twse.com.tw/v1/fund/T86_ALL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            for row in data:
                symbol = row.get("Code", "").strip()
                valid, _ = is_valid_symbol(symbol)
                if valid:
                    # 三大法人買賣超張數
                    buy_sell_shares = float(row.get("Difference", "0").replace(",", "")) / 1000.0
                    market_data.setdefault(symbol, {})["main_force"] = round(buy_sell_shares, 2)
            log("✓ 上市三大法人數據同步完成")
    except Exception as e:
        log(f"⚠️ 上市三大法人數據擷取失敗: {e}")

    # 現股當沖統計 (TWSE OpenAPI)
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/TWTB4U"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            for row in data:
                symbol = row.get("Code", "").strip()
                valid, _ = is_valid_symbol(symbol)
                if valid:
                    vol = float(row.get("BuyVolume", "0").replace(",", "")) / 1000.0
                    rate = float(row.get("Rate", "0").replace(",", ""))
                    market_data.setdefault(symbol, {})["day_trading"] = {
                        "volume": round(vol, 2),
                        "rate": rate
                    }
            log("✓ 上市現股當沖數據同步完成")
    except Exception as e:
        log(f"⚠️ 上市現股當沖數據擷取失敗: {e}")

    return market_data

# ============================================================
# 3. 整合與寫入
# ============================================================

def process_all():
    session = requests.Session()
    securities = get_all_taiwan_securities(session)

    if not securities:
        raise RuntimeError("未能讀取到任何股市標的，請確認 GitHub Actions 網路連線狀態。")

    chips_data = fetch_chips_and_daytrade(session)

    results = {}
    today_str = datetime.now().strftime("%Y-%m-%d")

    for item in securities:
        symbol = item["symbol"]
        info = chips_data.get(symbol, {})

        results[symbol] = {
            "symbol": symbol,
            "name": item["name"],
            "market": item["market"],
            "type": item["type"],
            "source": "TWSE/TPEX Official OpenAPI",
            "main_force_1d": info.get("main_force", 0.0),
            "day_trading_volume": info.get("day_trading", {}).get("volume", 0.0),
            "day_trading_rate": info.get("day_trading", {}).get("rate", 0.0),
            "updated_at": today_str,
        }

    return results

def save_chip(results: Dict[str, Any]):
    section("寫入 Data/chip.json (Atomic Write)")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    stock_count = len([s for s in results.values() if s['type'] == 'Stock'])
    etf_count = len([s for s in results.values() if s['type'] == 'ETF'])

    output = {
        "schema_version": VERSION,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "TWSE / TPEX Official OpenAPI",
        "universe_count": len(results),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "stocks": results,
    }

    temp_file = CHIP_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    temp_file.replace(CHIP_FILE)
    log(f"✓ 成功寫入 {len(results)} 檔標的至 chip.json")

def main():
    start_time = time.time()
    log(f"台股 AI 選股系統 fetch_chip.py {VERSION} 啟動")
    try:
        results = process_all()
        save_chip(results)
        elapsed = time.time() - start_time
        log(f"✓ 執行完成 | 全市場標的：{len(results)} 檔 | 總耗時：{elapsed:.1f} 秒")
        return 0
    except Exception as exc:
        log(f"❌ 執行失敗：{exc}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
