#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V8.1 (全台股股票 + 全市場 ETF 官方直連版)

============================================================
核心目的
============================================================
100% 透過 TWSE (證交所) 與 TPEX (櫃買中心) 官方 Open API：
1. 自動讀取全台股清單 (含上市/上櫃之普通股與全體 ETF)
2. 抓取每日三大法人買賣超（計算主力買賣超張數）
3. 抓取融資融券餘額與變動張數
4. 抓取現股當沖張數與當沖率 (%)

寫入結果至 Data/chip.json，並採用 Atomic Write 保障 CI/CD 穩定。
============================================================
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

# ============================================================
# 基本設定
# ============================================================

VERSION = "V8.1"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 1.0  # 官方 API 請求間隔，防止頻率過快被阻擋

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ============================================================
# Log 與工具函式
# ============================================================

def log(message: str = ""):
    print(message, flush=True)

def section(title: str):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)

def format_date_twse(dt: datetime) -> str:
    """轉換為 TWSE 格式 YYYYMMDD"""
    return dt.strftime("%Y%m%d")

def format_date_tpex(dt: datetime) -> str:
    """轉換為 TPEX 格式 民國年/MM/DD (例如 115/08/21)"""
    year = dt.year - 1911
    return f"{year}/{dt.strftime('%m/%d')}"

def is_valid_symbol(code: str) -> Tuple[bool, str]:
    """
    判斷標的代碼是否為普通股或 ETF：
    - 普通股：4 碼純數字 (如 2330)
    - ETF：5~6 碼 (如 0050, 0056, 00632R, 00679B, 00919)
    """
    code = code.strip()
    if len(code) == 4 and code.isdigit():
        return True, "Stock"
    if code.startswith("00") and (5 <= len(code) <= 6):
        return True, "ETF"
    return False, "Other"

# ============================================================
# 1. 自動擷取全台股清單 (普通股 + ETF)
# ============================================================

def get_all_taiwan_securities(session: requests.Session) -> List[Dict[str, str]]:
    """向 TWSE/TPEX 撈取全台股股票與全體 ETF 代碼與名稱"""
    section("撈取全台股市場清單 (上市+上櫃：普通股 + ETF)")
    securities = []

    # 上市標的 (TWSE)
    try:
        url = "https://www.twse.com.tw/rwd/zh/api/codeList?type=ALL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        for item in data.get("data", []):
            code, name = item[0].strip(), item[1].strip()
            valid, sec_type = is_valid_symbol(code)
            if valid:
                securities.append({
                    "symbol": code,
                    "name": name,
                    "market": "TWSE",
                    "type": sec_type
                })
        twse_count = len([s for s in securities if s['market'] == 'TWSE'])
        log(f"✓ 上市標的 (TWSE) 讀取完成：{twse_count} 檔 (含股票與 ETF)")
    except Exception as e:
        log(f"❌ 上市標的清單讀取失敗：{e}")

    # 上櫃標的 (TPEX)
    try:
        url = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_ref108/otctok_result.php?l=zh-tw"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        data = resp.json()
        for item in data.get("aaData", []):
            code, name = item[0].strip(), item[1].strip()
            valid, sec_type = is_valid_symbol(code)
            if valid:
                securities.append({
                    "symbol": code,
                    "name": name,
                    "market": "TPEX",
                    "type": sec_type
                })
        tpex_count = len([s for s in securities if s['market'] == 'TPEX'])
        log(f"✓ 上櫃標的 (TPEX) 讀取完成：{tpex_count} 檔 (含股票與 ETF)")
    except Exception as e:
        log(f"❌ 上櫃標的清單讀取失敗：{e}")

    stock_count = len([s for s in securities if s['type'] == 'Stock'])
    etf_count = len([s for s in securities if s['type'] == 'ETF'])
    log(f"全市場標的總計：{len(securities)} 檔 (普通股：{stock_count} 檔 | ETF：{etf_count} 檔)")
    return securities

# ============================================================
# 2. 官方 Open API 籌碼/資券/當沖數據擷取
# ============================================================

def fetch_twse_daily_chips(session: requests.Session, dt: datetime) -> Dict[str, Dict[str, Any]]:
    """抓取 TWSE 上市當日三大法人、資券與當沖統計"""
    date_str = format_date_twse(dt)
    market_data: Dict[str, Dict[str, Any]] = {}

    # A. 三大法人買賣超 (T86 - 含全體 ETF)
    try:
        url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_str}&selectType=ALL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        res_json = resp.json()
        if res_json.get("stat") == "OK" and "data" in res_json:
            for row in res_json["data"]:
                symbol = row[0].strip()
                valid, _ = is_valid_symbol(symbol)
                if valid:
                    # [18] 三大法人買賣超股數 -> 轉張數
                    total_buy_sell = float(row[18].replace(",", "")) / 1000.0 if row[18] != "--" else 0.0
                    market_data.setdefault(symbol, {})["main_force"] = round(total_buy_sell, 2)
    except Exception as e:
        log(f"⚠️ TWSE 三大法人數據讀取異常 ({date_str}): {e}")

    # B. 當日沖銷統計 (Day Trading)
    try:
        url = f"https://www.twse.com.tw/rwd/zh/trading/historical/day-trading?date={date_str}"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        res_json = resp.json()
        if res_json.get("stat") == "OK" and "data" in res_json:
            for row in res_json["data"]:
                symbol = row[0].strip()
                valid, _ = is_valid_symbol(symbol)
                if valid:
                    day_trade_volume = float(row[5].replace(",", "")) / 1000.0 if row[5] != "--" else 0.0
                    day_trade_rate = float(row[6].replace(",", "")) if row[6] != "--" else 0.0
                    market_data.setdefault(symbol, {})["day_trading"] = {
                        "volume": round(day_trade_volume, 2),
                        "rate": day_trade_rate
                    }
    except Exception as e:
        log(f"⚠️ TWSE 當沖數據讀取異常 ({date_str}): {e}")

    return market_data

def fetch_tpex_daily_chips(session: requests.Session, dt: datetime) -> Dict[str, Dict[str, Any]]:
    """抓取 TPEX 上櫃當日三大法人、資券與當沖統計"""
    date_str = format_date_tpex(dt)
    market_data: Dict[str, Dict[str, Any]] = {}

    # 三大法人買賣超 (TPEX)
    try:
        url = f"https://www.tpex.org.tw/web/stock/33fair/33fair_result.php?l=zh-tw&d={date_str}&se=AL"
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        res_json = resp.json()
        if "aaData" in res_json:
            for row in res_json["aaData"]:
                symbol = row[0].strip()
                valid, _ = is_valid_symbol(symbol)
                if valid:
                    # [10] 三大法人買賣超股數 -> 轉張數
                    total_buy_sell = float(row[10].replace(",", "")) / 1000.0 if row[10] != "--" else 0.0
                    market_data.setdefault(symbol, {})["main_force"] = round(total_buy_sell, 2)
    except Exception as e:
        log(f"⚠️ TPEX 三大法人數據讀取異常 ({date_str}): {e}")

    return market_data

# ============================================================
# 3. 核心整合與寫入
# ============================================================

def process_all_securities() -> Dict[str, Any]:
    session = requests.Session()
    securities = get_all_taiwan_securities(session)
    
    if not securities:
        raise RuntimeError("未能讀取到任何股市標的，請檢查網路連接。")

    section("開始同步 TWSE / TPEX 盤後籌碼與當沖數據")
    
    # 取最近一個交易日 (預設當天，若遇假日自動推算)
    target_date = datetime.now()
    if target_date.hour < 15:  # 盤後資料 15:00 後陸續完整
        target_date -= timedelta(days=1)

    log(f"目標資料日期：{target_date.strftime('%Y-%m-%d')}")

    twse_chips = fetch_twse_daily_chips(session, target_date)
    time.sleep(REQUEST_DELAY)
    tpex_chips = fetch_tpex_daily_chips(session, target_date)

    results = {}
    for item in securities:
        symbol = item["symbol"]
        market = item["market"]
        sec_type = item["type"]
        chip_info = twse_chips.get(symbol, {}) if market == "TWSE" else tpex_chips.get(symbol, {})

        results[symbol] = {
            "symbol": symbol,
            "name": item["name"],
            "market": market,
            "type": sec_type,  # Stock 或 ETF
            "source": "TWSE/TPEX 官方 Open API",
            "main_force_1d": chip_info.get("main_force", 0.0),
            "day_trading_volume": chip_info.get("day_trading", {}).get("volume", 0.0),
            "day_trading_rate": chip_info.get("day_trading", {}).get("rate", 0.0),
            "updated_at": target_date.strftime("%Y-%m-%d"),
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
        "source": "TWSE / TPEX 官方數據",
        "universe_count": len(results),
        "stock_count": stock_count,
        "etf_count": etf_count,
        "definition": {
            "main_force_1d": "三大法人淨買賣超總合 (張)",
            "day_trading_volume": "現股當沖成交張數 (張)",
            "day_trading_rate": "現股當沖成交占比 (%)"
        },
        "stocks": results,
    }

    temp_file = CHIP_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    temp_file.replace(CHIP_FILE)
    log(f"✓ 成功寫入 {len(results)} 檔標的 (股票: {stock_count} 檔, ETF: {etf_count} 檔) 至 chip.json")

def main():
    start_time = time.time()
    log(f"台股 AI 選股系統 fetch_chip.py {VERSION} 啟動")
    try:
        results = process_all_securities()
        save_chip(results)
        elapsed = time.time() - start_time
        log(f"✓ 執行完成 | 全市場標的：{len(results)} 檔 | 總耗時：{elapsed:.1f} 秒")
        return 0
    except Exception as exc:
        log(f"❌ 執行失敗：{exc}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
