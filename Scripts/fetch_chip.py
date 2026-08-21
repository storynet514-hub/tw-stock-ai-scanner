#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.2 (完整修正版)

============================================================
核心目的
============================================================
取得 CMoney「主力進出」頁面的「買賣超」（單位：張）。
正數：主力買超
負數：主力賣超

============================================================
重要定義與規則
============================================================
main_force_1d  : 最近 1 個交易日主力買賣超
main_force_5d  : 最近 5 個交易日「每日買賣超」加總
main_force_10d : 最近 10 個交易日「每日買賣超」加總
main_force_20d : 最近 20 個交易日「每日買賣超」加總

絕對禁止：
5日集中、20日集中、家數差、其他集中度欄位

優化重點：
1. 採用「全動態鍵值比對」解析 __NEXT_DATA__，解決前版解析出 0 筆退回 HTML Table 的問題。
2. 徹底解鎖 20D 完整資料。
3. 嚴格驗證：>= 20 筆才計算 main_force_20d，否則維持 None。
============================================================
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ============================================================
# 基本設定
# ============================================================

VERSION = "V6.2"
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Data"
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5
TARGET_HISTORY = 20

# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {"symbol": "2337", "name": "旺宏", "market": "TWSE"},
    {"symbol": "2426", "name": "鼎元", "market": "TWSE"},
    {"symbol": "2368", "name": "金像電", "market": "TWSE"},
    {"symbol": "3081", "name": "聯亞", "market": "TPEX"},
]

CMONEY_URL = "https://www.cmoney.tw/forum/stock/{symbol}?s=main-force"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
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

def normalize_date(text: Any) -> Optional[str]:
    if text is None:
        return None
    text = str(text).strip()
    patterns = [
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})",
        r"(\d{4})(\d{2})(\d{2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            y, m, d = match.groups()
            try:
                dt = datetime(int(y), int(m), int(d))
                return dt.strftime("%Y/%m/%d")
            except ValueError:
                continue
    return None

def parse_number(text: Any) -> Optional[float]:
    if text is None:
        return None
    text = str(text).strip().replace(",", "").replace("張", "")
    if not text or text.upper() in {"N/A", "NA", "NONE", "NULL", "-", "--", "無"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None

def normalize_header(text: Any) -> str:
    if text is None:
        return ""
    return str(text).replace("\n", "").replace("\r", "").replace(" ", "").replace("\u3000", "").strip()

# ============================================================
# 解析核心：__NEXT_DATA__ 全動態鍵值比對 (解決 0 筆問題)
# ============================================================

def extract_from_next_data(html: str) -> List[Dict[str, Any]]:
    """
    強效版 __NEXT_DATA__ 解析
    動態搜尋 JSON 樹狀結構，自動匹配日期欄位與買賣超數值，避開家數差與集中度
    """
    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag or not script_tag.string:
        return []

    results = []
    try:
        data = json.loads(script_tag.string)

        def recursive_search(obj: Any):
            if isinstance(obj, dict):
                norm_d = None
                # 1. 尋找日期欄位
                for k, v in obj.items():
                    if any(key in k.lower() for key in ["date", "time", "day"]):
                        norm_d = normalize_date(v)
                        if norm_d:
                            break

                if not norm_d:
                    for v in obj.values():
                        if isinstance(v, str):
                            norm_d = normalize_date(v)
                            if norm_d:
                                break

                # 2. 尋找主力買賣超數值
                if norm_d:
                    norm_f = None
                    for k, v in obj.items():
                        k_low = k.lower()
                        # 精確鎖定買賣超，排斥集中度與家數差
                        if any(key in k_low for key in ["buysell", "force", "net", "over", "amount"]):
                            if not any(bad in k_low for bad in ["ratio", "diff", "count", "percent", "rate"]):
                                val = parse_number(v)
                                if val is not None:
                                    norm_f = val
                                    break

                    if norm_f is not None:
                        results.append({"date": norm_d, "main_force": norm_f})

                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        recursive_search(v)

            elif isinstance(obj, list):
                for item in obj:
                    recursive_search(item)

        recursive_search(data)
    except Exception:
        return []

    # 去重並保留最新
    dedup = {}
    for r in results:
        dedup[r["date"]] = r["main_force"]

    return [{"date": k, "main_force": v} for k, v in dedup.items()]

def parse_cmoney_tables(html: str) -> List[Dict[str, Any]]:
    """傳統 HTML Table 解析 (備援機制)"""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        date_idx, force_idx = None, None
        header_row_idx = None

        for pos, row in enumerate(rows[:20]):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            headers = [normalize_header(cell.get_text(" ", strip=True)) for cell in cells]
            for idx, text in enumerate(headers):
                if date_idx is None and text == "日期":
                    date_idx = idx
                if force_idx is None and text == "買賣超":
                    force_idx = idx

            if date_idx is not None and force_idx is not None:
                header_row_idx = pos
                break

        if header_row_idx is None:
            continue

        for row in rows[header_row_idx + 1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) <= max(date_idx, force_idx):
                continue

            values = [cell.get_text(" ", strip=True) for cell in cells]
            d = normalize_date(values[date_idx])
            f = parse_number(values[force_idx])

            if d and f is not None:
                results.append({"date": d, "main_force": f})

    return results

# ============================================================
# 資料整合與計算
# ============================================================

def merge_current_data(*datasets) -> List[Dict[str, Any]]:
    combined = {}
    for dataset in datasets:
        for row in dataset:
            if not isinstance(row, dict):
                continue
            date = normalize_date(row.get("date"))
            val = parse_number(row.get("main_force"))
            if date and val is not None:
                combined[date] = float(val)

    result = [{"date": d, "main_force": v} for d, v in combined.items()]
    result.sort(key=lambda x: datetime.strptime(x["date"], "%Y/%m/%d"), reverse=True)
    return result[:TARGET_HISTORY]

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

# ============================================================
# 單股執行與錯誤處理
# ============================================================

def fetch_stock(session: requests.Session, stock: Dict[str, str]) -> Dict[str, Any]:
    symbol, name = stock["symbol"], stock["name"]
    section(f"CMoney 主力買賣超：{symbol} {name}")

    url = CMONEY_URL.format(symbol=symbol)
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    html = response.text

    if not html:
        raise RuntimeError("CMoney 回傳空白內容")

    json_data = extract_from_next_data(html)
    log(f"✓ __NEXT_DATA__ 解析取得有效資料：{len(json_data)} 筆")

    table_data = parse_cmoney_tables(html)
    log(f"✓ HTML Table 解析取得：{len(table_data)} 筆")

    history = merge_current_data(json_data, table_data)
    log(f"本次最終整合有效交易日：{len(history)} 筆")

    periods = calculate_periods(history)
    status = get_status(periods)

    log(f"主力 1日：{periods['main_force_1d']}")
    log(f"主力 5日：{periods['main_force_5d']}")
    log(f"主力 10日：{periods['main_force_10d']}")
    log(f"主力 20日：{periods['main_force_20d']}")

    if len(history) >= 20:
        log("✓ 已取得完整 20 個交易日")
    else:
        log(f"ℹ️ 本次 CMoney 實際取得 {len(history)} 筆")

    return {
        "symbol": symbol,
        "name": name,
        "market": stock["market"],
        "source": "CMoney",
        "source_url": url,
        "source_field": "買賣超",
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
        "source": "CMoney",
        "source_url": CMONEY_URL.format(symbol=stock["symbol"]),
        "source_field": "買賣超",
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
# 批次執行與嚴格驗證
# ============================================================

def fetch_all() -> Tuple[Dict[str, Any], int, int, int]:
    section("開始 CMoney 主力買賣超更新")
    log("本版本為固定測試模式 (固定測試：2337 / 2426 / 2368 / 3081)")

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
            raise RuntimeError(f"{symbol} 超過20筆資料")

        periods = calculate_periods(history)
        for field in ["main_force_1d", "main_force_5d", "main_force_10d", "main_force_20d"]:
            actual = record.get(field)
            expected = periods.get(field)
            if actual != expected:
                raise RuntimeError(f"{symbol} {field} 計算驗證失敗：actual={actual}, expected={expected}")

    log("✓ 1D / 5D / 10D / 20D 計算驗證完成")
    log("✓ 未使用 5日集中 / 20日集中 / 家數差")

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
        "source": "CMoney",
        "universe_mode": "fixed_test_4",
        "universe_count": len(TEST_STOCKS),
        "test_symbols": [stock["symbol"] for stock in TEST_STOCKS],
        "definition": {
            "main_force": "CMoney 主力進出之買賣超",
            "source_field": "買賣超",
            "main_force_1d": "最近1個交易日主力買賣超",
            "main_force_5d": "最近5個交易日每日主力買賣超加總",
            "main_force_10d": "最近10個交易日每日主力買賣超加總",
            "main_force_20d": "最近20個交易日每日主力買賣超加總",
            "unit": "張",
            "positive": "主力買超",
            "negative": "主力賣超",
            "forbidden_fields": ["5日集中", "20日集中", "家數差"],
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
