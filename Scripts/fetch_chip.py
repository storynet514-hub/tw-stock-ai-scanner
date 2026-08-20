#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V7.0

============================================================
核心目的
============================================================

取得 CMoney「主力進出」頁面的：

「買賣超」

單位：
張

正數：
主力買超

負數：
主力賣超


============================================================
重要定義
============================================================

main_force_1d
    最近一個交易日主力買賣超

main_force_5d
    最近 5 個交易日「每日買賣超」加總

main_force_10d
    最近 10 個交易日「每日買賣超」加總

main_force_20d
    最近 20 個交易日「每日買賣超」加總


絕對禁止：

5日集中
20日集中
家數差
其他集中度欄位
其他籌碼欄位


============================================================
V7.0 架構
============================================================

V6.0 原本為固定測試 4 檔：

2337 旺宏
2426 鼎元
2368 金像電
3081 艾訊

V7.0 改為：

讀取現有 Data/universe.json
取得全台股市場股票清單
逐檔抓取 CMoney 主力進出首頁
只解析真正的「買賣超」欄位

其餘核心邏輯保留。


============================================================
universe.json
============================================================

目前實際格式：

{
    "version": "V5.3.0",
    "generated_at": "...",
    "source": "...",
    "market": "TW",
    "total": 1985,
    "listed_stocks": 1095,
    "otc_stocks": 890,
    "listed_etf": 0,
    "otc_etf": 0,
    "items": [
        {
            "symbol": "1240.TWO",
            "code": "1240",
            "name": "",
            "market": "TPEx",
            "type": "stock"
        }
    ]
}

注意：

全市場股票來源是：

Data/universe.json

不是 Data/stocks.json。


============================================================
20D 歷史累積
============================================================

每次 GitHub Actions 執行：

1. 讀取 Data/universe.json
2. 取得全市場股票
3. 抓取 CMoney 最新可驗證的「日期 + 買賣超」
4. 讀取上一版 Data/chip.json
5. 合併歷史資料
6. 新資料覆蓋同日期舊資料
7. 依日期排序
8. 每檔股票保留最近 20 個交易日
9. 計算 1D / 5D / 10D / 20D


第一次執行：
    可以得到 1D / 5D / 10D
    20D 尚未累積完成

之後每日執行：
    持續累積新的交易日

當某檔股票歷史資料 >= 20 個交易日：
    自動產生 main_force_20d


============================================================
重要限制
============================================================

本版本：

✓ 使用 Data/universe.json 作為全市場股票清單

✓ 只取 universe.json 中 type = stock

✓ 不使用固定 4 檔測試

✓ 不建立或依賴 Data/stocks.json

✓ 不跑 API 探測

✓ 不猜 pagination

✓ 不使用 URL 延伸資料

✓ 不使用其他欄位補足 20D

✓ 不使用 5日集中

✓ 不使用 20日集中

✓ 不使用家數差

✓ 不使用其他籌碼欄位

✓ CMoney 僅驗證「日期 + 買賣超」

============================================================
輸出
============================================================

Data/chip.json

chip.json 自己保存每檔股票歷史資料。

GitHub Actions 每日正常 commit chip.json
即可持續累積 20D 歷史。
"""

from __future__ import annotations

import json
import re
import sys
import time

from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup


# ============================================================
# 基本設定
# ============================================================

VERSION = "V7.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

# 全市場股票來源
UNIVERSE_FILE = DATA_DIR / "universe.json"

# 籌碼歷史輸出
CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

MAX_HISTORY = 20


# ============================================================
# CMoney URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{code}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{code}?s=main-force"
)


# ============================================================
# Headers
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/131.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8"
    ),
    "Accept-Language": (
        "zh-TW,zh;q=0.9,en;q=0.8"
    ),
    "Connection": "keep-alive",
}


# ============================================================
# Log
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("=" * 72)
    log(title)
    log("=" * 72)


# ============================================================
# Number
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = (
        text
        .replace(",", "")
        .replace("張", "")
        .strip()
    )

    if text.upper() in {
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "-",
        "--",
        "－",
        "—",
        "無",
    }:
        return None

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    try:
        return float(match.group(0))
    except Exception:
        return None


# ============================================================
# 日期
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    patterns = [
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:

            y, m, d = match.groups()

            try:

                dt = datetime(
                    int(y),
                    int(m),
                    int(d)
                )

                return dt.strftime(
                    "%Y/%m/%d"
                )

            except Exception:

                return None

    return None


# ============================================================
# Header normalize
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = (
        text
        .replace("\n", "")
        .replace("\r", "")
        .replace(" ", "")
        .replace("\u3000", "")
        .strip()
    )

    return text


# ============================================================
# 嚴格判斷「買賣超」
# ============================================================

def is_main_force_header(text):

    header = normalize_header(text)

    return header == "買賣超"


# ============================================================
# 股票代碼
# ============================================================

def normalize_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # CMoney 使用純股票代碼
    if re.fullmatch(
        r"\d{4,6}",
        text
    ):
        return text

    return None


# ============================================================
# Symbol
# ============================================================

def normalize_symbol(value):

    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # universe.json 的格式：
    # 2337.TW
    # 3081.TWO
    #
    # 這裡保留完整 symbol 作為輸出識別。
    if re.fullmatch(
        r"\d{4,6}\.(TW|TWO)",
        text
    ):
        return text

    return None


# ============================================================
# 股票名稱
# ============================================================

def normalize_stock_name(value):

    if value is None:
        return ""

    return str(value).strip()


# ============================================================
# 市場
# ============================================================

def normalize_market(value):

    if value is None:
        return ""

    text = str(value).strip().upper()

    if text in {
        "TWSE",
        "TSE",
        "上市",
        "上市股票",
    }:
        return "TWSE"

    if text in {
        "TPEX",
        "TPE",
        "OTC",
        "TPEx",
        "上櫃",
        "上櫃股票",
    }:
        return "TPEx"

    return text


# ============================================================
# 從 universe.json 建立全市場清單
# ============================================================

def extract_stock_records(data):

    records = []

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 頂層格式不是 object"
        )

    # --------------------------------------------------------
    # universe.json 的實際股票清單欄位：
    #
    # items
    # --------------------------------------------------------

    items = data.get("items")

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json 找不到有效的 items 股票清單"
        )

    for item in items:

        if not isinstance(item, dict):
            continue

        # ----------------------------------------------------
        # 只抓股票
        # ----------------------------------------------------

        item_type = str(
            item.get("type", "")
        ).strip().lower()

        if item_type != "stock":
            continue

        # ----------------------------------------------------
        # 股票代碼
        # ----------------------------------------------------

        symbol = normalize_symbol(
            item.get("symbol")
        )

        code = normalize_code(
            item.get("code")
        )

        # ----------------------------------------------------
        # 若 symbol 沒有，但 code 有，
        # 依 market 建立 symbol。
        # 正常情況 universe.json 已有 symbol，
        # 這只是防呆。
        # ----------------------------------------------------

        market = normalize_market(
            item.get("market")
        )

        if not code:
            continue

        if not symbol:

            if market == "TWSE":
                symbol = f"{code}.TW"

            elif market == "TPEx":
                symbol = f"{code}.TWO"

            else:
                continue

        name = normalize_stock_name(
            item.get("name")
        )

        records.append({
            "symbol": symbol,
            "code": code,
            "name": name,
            "market": market,
            "type": "stock",
        })

    return records


# ============================================================
# 載入全市場股票
# ============================================================

def load_market_stocks():

    section(
        "載入全台股市場股票清單"
    )

    # --------------------------------------------------------
    # 確認 universe.json
    # --------------------------------------------------------

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json，"
            "無法建立全市場股票清單"
        )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"無法讀取 Data/universe.json：{exc}"
        )

    # --------------------------------------------------------
    # 顯示 universe metadata
    # --------------------------------------------------------

    declared_total = data.get(
        "total"
    )

    declared_listed = data.get(
        "listed_stocks"
    )

    declared_otc = data.get(
        "otc_stocks"
    )

    log(
        f"universe.json 宣告總數："
        f"{declared_total}"
    )

    log(
        f"宣告上市股票："
        f"{declared_listed}"
    )

    log(
        f"宣告上櫃股票："
        f"{declared_otc}"
    )

    # --------------------------------------------------------
    # 解析 items
    # --------------------------------------------------------

    records = extract_stock_records(
        data
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for stock in records:

        symbol = stock["symbol"]

        if symbol not in unique:

            unique[symbol] = stock

        else:

            old = unique[symbol]

            if not old.get("name") and stock.get("name"):
                old["name"] = stock["name"]

            if not old.get("code") and stock.get("code"):
                old["code"] = stock["code"]

            if not old.get("market") and stock.get("market"):
                old["market"] = stock["market"]

    stocks = list(
        unique.values()
    )

    stocks.sort(
        key=lambda item: item["code"]
    )

    if not stocks:

        raise RuntimeError(
            "universe.json 的 items 沒有有效股票資料"
        )

    # --------------------------------------------------------
    # 統計
    # --------------------------------------------------------

    listed_count = sum(
        1
        for stock in stocks
        if stock["market"] == "TWSE"
    )

    otc_count = sum(
        1
        for stock in stocks
        if stock["market"] == "TPEx"
    )

    log(
        f"實際讀取股票數：{len(stocks)}"
    )

    log(
        f"實際上市股票：{listed_count}"
    )

    log(
        f"實際上櫃股票：{otc_count}"
    )

    log(
        "資料來源：Data/universe.json"
    )

    log(
        "✓ 使用 universe.json 的 items"
    )

    log(
        "✓ 只納入 type = stock"
    )

    log(
        "✓ 已取消固定 4 檔測試模式"
    )

    # --------------------------------------------------------
    # 如果 metadata 與 items 數量不一致，
    # 直接警告，不自行補資料。
    # --------------------------------------------------------

    if (
        isinstance(declared_total, int)
        and declared_total != len(stocks)
    ):

        log(
            "⚠️ 注意：universe.json "
            f"total={declared_total}，"
            f"實際股票={len(stocks)}"
        )

    return stocks


# ============================================================
# Request
# ============================================================

def request_cmoney_page(
    session,
    code
):

    urls = [
        CMONEY_URL.format(
            code=code
        ),
        CMONEY_MOBILE_URL.format(
            code=code
        ),
    ]

    last_error = None

    for url in urls:

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            html = response.text

            if not html:
                raise RuntimeError(
                    "CMoney 回傳空白內容"
                )

            return html, url

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 找日期欄與買賣超欄
# ============================================================

def find_column_indexes(headers):

    date_index = None
    force_index = None

    for index, header in enumerate(
        headers
    ):

        normalized = normalize_header(
            header
        )

        if date_index is None:

            if normalized == "日期":

                date_index = index

        if force_index is None:

            if is_main_force_header(
                normalized
            ):

                force_index = index

    return date_index, force_index


# ============================================================
# 嚴格解析 CMoney 表格
# ============================================================

def parse_cmoney_main_force(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    tables = soup.find_all(
        "table"
    )

    best_result = []

    target_table = None

    target_date_index = None

    target_force_index = None

    # ========================================================
    # 找到同時具有：
    #
    # 日期
    # 買賣超
    #
    # 的真正資料表
    # ========================================================

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        found = False

        for row in rows[:15]:

            cells = row.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            date_index, force_index = (
                find_column_indexes(
                    headers
                )
            )

            if (
                date_index is not None
                and force_index is not None
            ):

                target_table = table

                target_date_index = date_index

                target_force_index = force_index

                found = True

                break

        if found:
            break

    # ========================================================
    # 沒有真正的「日期 + 買賣超」
    # ========================================================

    if target_table is None:

        return []

    # ========================================================
    # 解析目標 table
    # ========================================================

    rows = target_table.find_all(
        "tr"
    )

    for row in rows:

        cells = row.find_all(
            ["th", "td"]
        )

        if len(cells) <= max(
            target_date_index,
            target_force_index
        ):
            continue

        values = [
            cell.get_text(
                " ",
                strip=True
            )
            for cell in cells
        ]

        date_value = normalize_date(
            values[target_date_index]
        )

        if not date_value:
            continue

        force_value = parse_number(
            values[target_force_index]
        )

        if force_value is None:
            continue

        best_result.append({
            "date": date_value,
            "main_force": force_value,
        })

    # ========================================================
    # 日期去重
    # ========================================================

    unique = {}

    for row in best_result:

        unique[
            row["date"]
        ] = row["main_force"]

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in unique.items()
    ]

    # ========================================================
    # 最新日期在前
    # ========================================================

    result.sort(
        key=lambda row: datetime.strptime(
            row["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result


# ============================================================
# 讀取舊 chip.json
# ============================================================

def load_previous_chip():

    if not CHIP_FILE.exists():

        log(
            "上一版 chip.json 不存在"
        )

        return None

    try:

        with CHIP_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(
            data,
            dict
        ):

            log(
                "⚠️ 舊 chip.json 格式不是 object"
            )

            return None

        return data

    except Exception as exc:

        log(
            f"⚠️ 無法讀取上一版 chip.json："
            f"{exc}"
        )

        return None


# ============================================================
# 取得舊歷史
# ============================================================

def get_previous_history(
    previous_data,
    symbol
):

    if not previous_data:
        return []

    stocks = previous_data.get(
        "stocks",
        {}
    )

    if not isinstance(
        stocks,
        dict
    ):
        return []

    record = stocks.get(
        symbol
    )

    if not isinstance(
        record,
        dict
    ):
        return []

    history = record.get(
        "history",
        []
    )

    if not isinstance(
        history,
        list
    ):
        return []

    cleaned = []

    for row in history:

        if not isinstance(
            row,
            dict
        ):
            continue

        date = normalize_date(
            row.get("date")
        )

        value = parse_number(
            row.get("main_force")
        )

        if not date:
            continue

        if value is None:
            continue

        cleaned.append({
            "date": date,
            "main_force": value,
        })

    return cleaned


# ============================================================
# 合併歷史
# ============================================================

def merge_history(
    old_history,
    new_history
):

    combined = {}

    # --------------------------------------------------------
    # 舊資料
    # --------------------------------------------------------

    for row in old_history:

        date = row.get("date")

        value = row.get("main_force")

        if not date or value is None:
            continue

        combined[date] = float(value)

    # --------------------------------------------------------
    # 新資料覆蓋同日期舊資料
    # --------------------------------------------------------

    for row in new_history:

        date = row.get("date")

        value = row.get("main_force")

        if not date or value is None:
            continue

        combined[date] = float(value)

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in combined.items()
    ]

    result.sort(
        key=lambda row: datetime.strptime(
            row["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    # 最近 20 個交易日
    return result[:MAX_HISTORY]


# ============================================================
# 計算期間
# ============================================================

def calculate_periods(
    history
):

    values = [
        float(row["main_force"])
        for row in history
        if row.get("main_force") is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(values),
    }

    if len(values) >= 1:

        result["main_force_1d"] = round(
            values[0],
            2
        )

    if len(values) >= 5:

        result["main_force_5d"] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result["main_force_10d"] = round(
            sum(values[:10]),
            2
        )

    if len(values) >= 20:

        result["main_force_20d"] = round(
            sum(values[:20]),
            2
        )

    return result


# ============================================================
# Status
# ============================================================

def get_status(
    periods
):

    if periods.get(
        "main_force_20d"
    ) is not None:

        return "complete"

    if periods.get(
        "main_force_10d"
    ) is not None:

        return "partial_20d"

    if periods.get(
        "main_force_5d"
    ) is not None:

        return "partial_10d"

    if periods.get(
        "main_force_1d"
    ) is not None:

        return "partial_5d"

    return "insufficient"


# ============================================================
# 取得單一股票
# ============================================================

def fetch_stock(
    session,
    stock,
    previous_data
):

    symbol = stock["symbol"]
    code = stock["code"]
    name = stock["name"]

    title_name = (
        f"{code} {name}"
        if name
        else code
    )

    section(
        f"CMoney 主力買賣超："
        f"{title_name}"
    )

    # --------------------------------------------------------
    # CMoney 使用純 code
    #
    # 例如：
    # symbol = 2337.TW
    # code   = 2337
    #
    # 不把 .TW / .TWO 傳給 CMoney
    # --------------------------------------------------------

    html, source_url = request_cmoney_page(
        session,
        code
    )

    # --------------------------------------------------------
    # 只解析 CMoney 首頁真正的「買賣超」
    # --------------------------------------------------------

    new_history = parse_cmoney_main_force(
        html
    )

    log(
        f"CMoney 首頁有效「買賣超」："
        f"{len(new_history)} 筆"
    )

    if new_history:

        log(
            "✓ 已確認資料來源欄位：買賣超"
        )

    else:

        log(
            "❌ 首頁沒有找到可驗證的"
            "「日期 + 買賣超」資料"
        )

    # --------------------------------------------------------
    # 舊歷史
    # --------------------------------------------------------

    old_history = get_previous_history(
        previous_data,
        symbol
    )

    log(
        f"上一版保存歷史："
        f"{len(old_history)} 筆"
    )

    # --------------------------------------------------------
    # 合併
    # --------------------------------------------------------

    history = merge_history(
        old_history,
        new_history
    )

    log(
        f"合併後歷史："
        f"{len(history)} 筆"
    )

    # --------------------------------------------------------
    # 計算
    # --------------------------------------------------------

    periods = calculate_periods(
        history
    )

    status = get_status(
        periods
    )

    log(
        f"主力1日："
        f"{periods['main_force_1d']}"
    )

    log(
        f"主力5日："
        f"{periods['main_force_5d']}"
    )

    log(
        f"主力10日："
        f"{periods['main_force_10d']}"
    )

    log(
        f"主力20日："
        f"{periods['main_force_20d']}"
    )

    log(
        f"歷史筆數："
        f"{len(history)}"
    )

    if len(history) >= 20:

        log(
            "✓ 已累積完整 20 個交易日"
        )

    else:

        log(
            "ℹ️ 20D 尚未累積完成，"
            "下一交易日繼續累積"
        )

    return {
        "symbol": symbol,
        "code": code,
        "name": name,
        "market": stock["market"],
        "type": "stock",

        "source": "CMoney",

        "source_url": source_url,

        "source_field": "買賣超",

        "main_force_1d":
            periods["main_force_1d"],

        "main_force_5d":
            periods["main_force_5d"],

        "main_force_10d":
            periods["main_force_10d"],

        "main_force_20d":
            periods["main_force_20d"],

        "history_count":
            len(history),

        "status":
            status,

        "history":
            history,

        "error":
            None,
    }


# ============================================================
# 建立失敗紀錄
# ============================================================

def build_error_record(
    stock,
    error,
    previous_data
):

    symbol = stock["symbol"]

    old_history = get_previous_history(
        previous_data,
        symbol
    )

    periods = calculate_periods(
        old_history
    )

    return {
        "symbol": symbol,
        "code": stock["code"],
        "name": stock["name"],
        "market": stock["market"],
        "type": "stock",

        "source": "CMoney",

        "source_url":
            CMONEY_URL.format(
                code=stock["code"]
            ),

        "source_field": "買賣超",

        "main_force_1d":
            periods["main_force_1d"],

        "main_force_5d":
            periods["main_force_5d"],

        "main_force_10d":
            periods["main_force_10d"],

        "main_force_20d":
            periods["main_force_20d"],

        "history_count":
            len(old_history),

        "status":
            get_status(periods),

        "history":
            old_history,

        "error":
            str(error),
    }


# ============================================================
# Fetch all
# ============================================================

def fetch_all(
    stocks,
    previous_data
):

    section(
        "開始 CMoney 全市場主力買賣超更新"
    )

    log(
        "本版本為全台股市場模式"
    )

    log(
        f"全市場股票：{len(stocks)} 檔"
    )

    log(
        "資料清單：Data/universe.json"
    )

    log(
        "只使用 CMoney 首頁「買賣超」"
    )

    log(
        "不探測 API"
    )

    log(
        "不猜 pagination"
    )

    log(
        "歷史資料保存於 Data/chip.json"
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    results = {}

    complete = 0
    partial = 0
    insufficient = 0
    failed = 0

    total = len(stocks)

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        symbol = stock["symbol"]
        code = stock["code"]
        name = stock["name"]

        display_name = (
            f"{code} {name}"
            if name
            else code
        )

        log("")

        log(
            f"[{index}/{total}] "
            f"{display_name}"
        )

        try:

            record = fetch_stock(
                session,
                stock,
                previous_data
            )

            results[symbol] = record

            status = record["status"]

            if status == "complete":

                complete += 1

            elif status.startswith(
                "partial"
            ):

                partial += 1

            else:

                insufficient += 1

        except Exception as exc:

            failed += 1

            log(
                f"❌ {code} 取得失敗："
                f"{exc}"
            )

            record = build_error_record(
                stock,
                exc,
                previous_data
            )

            results[symbol] = record

            if record["main_force_20d"] is not None:

                complete += 1

            elif (
                record["main_force_10d"] is not None
            ):

                partial += 1

            elif (
                record["main_force_1d"] is not None
            ):

                partial += 1

            else:

                insufficient += 1

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete,
        partial,
        insufficient,
        failed
    )


# ============================================================
# Validate
# ============================================================

def validate(
    results,
    stocks
):

    section(
        "最終資料驗證"
    )

    if len(results) != len(stocks):

        raise RuntimeError(
            "輸出股票數量錯誤"
        )

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for stock in stocks:

        symbol = stock["symbol"]

        if symbol not in results:

            raise RuntimeError(
                f"缺少股票：{symbol}"
            )

        record = results[symbol]

        # ----------------------------------------------------
        # 確認基本識別資料
        # ----------------------------------------------------

        if record.get("code") != stock["code"]:

            raise RuntimeError(
                f"{symbol} code 不一致"
            )

        if record.get("market") != stock["market"]:

            raise RuntimeError(
                f"{symbol} market 不一致"
            )

        # ----------------------------------------------------
        # 統計有效期間
        # ----------------------------------------------------

        if record.get(
            "main_force_1d"
        ) is not None:

            valid_1d += 1

        if record.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if record.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

        if record.get(
            "main_force_20d"
        ) is not None:

            valid_20d += 1

        # ----------------------------------------------------
        # 確認 history
        # ----------------------------------------------------

        history = record.get(
            "history",
            []
        )

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history 格式錯誤"
            )

        # ----------------------------------------------------
        # 最多 20 筆
        # ----------------------------------------------------

        if len(history) > MAX_HISTORY:

            raise RuntimeError(
                f"{symbol} history 超過 "
                f"{MAX_HISTORY} 筆"
            )

        # ----------------------------------------------------
        # 驗證日期排序
        # ----------------------------------------------------

        dates = [
            row.get("date")
            for row in history
            if isinstance(row, dict)
        ]

        for i in range(
            len(dates) - 1
        ):

            if dates[i] < dates[i + 1]:

                raise RuntimeError(
                    f"{symbol} history 日期排序錯誤"
                )

        # ----------------------------------------------------
        # 驗證期間計算
        # ----------------------------------------------------

        periods = calculate_periods(
            history
        )

        for field in [
            "main_force_1d",
            "main_force_5d",
            "main_force_10d",
            "main_force_20d",
        ]:

            actual = record.get(
                field
            )

            expected = periods.get(
                field
            )

            if actual != expected:

                raise RuntimeError(
                    f"{symbol} {field} "
                    f"計算驗證失敗："
                    f"actual={actual}, "
                    f"expected={expected}"
                )

    log(
        f"全市場股票：{len(stocks)}"
    )

    log(
        f"有效主力1D：{valid_1d}"
    )

    log(
        f"有效主力5D：{valid_5d}"
    )

    log(
        f"有效主力10D：{valid_10d}"
    )

    log(
        f"有效主力20D：{valid_20d}"
    )

    # --------------------------------------------------------
    # 全市場模式下：
    #
    # 不要求每檔股票都必須有 5D / 10D，
    # 因為個別股票可能抓取失敗。
    # --------------------------------------------------------

    if valid_1d == 0:

        raise RuntimeError(
            "全市場沒有任何有效主力1日資料"
        )

    if valid_5d == 0:

        raise RuntimeError(
            "全市場沒有任何有效主力5日資料"
        )

    if valid_10d == 0:

        raise RuntimeError(
            "全市場沒有任何有效主力10日資料"
        )

    # --------------------------------------------------------
    # 20D 不在歷史累積期間強制失敗
    # --------------------------------------------------------

    if valid_20d == len(stocks):

        log(
            "✓ 全市場全部已有完整20D"
        )

    else:

        log(
            "ℹ️ 20D 尚在歷史累積階段"
        )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 1D / 5D / 10D / 20D 計算驗證完成"
    )

    log(
        "✓ 未使用 5日集中 / 20日集中 / 家數差"
    )


# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    stocks,
    complete,
    partial,
    insufficient,
    failed
):

    section(
        "寫入 Data/chip.json"
    )

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    valid_20d = sum(
        1
        for record in results.values()
        if record.get(
            "main_force_20d"
        ) is not None
    )

    output = {

        "schema_version":
            VERSION,

        "generated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "data_date":
            now.strftime(
                "%Y-%m-%d"
            ),

        "source":
            "CMoney",

        "universe_mode":
            "full_market",

        "universe_source":
            "Data/universe.json",

        "universe_count":
            len(stocks),

        "definition": {

            "main_force":
                "CMoney 主力進出之買賣超",

            "source_field":
                "買賣超",

            "main_force_1d":
                "最近1個交易日主力買賣超",

            "main_force_5d":
                "最近5個交易日每日主力買賣超加總",

            "main_force_10d":
                "最近10個交易日每日主力買賣超加總",

            "main_force_20d":
                "最近20個交易日每日主力買賣超加總",

            "history_method":
                "每日更新後與上一版chip.json歷史資料合併",

            "unit":
                "張",

            "positive":
                "主力買超",

            "negative":
                "主力賣超",

            "forbidden_fields": [
                "5日集中",
                "20日集中",
                "家數差",
            ],
        },

        "history_accumulation": {

            "enabled":
                True,

            "target_days":
                20,

            "current_valid_20d_stocks":
                valid_20d,

            "note":
                "20D由每日抓取之買賣超歷史資料累積計算",
        },

        "statistics": {

            "complete":
                complete,

            "partial":
                partial,

            "insufficient":
                insufficient,

            "failed":
                failed,

            "valid_20d":
                valid_20d,
        },

        "stocks":
            results,
    }

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
    )

    # --------------------------------------------------------
    # 寫入暫存
    # --------------------------------------------------------

    with temp_file.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 寫入後重新讀取驗證
    # --------------------------------------------------------

    with temp_file.open(
        "r",
        encoding="utf-8"
    ) as f:

        verify = json.load(f)

    if not isinstance(
        verify,
        dict
    ):

        raise RuntimeError(
            "chip.json 頂層不是 object"
        )

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 不是 object"
        )

    if len(
        verify_stocks
    ) != len(stocks):

        raise RuntimeError(
            "chip.json 股票數量錯誤"
        )

    # --------------------------------------------------------
    # 驗證所有股票
    # --------------------------------------------------------

    for stock in stocks:

        symbol = stock["symbol"]

        if symbol not in verify_stocks:

            raise RuntimeError(
                f"chip.json 缺少 {symbol}"
            )

        record = verify_stocks[symbol]

        if record.get("code") != stock["code"]:

            raise RuntimeError(
                f"chip.json {symbol} code 不一致"
            )

        history = record.get(
            "history",
            []
        )

        if not isinstance(
            history,
            list
        ):

            raise RuntimeError(
                f"{symbol} history 格式錯誤"
            )

        periods = calculate_periods(
            history
        )

        for field in [
            "main_force_1d",
            "main_force_5d",
            "main_force_10d",
            "main_force_20d",
        ]:

            if (
                record.get(field)
                != periods.get(field)
            ):

                raise RuntimeError(
                    f"{symbol} {field} "
                    "寫入後驗證失敗"
                )

    # --------------------------------------------------------
    # 原子替換
    # --------------------------------------------------------

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 寫入成功"
    )

    log(
        f"輸出股票數："
        f"{len(stocks)}"
    )

    log(
        f"完整20D："
        f"{valid_20d}"
    )

    log(
        f"輸出檔案："
        f"{CHIP_FILE}"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)

    log(
        "台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )

    log("=" * 72)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    log(
        "資料來源：CMoney 主力進出"
    )

    log(
        "指定欄位：買賣超"
    )

    log(
        "20D：每日買賣超歷史累積"
    )

    log(
        "股票範圍：全台股市場"
    )

    log(
        "股票清單：Data/universe.json"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

        # ----------------------------------------------------
        # 載入全市場股票
        # ----------------------------------------------------

        stocks = load_market_stocks()

        # ----------------------------------------------------
        # 讀上一版資料
        # ----------------------------------------------------

        previous_data = load_previous_chip()

        if previous_data:

            previous_stocks = (
                previous_data.get(
                    "stocks",
                    {}
                )
            )

            if isinstance(
                previous_stocks,
                dict
            ):

                log(
                    f"上一版 chip.json 股票："
                    f"{len(previous_stocks)}"
                )

        # ----------------------------------------------------
        # 抓取
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient,
            failed
        ) = fetch_all(
            stocks,
            previous_data
        )

        # ----------------------------------------------------
        # 驗證
        # ----------------------------------------------------

        validate(
            results,
            stocks
        )

        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------

        save_chip(
            results,
            stocks,
            complete,
            partial,
            insufficient,
            failed
        )

        elapsed = (
            time.time()
            - start_time
        )

        valid_20d = sum(
            1
            for record in results.values()
            if record.get(
                "main_force_20d"
            ) is not None
        )

        valid_10d = sum(
            1
            for record in results.values()
            if record.get(
                "main_force_10d"
            ) is not None
        )

        valid_5d = sum(
            1
            for record in results.values()
            if record.get(
                "main_force_5d"
            ) is not None
        )

        valid_1d = sum(
            1
            for record in results.values()
            if record.get(
                "main_force_1d"
            ) is not None
        )

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py {VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"全市場股票："
            f"{len(stocks)}"
        )

        log(
            f"有效1D："
            f"{valid_1d}"
        )

        log(
            f"有效5D："
            f"{valid_5d}"
        )

        log(
            f"有效10D："
            f"{valid_10d}"
        )

        log(
            f"完整20D："
            f"{valid_20d}"
        )

        log(
            f"抓取失敗："
            f"{failed}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出："
            f"{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 72)

        log(
            f"❌ fetch_chip.py {VERSION} 執行失敗"
        )

        log("=" * 72)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json"
            )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )