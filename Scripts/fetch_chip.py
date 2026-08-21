#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.0

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
V6.0 架構修正
============================================================

CMoney 主力進出首頁目前一次可直接驗證約 10 個交易日。

因此：

「20D」不應該強迫 CMoney 單次頁面一次提供 20 筆。

正確做法：

每次 GitHub Actions 執行：

1. 抓 CMoney 最新 10 個交易日
2. 讀取上一版 Data/chip.json
3. 合併歷史資料
4. 以日期去重
5. 依日期排序
6. 保留最近 20 個交易日
7. 計算 1D / 5D / 10D / 20D

因此：

第一次執行：
    可以得到 1D / 5D / 10D
    20D 尚未累積完成

之後每天執行：
    持續累積新的交易日

當歷史資料 >= 20 個交易日：
    自動產生 main_force_20d

============================================================
重要
============================================================

本版本：

不讀 universe.json
不跑全市場
不探測 API
不猜 pagination
不使用 URL 延伸資料
不使用其他欄位補足 20D

固定測試：

3490 單井
3543 州巧
1583 程泰
6674 鋐寶科技

============================================================
輸出
============================================================

Data/chip.json

並且 chip.json 自己保存歷史資料，
因此 GitHub Actions 只要正常 commit chip.json，
歷史就會持續累積。

============================================================
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

VERSION = "V6.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

MAX_HISTORY = 20

# ============================================================
# 固定測試股票
# ============================================================

TEST_STOCKS = [
    {
        "symbol": "3543",
        "name": "州巧",
        "market": "TWSE",
    },
    {
        "symbol": "1583",
        "name": "程泰",
        "market": "TWSE",
    },
    {
        "symbol": "6674",
        "name": "鋐寶科技",
        "market": "TWSE",
    },
    {
        "symbol": "3490",
        "name": "單井",
        "market": "TPEX",
    },
]

# ============================================================
# CMoney URL
# ============================================================

CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
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

    # --------------------------------------------------------
    # 只接受真正的「買賣超」
    # --------------------------------------------------------

    if header == "買賣超":
        return True

    return False

# ============================================================
# Request
# ============================================================

def request_cmoney_page(
    session,
    symbol
):

    urls = [
        CMONEY_URL.format(
            symbol=symbol
        ),
        CMONEY_MOBILE_URL.format(
            symbol=symbol
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

        # ----------------------------------------------------
        # 日期
        # ----------------------------------------------------

        if date_index is None:

            if normalized == "日期":

                date_index = index

        # ----------------------------------------------------
        # 買賣超
        # ----------------------------------------------------

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

    best_header = None

    best_date_index = None

    best_force_index = None

    # ========================================================
    # 第一階段：
    # 找到真正同時具有
    #
    # 日期
    # 買賣超
    #
    # 的 table
    # ========================================================

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        for header_row in rows[:15]:

            cells = header_row.find_all(
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

                best_header = headers
                best_date_index = date_index
                best_force_index = force_index

                break

        if best_header is not None:
            break

    # ========================================================
    # 沒找到
    # ========================================================

    if best_header is None:

        return []

    # ========================================================
    # 解析資料
    # ========================================================

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        # 只有包含目標 header 的 table 才處理

        table_has_target = False

        for row in rows[:15]:

            cells = row.find_all(
                ["th", "td"]
            )

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
                date_index == best_date_index
                and force_index == best_force_index
                and date_index is not None
                and force_index is not None
            ):

                table_has_target = True
                break

        if not table_has_target:
            continue

        # ----------------------------------------------------
        # 讀取每一列
        # ----------------------------------------------------

        for row in rows:

            cells = row.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                best_date_index,
                best_force_index
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
                values[best_date_index]
            )

            if not date_value:
                continue

            force_value = parse_number(
                values[best_force_index]
            )

            if force_value is None:
                continue

            best_result.append({
                "date": date_value,
                "main_force": force_value,
            })

    # ========================================================
    # 去重
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
    # 最新在前
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

    # 舊資料
    for row in old_history:

        date = row.get("date")

        value = row.get("main_force")

        if not date or value is None:
            continue

        combined[date] = float(value)

    # 新資料覆蓋舊資料
    # 同一天以 CMoney 本次最新抓取值為準
    for row in new_history:

        date = row.get("date")

        value = row.get("main_force")

        if not date or value is None:
            continue

        combined[date] = float(value)

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
    name = stock["name"]

    section(
        f"CMoney 主力買賣超："
        f"{symbol} {name}"
    )

    html, source_url = request_cmoney_page(
        session,
        symbol
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
        "name": name,
        "market": stock["market"],

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
        "name": stock["name"],
        "market": stock["market"],

        "source": "CMoney",

        "source_url":
            CMONEY_URL.format(
                symbol=symbol
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
    previous_data
):

    section(
        "開始 CMoney 主力買賣超更新"
    )

    log(
        "本版本為固定測試模式"
    )

    log(
        "不讀 universe.json"
    )

    log(
        "不跑全市場 Universe"
    )

    log(
        "固定測試："
        "2337 / 2426 / 2368 / 3081"
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

    total = len(TEST_STOCKS)

    for index, stock in enumerate(
        TEST_STOCKS,
        start=1
    ):

        symbol = stock["symbol"]
        name = stock["name"]

        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
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

            log(
                f"❌ {symbol} 取得失敗："
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

            elif record["main_force_10d"] is not None:

                partial += 1

            elif record["main_force_1d"] is not None:

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
        insufficient
    )

# ============================================================
# Validate
# ============================================================

def validate(
    results
):

    section(
        "最終資料驗證"
    )

    if len(results) != len(TEST_STOCKS):

        raise RuntimeError(
            "輸出股票數量錯誤"
        )

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        if symbol not in results:

            raise RuntimeError(
                f"缺少測試股票：{symbol}"
            )

        record = results[symbol]

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
        # 確認 history 與 20D 計算一致
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
        f"測試股票：{len(TEST_STOCKS)}"
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
    # 5D / 10D 是目前系統必須立即可用的
    # --------------------------------------------------------

    if valid_5d == 0:

        raise RuntimeError(
            "沒有任何有效主力5日資料"
        )

    if valid_10d == 0:

        raise RuntimeError(
            "沒有任何有效主力10日資料"
        )

    # --------------------------------------------------------
    # 20D 不在第一次執行時強制失敗
    # --------------------------------------------------------

    if valid_20d == len(TEST_STOCKS):

        log(
            "✓ 四檔全部已有完整20D"
        )

    else:

        log(
            "ℹ️ 20D 尚在歷史累積階段"
        )

    log(
        "✓ 資料來源欄位驗證完成"
    )

    log(
        "✓ 1D / 5D / 10D 計算驗證完成"
    )

    log(
        "✓ 未使用 5日集中 / 20日集中 / 家數差"
    )

# ============================================================
# Save
# ============================================================

def save_chip(
    results,
    complete,
    partial,
    insufficient
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
            "fixed_test_4",

        "universe_count":
            len(TEST_STOCKS),

        "test_symbols": [
            stock["symbol"]
            for stock in TEST_STOCKS
        ],

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
    ) != len(TEST_STOCKS):

        raise RuntimeError(
            "chip.json 股票數量錯誤"
        )

    # --------------------------------------------------------
    # 驗證四檔股票
    # --------------------------------------------------------

    for stock in TEST_STOCKS:

        symbol = stock["symbol"]

        if symbol not in verify_stocks:

            raise RuntimeError(
                f"chip.json 缺少 {symbol}"
            )

        record = verify_stocks[symbol]

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

            if record.get(field) != periods.get(field):

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
        f"{len(TEST_STOCKS)}"
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
        "固定測試：2337 / 2426 / 2368 / 3081"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

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
            insufficient
        ) = fetch_all(
            previous_data
        )

        # ----------------------------------------------------
        # 驗證
        # ----------------------------------------------------

        validate(
            results
        )

        # ----------------------------------------------------
        # 儲存
        # ----------------------------------------------------

        save_chip(
            results,
            complete,
            partial,
            insufficient
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

        log("")
        log("=" * 72)

        log(
            f"✓ fetch_chip.py {VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"測試股票："
            f"{len(TEST_STOCKS)}"
        )

        log(
            f"完整20D："
            f"{valid_20d}"
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