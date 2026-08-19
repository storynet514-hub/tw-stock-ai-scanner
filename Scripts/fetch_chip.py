#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V7.0

本版本目的：
1. 測試階段固定只抓 5 檔
2. 不限制歷史資料只能 10D
3. CMoney 實際抓到幾筆就保留幾筆
4. 自動計算主力 1D / 5D / 10D / 20D
5. 歷史不足時，對應期間顯示 None
6. 不用收盤價冒充主力買賣超
7. 不用「舊10D → 新10D」假裝增加歷史
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

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30
REQUEST_DELAY = 0.5

# ============================================================
# 測試股票
#
# 注意：
# 這裡限制的是「股票數量」
# 不是「歷史天數」
#
# 歷史資料能抓幾天就抓幾天。
# ============================================================

TEST_MODE = True

TEST_STOCKS = [
    {
        "symbol": "2337",
        "name": "旺宏",
        "market": "TW",
    },
    {
        "symbol": "2426",
        "name": "鼎元",
        "market": "TW",
    },
    {
        "symbol": "2368",
        "name": "金像電",
        "market": "TW",
    },
    {
        "symbol": "3081",
        "name": "艾訊",
        "market": "TWO",
    },
    {
        "symbol": "3490",
        "name": "單井",
        "market": "TW",
    },
]


CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

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
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
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
# 數字解析
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = text.replace(",", "")
    text = text.replace("張", "")
    text = text.replace("%", "")
    text = text.replace("\u3000", "")

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
# 日期解析
# ============================================================

DATE_PATTERNS = [
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"\d{4}-\d{1,2}-\d{1,2}",
]


def normalize_date(text):

    if text is None:
        return None

    text = str(text).strip()

    for pattern in DATE_PATTERNS:

        if re.fullmatch(pattern, text):

            return text.replace("-", "/")

    return None


# ============================================================
# Header
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace("\n", "")
    text = text.replace("\r", "")
    text = text.replace(" ", "")
    text = text.replace("\u3000", "")

    return text.strip()


def is_main_force_header(text):

    header = normalize_header(text)

    if header == "買賣超":
        return True

    if (
        "買賣超" in header
        and "家數" not in header
        and "集中" not in header
    ):
        return True

    return False


# ============================================================
# Request
# ============================================================

def request_page(session, symbol):

    urls = [
        CMONEY_URL.format(symbol=symbol),
        CMONEY_MOBILE_URL.format(symbol=symbol),
    ]

    last_error = None

    for url in urls:

        try:

            response = session.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            html = response.text

            if html:
                return html

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "CMoney 頁面無法取得"
    )


# ============================================================
# Table 解析
#
# 只接受明確 Header：
#
# 日期 | 收盤價 | 買賣超 | ...
#
# ============================================================

def parse_table_with_header(soup):

    tables = soup.find_all("table")

    for table in tables:

        rows = table.find_all("tr")

        if not rows:
            continue

        header_index = None
        headers = None

        for row_index, tr in enumerate(rows[:15]):

            cells = tr.find_all(
                ["th", "td"]
            )

            if not cells:
                continue

            current_headers = [
                normalize_header(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            has_date = any(
                h == "日期"
                or "日期" in h
                for h in current_headers
            )

            has_force = any(
                is_main_force_header(h)
                for h in current_headers
            )

            if has_date and has_force:

                header_index = row_index
                headers = current_headers
                break

        if header_index is None:
            continue

        date_index = None
        force_index = None

        for i, header in enumerate(headers):

            if (
                date_index is None
                and (
                    header == "日期"
                    or "日期" in header
                )
            ):
                date_index = i

            if (
                force_index is None
                and is_main_force_header(header)
            ):
                force_index = i

        if (
            date_index is None
            or force_index is None
        ):
            continue

        log(
            f"   ✓ CMoney Header："
            f"日期={date_index} "
            f"買賣超={force_index}"
        )

        result = []

        for tr in rows[header_index + 1:]:

            cells = tr.find_all(
                ["th", "td"]
            )

            if len(cells) <= max(
                date_index,
                force_index
            ):
                continue

            values = [
                cell.get_text(
                    " ",
                    strip=True
                )
                for cell in cells
            ]

            date = normalize_date(
                values[date_index]
            )

            if not date:
                continue

            main_force = parse_number(
                values[force_index]
            )

            if main_force is None:
                continue

            result.append({
                "date": date,
                "main_force": main_force,
            })

        if result:
            return result

    return []


# ============================================================
# 文字解析
#
# 重要：
# 不再把日期後第一個數字直接當買賣超。
#
# 只有能確認：
# 日期 → 收盤價 → 買賣超
# 才使用。
# ============================================================

def parse_text_fallback(soup):

    text = soup.get_text(
        "\n",
        strip=True
    )

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    result = []

    for i, line in enumerate(lines):

        date = normalize_date(line)

        if not date:
            continue

        numbers = []

        for j in range(
            i + 1,
            min(i + 10, len(lines))
        ):

            number = parse_number(
                lines[j]
            )

            if number is not None:
                numbers.append(number)

            if len(numbers) >= 2:
                break

        if len(numbers) < 2:
            continue

        # 第一個 = 收盤價
        # 第二個 = 買賣超
        main_force = numbers[1]

        result.append({
            "date": date,
            "main_force": main_force,
        })

    return result


# ============================================================
# Clean history
# ============================================================

def clean_history(rows):

    unique = {}

    for row in rows:

        date = row.get("date")
        value = row.get("main_force")

        if not date or value is None:
            continue

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:
            continue

        unique[date] = float(value)

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in unique.items()
    ]

    result.sort(
        key=lambda x: datetime.strptime(
            x["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result


# ============================================================
# 取得主力歷史
#
# 不限制 10D。
# ============================================================

def fetch_history(session, symbol):

    html = request_page(
        session,
        symbol
    )

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # 第一優先：Table Header
    history = parse_table_with_header(
        soup
    )

    if history:

        return clean_history(history)

    # 第二優先：嚴格文字結構
    history = parse_text_fallback(
        soup
    )

    if history:

        return clean_history(history)

    return []


# ============================================================
# 計算 1D / 5D / 10D / 20D
#
# history 有幾筆就保留幾筆。
#
# 不足：
# 5D 不足5筆 → None
# 10D 不足10筆 → None
# 20D 不足20筆 → None
# ============================================================

def calculate_periods(history):

    values = [
        row["main_force"]
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
            sum(values[:1]),
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
# 單檔
# ============================================================

def fetch_stock(session, stock):

    symbol = stock["symbol"]

    history = fetch_history(
        session,
        symbol
    )

    if not history:

        raise RuntimeError(
            "找不到 CMoney 主力買賣超資料"
        )

    periods = calculate_periods(
        history
    )

    return history, periods


# ============================================================
# 判斷狀態
# ============================================================

def get_status(periods):

    count = periods["history_count"]

    if count >= 20:
        return "complete_20d"

    if count >= 10:
        return "complete_10d"

    if count >= 5:
        return "complete_5d"

    if count >= 1:
        return "partial"

    return "insufficient"


# ============================================================
# 取得測試 Universe
# ============================================================

def load_stocks():

    if TEST_MODE:

        section("TEST MODE：固定測試 5 檔")

        stocks = TEST_STOCKS

        log(
            "本次只處理以下 5 檔："
        )

        for stock in stocks:

            log(
                f"   {stock['symbol']} "
                f"{stock['name']}"
            )

        log("")
        log(
            "⚠️ 歷史資料天數不限制"
        )
        log(
            "⚠️ 實際抓到幾天就保留幾天"
        )

        return stocks

    raise RuntimeError(
        "正式全市場模式尚未開啟。"
        "目前故意鎖定 TEST_MODE。"
    )


# ============================================================
# 全部股票
# ============================================================

def fetch_all(stocks):

    section("開始取得主力買賣超")

    total = len(stocks)

    log(
        f"待處理股票：{total}"
    )

    # --------------------------------------------------------
    # 安全檢查
    #
    # 只要 TEST_MODE 開啟：
    # 絕對不能超過5檔
    # --------------------------------------------------------

    if TEST_MODE and total != 5:

        raise RuntimeError(
            f"TEST_MODE 異常："
            f"預期5檔，實際{total}檔"
        )

    session = requests.Session()

    results = {}

    complete_20d = 0
    complete_10d = 0
    complete_5d = 0
    partial = 0
    insufficient = 0

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        symbol = stock["symbol"]
        name = stock["name"]

        log("")
        log(
            f"[{index}/{total}] "
            f"{symbol} {name}"
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock["market"],
            "source": "CMoney",
            "main_force_1d": None,
            "main_force_5d": None,
            "main_force_10d": None,
            "main_force_20d": None,
            "history_count": 0,
            "status": "insufficient",
            "history": [],
            "error": None,
        }

        try:

            history, periods = fetch_stock(
                session,
                stock
            )

            record.update(periods)

            # ------------------------------------------------
            # 重要：
            # 不切 [:10]
            #
            # CMoney 抓到幾筆就保留幾筆
            # ------------------------------------------------

            record["history"] = history

            record["status"] = get_status(
                periods
            )

            status = record["status"]

            if status == "complete_20d":

                complete_20d += 1

            elif status == "complete_10d":

                complete_10d += 1

            elif status == "complete_5d":

                complete_5d += 1

            elif status == "partial":

                partial += 1

            else:

                insufficient += 1

            log(
                f"   實際歷史筆數："
                f"{record['history_count']}"
            )

            log(
                f"   主力1日："
                f"{record['main_force_1d']}"
            )

            log(
                f"   主力5日："
                f"{record['main_force_5d']}"
            )

            log(
                f"   主力10日："
                f"{record['main_force_10d']}"
            )

            log(
                f"   主力20日："
                f"{record['main_force_20d']}"
            )

            # 顯示實際日期
            if history:

                dates = [
                    row["date"]
                    for row in history
                ]

                log(
                    f"   最新日期："
                    f"{dates[0]}"
                )

                log(
                    f"   最舊日期："
                    f"{dates[-1]}"
                )

        except Exception as exc:

            insufficient += 1

            record["error"] = str(exc)

            log(
                f"   ❌ 取得失敗：{exc}"
            )

        results[symbol] = record

        time.sleep(
            REQUEST_DELAY
        )

    return (
        results,
        complete_20d,
        complete_10d,
        complete_5d,
        partial,
        insufficient,
    )


# ============================================================
# 驗證
# ============================================================

def validate(results):

    section("籌碼資料驗證")

    total = len(results)

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0
    valid_20d = 0

    for record in results.values():

        if record["main_force_1d"] is not None:
            valid_1d += 1

        if record["main_force_5d"] is not None:
            valid_5d += 1

        if record["main_force_10d"] is not None:
            valid_10d += 1

        if record["main_force_20d"] is not None:
            valid_20d += 1

    log(f"測試股票：{total}")
    log(f"主力1日有效：{valid_1d}")
    log(f"主力5日有效：{valid_5d}")
    log(f"主力10日有效：{valid_10d}")
    log(f"主力20日有效：{valid_20d}")

    if TEST_MODE and total != 5:

        raise RuntimeError(
            "TEST_MODE 驗證失敗："
            "不是5檔"
        )

    if valid_1d == 0:

        raise RuntimeError(
            "完全沒有有效1D資料"
        )

    log("")
    log(
        "✓ 資料結構驗證完成"
    )

    if valid_20d == total:

        log(
            "✓ 5檔全部具備20D"
        )

    else:

        log(
            f"⚠️ 目前只有 "
            f"{valid_20d}/{total} 檔具備20D"
        )

        log(
            "⚠️ 不會把10D冒充20D"
        )


# ============================================================
# 儲存
# ============================================================

def save_chip(results):

    section("寫入 Data/chip.json")

    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    now = datetime.now()

    output = {
        "schema_version": VERSION,
        "generated_at": now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "data_date": now.strftime(
            "%Y-%m-%d"
        ),
        "source": "CMoney",

        "definition": {
            "main_force": (
                "CMoney 主力進出之買賣超"
            ),
            "unit": "張",
            "positive": "主力買超",
            "negative": "主力賣超",

            "main_force_5d": (
                "最近5個交易日主力買賣超加總"
            ),

            "main_force_10d": (
                "最近10個交易日主力買賣超加總"
            ),

            "main_force_20d": (
                "最近20個交易日主力買賣超加總"
            ),
        },

        "test_mode": TEST_MODE,

        "universe_count": len(results),

        "stocks": results,
    }

    temp_file = CHIP_FILE.with_suffix(
        ".json.tmp"
    )

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

    # 寫入後重新讀取
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
            "chip.json 驗證失敗"
        )

    stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    if TEST_MODE and len(stocks) != 5:

        raise RuntimeError(
            f"chip.json 驗證失敗："
            f"預期5檔，實際{len(stocks)}檔"
        )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 建立成功"
    )

    log(
        f"股票數量：{len(stocks)}"
    )

    log(
        f"檔案：{CHIP_FILE}"
    )


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 72)
    log(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )
    log("=" * 72)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        # ----------------------------------------------------
        # 1. 股票
        # ----------------------------------------------------

        stocks = load_stocks()

        # ----------------------------------------------------
        # 2. 取得資料
        # ----------------------------------------------------

        (
            results,
            complete_20d,
            complete_10d,
            complete_5d,
            partial,
            insufficient,
        ) = fetch_all(
            stocks
        )

        # ----------------------------------------------------
        # 3. 驗證
        # ----------------------------------------------------

        validate(
            results
        )

        # ----------------------------------------------------
        # 4. 儲存
        # ----------------------------------------------------

        save_chip(
            results
        )

        elapsed = (
            time.time()
            - start_time
        )

        section(
            "fetch_chip.py 執行完成"
        )

        log(
            f"測試股票：{len(results)}"
        )

        log(
            f"20D完整：{complete_20d}"
        )

        log(
            f"10D完整：{complete_10d}"
        )

        log(
            f"5D完整：{complete_5d}"
        )

        log(
            f"部分：{partial}"
        )

        log(
            f"失敗：{insufficient}"
        )

        log(
            f"總耗時：{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        section(
            "❌ fetch_chip.py 執行失敗"
        )

        log(
            f"原因：{exc}"
        )

        return 1


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )