#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V7.0 (FinMind API 版)

============================================================
核心目的
============================================================
使用 FinMind API 取得台股籌碼買賣超資料（單位：張）。
正數：法人買超
負數：法人賣超

============================================================
重要定義與規則
============================================================
main_force_1d  : 最近 1 個交易日法人買賣超
main_force_5d  : 最近 5 個交易日「每日買賣超」加總
main_force_10d : 最近 10 個交易日「每日買賣超」加總
main_force_20d : 最近 20 個交易日「每日買賣超」加總

優點：
1. 使用標準 RESTful API，無須解析 HTML/Next.js JSON，極度穩定。
2. 不易觸發反爬蟲機制。
3. 自動計算外資、投信、自營商三大法人淨買賣超總和。
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

VERSION = "V7.0"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5
TARGET_HISTORY = 20

# FinMind API 端點 (免費版可直接使用，若有 Token 可填入 FINMIND_TOKEN)
FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = ""  # 若有申請 FinMind 官方免費/付費 Token 可填於此處

# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {"symbol": "2337", "name": "旺宏", "market": "TWSE"},
    {"symbol": "2426", "name": "鼎元", "market": "TWSE"},
    {"symbol": "2368", "name": "金像電", "market": "TWSE"},
    {"symbol": "3081", "name": "聯亞", "market": "TPEX"},
]

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

# ============================================================
# FinMind API 擷取核心
# ============================================================

def fetch_finmind_chip(session: requests.Session, symbol: str) -> List[Dict[str, Any]]:
    """
    從 FinMind API 抓取三大法人買賣超資料，並計算每日總買賣超（張數）
    """
    # 抓取過去 40 天以確保涵蓋足夠的 20 個交易日（避開假日與連假）
    start_date = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
    
    params = {
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": symbol,
        "start_date": start_date,
    }
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN

    resp = session.get(FINMIND_API_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    result = resp.json()

    if result.get("status") != 200 or "data" not in result:
        raise RuntimeError(f"FinMind API 回傳異常: {result.get('msg', '未知錯誤')}")

    raw_data = result["data"]
    if not raw_data:
        return []

    # 依日期將外資、投信、自營商買賣超進行加總（股數轉張數，除以 1000）
    daily_summary: Dict[str, float] = {}
    for item in raw_data:
        date_str = item.get("date", "").replace("-", "/")
        buy = item.get("buy", 0)
        sell = item.get("sell", 0)
        net_shares = buy - sell  # 淨買賣超股數

        if date_str:
            daily_summary[date_str] = daily_summary.get(date_str, 0.0) + (net_shares / 1000.0)

    # 轉為清單並依日期由新到舊排序
    history = [
        {"date": d, "main_force": round(val, 2)}
        for d, val in daily_summary.items()
    ]
    history.sort(key=lambda x: datetime.strptime(x["date"], "%Y/%m/%d"), reverse=True)

    return history[:TARGET_HISTORY]

# ============================================================
# 資料計算與驗證
# ============================================================

def calculate_periods(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = [float(r["main_force"]) for r in history if r.get("main_force") is not None]
    return {
        "main_force_1d": round(sum(values[:1]), 2) if len(values) >= 1 else None,
        "main_force_5d": round(sum(values[:5]), 2) if len(values) >= 5 else None,
        "main_force_10d": round(sum(values[:10]), 2) if len(values) >= 10 else None,
        "main_force_20d": round(sum(values[:20]), 2) if len(values) >= 20 else None,
        "history_count": len(values),
    }

def get_status(periods: Dict[str, Any]) -> str:
    if periods.get("main_force_20d") is not None:
        return "complete"
    if periods.get("main_force_10d") is not None:
        return "partial_20d"
    if periods.get("main_force_5d") is not None:
        return "partial_10d"
    if periods.get("main_force_1d") is not None:
        return "partial_5d"
    return "insufficient"

def fetch_stock(session: requests.Session, stock: Dict[str, str]) -> Dict[str, Any]:
    symbol, name = stock["symbol"], stock["name"]
    section(f"FinMind API 法人買賣超：{symbol} {name}")

    history = fetch_finmind_chip(session, symbol)
    log(f"✓ FinMind API 成功回傳有效交易日：{len(history)} 筆")

    periods = calculate_periods(history)
    status = get_status(periods)

    log(f"法人 1日：{periods['main_force_1d']} 張")
    log(f"法人 5日：{periods['main_force_5d']} 張")
    log(f"法人 10日：{periods['main_force_10d']} 張")
    log(f"法人 20日：{periods['main_force_20d']} 張")

    return {
        "symbol": symbol,
        "name": name,
        "market": stock["market"],
        "source": "FinMind API",
        "source_url": FINMIND_API_URL,
        "source_field": "三大法人買賣超加總",
        "main_force_1d": periods["main_force_1d"],
        "main_force_5d": periods["main_force_5d"],
        "main_force_10d": periods["main_force_10d"],
        "main_force_20d": periods["main_force_20d"],
        "history_count": len(history),
        "status": status,
        "history": history,
        "error": None,
    }

def build_error_record(stock: Dict[str, str], error: Exception) -> Dict[str, Any]:
    return {
        "symbol": stock["symbol"],
        "name": stock["name"],
        "market": stock["market"],
        "source": "FinMind API",
        "source_url": FINMIND_API_URL,
        "source_field": "三大法人買賣超加總",
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": 0,
        "status": "insufficient",
        "history": [],
        "error": str(error),
    }

# ============================================================
# 批次執行與驗證
# ============================================================

def fetch_all() -> Tuple[Dict[str, Any], int, int, int]:
    section("開始 FinMind API 籌碼資料更新")
    log("本版本為固定測試模式 (測試股票：2337 / 2426 / 2368 / 3081)")

    session = requests.Session()
    results = {}
    complete, partial, insufficient = 0, 0, 0
    total = len(TEST_STOCKS)

    for index, stock in enumerate(TEST_STOCKS, start=1):
        log(f"[{index}/{total}] {stock['symbol']} {stock['name']}")
        try:
            record = fetch_stock(session, stock)
            results[stock["symbol"]] = record

            if record["main_force_20d"] is not None:
                complete += 1
            elif record["main_force_10d"] is not None:
                partial += 1
            else:
                insufficient += 1

        except Exception as exc:
            log(f"❌ {stock['symbol']} 取得失敗：{exc}")
            results[stock["symbol"]] = build_error_record(stock, exc)
            insufficient += 1

        time.sleep(REQUEST_DELAY)

    return results, complete, partial, insufficient

def validate(results: Dict[str, Any]):
    section("最終資料驗證")

    if len(results) != len(TEST_STOCKS):
        raise RuntimeError("輸出股票數量錯誤")

    for stock in TEST_STOCKS:
        symbol = stock["symbol"]
        if symbol not in results:
            raise RuntimeError(f"缺少測試股票：{symbol}")

        record = results[symbol]
        history = record.get("history", [])

        if len(history) > TARGET_HISTORY:
            raise RuntimeError(f"{symbol} 超過 20 筆資料")

        periods = calculate_periods(history)
        for field in ["main_force_1d", "main_force_5d", "main_force_10d", "main_force_20d"]:
            actual = record.get(field)
            expected = periods.get(field)
            if actual != expected:
                raise RuntimeError(f"{symbol} {field} 計算驗證失敗：actual={actual}, expected={expected}")

    log("✓ 1D / 5D / 10D / 20D 計算驗證完全正確")

# ============================================================
# 檔案寫入 (Atomic Write)
# ============================================================

def save_chip(results: Dict[str, Any], complete: int, partial: int, insufficient: int):
    section("寫入 Data/chip.json")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()

    valid_20d = sum(1 for record in results.values() if record.get("main_force_20d") is not None)

    output = {
        "schema_version": VERSION,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "data_date": now.strftime("%Y-%m-%d"),
        "source": "FinMind API",
        "universe_mode": "fixed_test_4",
        "universe_count": len(TEST_STOCKS),
        "test_symbols": [stock["symbol"] for stock in TEST_STOCKS],
        "definition": {
            "main_force": "FinMind 三大法人（外資、投信、自營商）淨買賣超加總",
            "source_field": "買賣超張數",
            "main_force_1d": "最近1個交易日法人買賣超",
            "main_force_5d": "最近5個交易日每日法人買賣超加總",
            "main_force_10d": "最近10個交易日每日法人買賣超加總",
            "main_force_20d": "最近20個交易日每日法人買賣超加總",
            "unit": "張",
            "positive": "法人買超",
            "negative": "法人賣超",
        },
        "history_accumulation": {"enabled": False, "target_days": 20},
        "statistics": {
            "complete": complete,
            "partial": partial,
            "insufficient": insufficient,
            "valid_20d": valid_20d,
        },
        "stocks": results,
    }

    temp_file = CHIP_FILE.with_suffix(".json.tmp")
    with temp_file.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    with temp_file.open("r", encoding="utf-8") as f:
        verify = json.load(f)

    verify_stocks = verify.get("stocks", {})
    if len(verify_stocks) != len(TEST_STOCKS):
        raise RuntimeError("chip.json 驗證失敗：股票數量不匹配")

    temp_file.replace(CHIP_FILE)
    log("✓ chip.json 寫入成功")
    log(f"輸出檔案：{CHIP_FILE}")

# ============================================================
# Main
# ============================================================

def main():
    start_time = time.time()
    log("")
    log("=" * 72)
    log(f"台股 AI 選股系統 fetch_chip.py {VERSION}")
    log("=" * 72)
    log("開始時間：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        results, complete, partial, insufficient = fetch_all()
        validate(results)
        save_chip(results, complete, partial, insufficient)

        elapsed = time.time() - start_time
        valid_20d = sum(1 for record in results.values() if record.get("main_force_20d") is not None)

        log("")
        log("=" * 72)
        log(f"✓ fetch_chip.py {VERSION} 完成")
        log("=" * 72)
        log(f"測試股票：{len(TEST_STOCKS)} | 完整20D：{valid_20d} | 耗時：{elapsed:.1f} 秒")
        return 0

    except Exception as exc:
        log("")
        log("=" * 72)
        log(f"❌ fetch_chip.py {VERSION} 執行失敗：{exc}")
        log("=" * 72)
        if CHIP_FILE.exists():
            log("⚠️ 保留既有 chip.json")
        return 1

if __name__ == "__main__":
    sys.exit(main())
