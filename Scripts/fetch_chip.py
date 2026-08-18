#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V3.1

功能：
1. 讀取 Data/universe.json
2. 支援 build_universe.py 產生的：
      2330.TW
      2426.TW
      7794.TWO
3. 取得 CMoney 公開主力資料
4. 計算：
      主力 1 日
      主力 5 日
      主力 10 日
5. 輸出 Data/chip.json

主力定義：
券商分點主力買賣超，不是三大法人。

單位：
張。

正數 = 主力買超
負數 = 主力賣超
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

VERSION = "V3.1"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

MIN_HISTORY = 10


# ============================================================
# CMoney
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
# HTTP
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
# 輸出
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):

    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# Universe Symbol 正規化
# ============================================================

def normalize_symbol(value):

    if value is None:
        return ""

    symbol = str(value).strip().upper()

    if not symbol:
        return ""

    # --------------------------------------------------------
    # build_universe.py 格式：
    #
    # 2330.TW
    # 2426.TW
    # 7794.TWO
    #
    # 轉成 CMoney 所需：
    #
    # 2330
    # 2426
    # 7794
    # --------------------------------------------------------

    match = re.fullmatch(
        r"([0-9]{4,6})\.(TW|TWO)",
        symbol
    )

    if match:
        return match.group(1)

    # --------------------------------------------------------
    # 兼容純股票代號
    # --------------------------------------------------------

    if re.fullmatch(
        r"[0-9]{4,6}",
        symbol
    ):
        return symbol

    return ""


# ============================================================
# 讀取 Universe
# ============================================================

def load_universe():

    section("讀取台股全市場 Universe")

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    with UNIVERSE_FILE.open(
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise RuntimeError(
            "universe.json 格式錯誤："
            "頂層必須是 object"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json items "
            "不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("code")
            or item.get("ticker")
        )

        symbol = normalize_symbol(
            raw_symbol
        )

        if not symbol:
            continue

        if symbol in seen:
            continue

        name = str(
            item.get(
                "name",
                ""
            )
        ).strip()

        market = str(
            item.get(
                "market",
                ""
            )
        ).strip()

        stocks.append({
            "symbol": symbol,
            "name": name,
            "market": market,
        })

        seen.add(symbol)

    if not stocks:

        raise RuntimeError(
            "Universe 沒有任何合法股票"
        )

    log(
        f"Universe 股票數量："
        f"{len(stocks)}"
    )

    # 顯示前幾檔確認格式
    log("Universe 前 5 檔：")

    for stock in stocks[:5]:

        log(
            f"  {stock['symbol']} "
            f"{stock['name']} "
            f"{stock['market']}"
        )

    return stocks


# ============================================================
# 數字解析
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(text).strip()

    if not text:
        return None

    text = text.replace(
        ",",
        ""
    )

    text = text.replace(
        "%",
        ""
    )

    if text.upper() in {
        "N/A",
        "NA",
        "-",
        "--",
        "－",
        "—",
        "無",
    }:
        return None

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        text
    )

    if not match:
        return None

    try:

        return float(
            match.group(0)
        )

    except Exception:

        return None


# ============================================================
# 取得網頁
# ============================================================

def request_page(
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

            text = response.text

            if text:
                return text

        except Exception as exc:

            last_error = exc

    if last_error:

        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 日期判斷
# ============================================================

def parse_date_text(value):

    if not value:
        return None

    value = str(value).strip()

    patterns = [
        r"\d{4}/\d{1,2}/\d{1,2}",
        r"\d{4}-\d{1,2}-\d{1,2}",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            value
        )

        if match:

            normalized = value.replace(
                "-",
                "/"
            )

            try:

                return datetime.strptime(
                    normalized,
                    "%Y/%m/%d"
                )

            except Exception:

                return None

    return None


# ============================================================
# 解析主力歷史資料
# ============================================================

def parse_main_force_table(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    rows = []

    # ========================================================
    # 1. Table
    # ========================================================

    tables = soup.find_all(
        "table"
    )

    for table in tables:

        tr_list = table.find_all(
            "tr"
        )

        for tr in tr_list:

            cells = tr.find_all(
                ["th", "td"]
            )

            values = []

            for cell in cells:

                text = cell.get_text(
                    " ",
                    strip=True
                )

                if text:
                    values.append(
                        text
                    )

            if len(values) < 3:
                continue

            date_index = None

            for i, value in enumerate(
                values
            ):

                if parse_date_text(
                    value
                ) is not None:

                    date_index = i
                    break

            if date_index is None:
                continue

            force_value = None

            for i in range(
                date_index + 1,
                len(values)
            ):

                number = parse_number(
                    values[i]
                )

                if number is not None:

                    force_value = number
                    break

            if force_value is None:
                continue

            date_text = values[
                date_index
            ].replace(
                "-",
                "/"
            )

            rows.append({
                "date": date_text,
                "main_force": force_value
            })

    # ========================================================
    # 2. 整頁文字備援
    # ========================================================

    if not rows:

        text = soup.get_text(
            "\n",
            strip=True
        )

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        for i, line in enumerate(
            lines
        ):

            if parse_date_text(
                line
            ) is None:

                continue

            force_value = None

            for j in range(
                i + 1,
                min(
                    i + 8,
                    len(lines)
                )
            ):

                number = parse_number(
                    lines[j]
                )

                if number is not None:

                    force_value = number
                    break

            if force_value is None:
                continue

            rows.append({
                "date": line.replace(
                    "-",
                    "/"
                ),
                "main_force": force_value
            })

    # ========================================================
    # 3. 去除重複
    # ========================================================

    unique = {}

    for row in rows:

        date = row.get(
            "date"
        )

        value = row.get(
            "main_force"
        )

        if not date:
            continue

        if value is None:
            continue

        unique[date] = value

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value
        })

    # ========================================================
    # 4. 新 → 舊
    # ========================================================

    result.sort(
        key=lambda x: parse_date_text(
            x["date"]
        ) or datetime.min,
        reverse=True
    )

    return result


# ============================================================
# 單一股票
# ============================================================

def fetch_stock(
    session,
    stock
):

    symbol = stock[
        "symbol"
    ]

    html = request_page(
        session,
        symbol
    )

    history = parse_main_force_table(
        html
    )

    if not history:

        raise RuntimeError(
            "找不到主力歷史資料"
        )

    return history


# ============================================================
# 計算 1 / 5 / 10 日
# ============================================================

def calculate_periods(
    history
):

    values = [
        row["main_force"]
        for row in history
        if row.get(
            "main_force"
        ) is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "history_count": len(values),
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            sum(values[:1]),
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(values[:5]),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(values[:10]),
            2
        )

    return result


# ============================================================
# Status
# ============================================================

def get_status(data):

    d5 = data.get(
        "main_force_5d"
    )

    d10 = data.get(
        "main_force_10d"
    )

    if (
        d5 is not None
        and d10 is not None
    ):
        return "complete"

    if (
        d5 is not None
        or d10 is not None
    ):
        return "partial"

    return "insufficient"


# ============================================================
# 全市場抓取
# ============================================================

def fetch_all(stocks):

    section("開始取得主力資料")

    total = len(
        stocks
    )

    log(
        f"待處理股票：{total}"
    )

    session = requests.Session()

    results = {}

    complete = 0
    partial = 0
    insufficient = 0

    for index, stock in enumerate(
        stocks,
        start=1
    ):

        symbol = stock[
            "symbol"
        ]

        name = stock[
            "name"
        ]

        log(
            f"[{index}/{total}] "
            f"{symbol} "
            f"{name}"
        )

        record = {
            "symbol": symbol,
            "name": name,
            "market": stock[
                "market"
            ],
            "source": "CMoney",
            "main_force_1d": None,
            "main_force_5d": None,
            "main_force_10d": None,
            "history_count": 0,
            "status": "insufficient",
            "history": [],
            "error": None,
        }

        try:

            history = fetch_stock(
                session,
                stock
            )

            periods = calculate_periods(
                history
            )

            record.update(
                periods
            )

            record[
                "history"
            ] = history[:10]

            record[
                "status"
            ] = get_status(
                record
            )

            status = record[
                "status"
            ]

            if status == "complete":
                complete += 1

            elif status == "partial":
                partial += 1

            else:
                insufficient += 1

            log(
                "   主力1日："
                f"{record['main_force_1d']}"
            )

            log(
                "   主力5日："
                f"{record['main_force_5d']}"
            )

            log(
                "   主力10日："
                f"{record['main_force_10d']}"
            )

            if status != "complete":

                log(
                    "   ⚠️ 籌碼資料不足"
                )

        except Exception as exc:

            insufficient += 1

            record[
                "error"
            ] = str(exc)

            log(
                f"   ⚠️ 取得失敗：{exc}"
            )

        results[
            symbol
        ] = record

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
# 驗證
# ============================================================

def validate(
    results,
    total,
    complete,
    partial,
    insufficient
):

    section("籌碼資料驗證")

    log(
        f"Universe：{total}"
    )

    log(
        f"完整：{complete}"
    )

    log(
        f"部分：{partial}"
    )

    log(
        f"不足：{insufficient}"
    )

    if not results:

        raise RuntimeError(
            "沒有任何股票資料"
        )

    valid_5d = 0
    valid_10d = 0

    for record in results.values():

        if record.get(
            "main_force_5d"
        ) is not None:

            valid_5d += 1

        if record.get(
            "main_force_10d"
        ) is not None:

            valid_10d += 1

    log(
        f"主力5日有效：{valid_5d}"
    )

    log(
        f"主力10日有效：{valid_10d}"
    )

    if valid_5d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力5日資料"
        )

    log(
        "✓ 籌碼資料驗證通過"
    )


# ============================================================
# 寫入 chip.json
# ============================================================

def save_chip(
    results,
    total,
    complete,
    partial,
    insufficient
):

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
                "券商分點主力買賣超"
            ),
            "main_force_5d": (
                "最近5個交易日主力買賣超加總"
            ),
            "main_force_10d": (
                "最近10個交易日主力買賣超加總"
            ),
            "unit": "張",
            "positive": "主力買超",
            "negative": "主力賣超",
        },
        "universe_count": total,
        "statistics": {
            "complete": complete,
            "partial": partial,
            "insufficient": insufficient,
        },
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

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 建立成功"
    )

    log(
        f"檔案：{CHIP_FILE}"
    )

    log(
        f"大小："
        f"{CHIP_FILE.stat().st_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# Main
# ============================================================

def main():

    start_time = time.time()

    log("")
    log("=" * 64)
    log(
        f"台股 AI 選股系統 "
        f"fetch_chip.py {VERSION}"
    )
    log("=" * 64)

    log(
        "開始時間："
        + datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    try:

        stocks = load_universe()

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all(
            stocks
        )

        validate(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        save_chip(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        elapsed = (
            time.time()
            - start_time
        )

        log("")
        log("=" * 64)
        log(
            "✓ fetch_chip.py 執行完成"
        )
        log("=" * 64)

        log(
            f"完整：{complete}"
        )

        log(
            f"部分：{partial}"
        )

        log(
            f"不足：{insufficient}"
        )

        log(
            f"總耗時："
            f"{elapsed:.1f} 秒"
        )

        log(
            f"輸出：{CHIP_FILE}"
        )

        return 0

    except Exception as exc:

        log("")
        log("=" * 64)
        log(
            "❌ fetch_chip.py 執行失敗"
        )
        log("=" * 64)

        log(
            f"原因：{exc}"
        )

        if CHIP_FILE.exists():

            log(
                "⚠️ 保留既有 chip.json"
            )

        return 1


if __name__ == "__main__":
    sys.exit(main())