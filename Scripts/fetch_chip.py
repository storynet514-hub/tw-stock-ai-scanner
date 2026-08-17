#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V3.0

============================================================
核心功能
============================================================

取得台股全市場：

1. 每日主力買賣超
2. 主力 5 日買賣超
3. 主力 10 日買賣超

============================================================
重要定義
============================================================

本程式的「主力」：

不是三大法人。

不是：

- 外資
- 投信
- 自營商

而是以公開券商分點資料所形成的「主力買賣超」數值。

主力 5 日：
最近 5 個交易日每日主力買賣超加總。

主力 10 日：
最近 10 個交易日每日主力買賣超加總。

單位：
張。

正數 = 主力買超
負數 = 主力賣超

============================================================
資料來源
============================================================

CMoney 公開個股主力進出頁面。

不使用 WantGoo。

============================================================
輸出
============================================================

Data/chip.json
"""

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

VERSION = "V3.0"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.20

# 每檔股票至少需要幾筆歷史資料
MIN_HISTORY = 10

# CMoney
CMONEY_URL = (
    "https://www.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

# 備援 mobile 頁面
CMONEY_MOBILE_URL = (
    "https://mobile.cmoney.tw/forum/stock/"
    "{symbol}?s=main-force"
)

# User-Agent
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
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
            "universe.json 格式錯誤"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json items 不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = item.get(
            "symbol"
        )

        if symbol is None:
            continue

        symbol = str(
            symbol
        ).strip()

        # 台股普通股票 4 碼
        if not re.fullmatch(
            r"\d{4}",
            symbol
        ):
            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        stocks.append({
            "symbol": symbol,
            "name": str(
                item.get(
                    "name",
                    ""
                )
            ).strip(),
            "market": str(
                item.get(
                    "market",
                    ""
                )
            ).strip(),
        })

    if not stocks:

        raise RuntimeError(
            "Universe 沒有任何合法股票"
        )

    log(
        f"Universe 股票數量："
        f"{len(stocks)}"
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

    # 移除千分位
    text = text.replace(
        ",",
        ""
    )

    # 移除 %
    text = text.replace(
        "%",
        ""
    )

    # 處理中文 N/A
    if text.upper() in [
        "N/A",
        "NA",
        "-",
        "--",
        "－",
        "—"
    ]:
        return None

    # 保留負號、小數
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

            if not text:
                continue

            return text

        except Exception as exc:

            last_error = exc

    if last_error:

        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 解析主力歷史資料
# ============================================================

def parse_main_force_table(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    rows = []

    # --------------------------------------------------------
    # 找所有 table
    # --------------------------------------------------------

    tables = soup.find_all(
        "table"
    )

    for table in tables:

        tr_list = table.find_all(
            "tr"
        )

        if not tr_list:
            continue

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

            # ------------------------------------------------
            # 找日期
            # ------------------------------------------------

            date_index = None

            for i, value in enumerate(
                values
            ):

                if re.fullmatch(
                    r"\d{4}/\d{1,2}/\d{1,2}",
                    value
                ):

                    date_index = i
                    break

                if re.fullmatch(
                    r"\d{4}-\d{1,2}-\d{1,2}",
                    value
                ):

                    date_index = i
                    break

            if date_index is None:
                continue

            # ------------------------------------------------
            # 日期後第一個數字通常就是
            # 主力買賣超
            # ------------------------------------------------

            force_value = None

            for i in range(
                date_index + 1,
                len(values)
            ):

                candidate = values[i]

                number = parse_number(
                    candidate
                )

                if number is not None:

                    force_value = number

                    break

            if force_value is None:
                continue

            date_text = values[
                date_index
            ]

            date_text = date_text.replace(
                "-",
                "/"
            )

            rows.append({
                "date": date_text,
                "main_force": force_value
            })

    # --------------------------------------------------------
    # 如果 table 沒抓到
    # 嘗試整頁文字解析
    # --------------------------------------------------------

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

            if not re.fullmatch(
                r"\d{4}/\d{1,2}/\d{1,2}",
                line
            ):
                continue

            # 往後找數字
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
                "date": line,
                "main_force": force_value
            })

    # --------------------------------------------------------
    # 去除重複日期
    # --------------------------------------------------------

    unique = {}

    for row in rows:

        date = row[
            "date"
        ]

        unique[date] = row[
            "main_force"
        ]

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value
        })

    # 日期由新到舊
    result.sort(
        key=lambda x: datetime.strptime(
            x["date"],
            "%Y/%m/%d"
        ),
        reverse=True
    )

    return result


# ============================================================
# 取得單一股票
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
# 計算 5 / 10 日
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
        "history_count": len(
            values
        ),
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            sum(
                values[:1]
            ),
            2
        )

    if len(values) >= 5:

        result[
            "main_force_5d"
        ] = round(
            sum(
                values[:5]
            ),
            2
        )

    if len(values) >= 10:

        result[
            "main_force_10d"
        ] = round(
            sum(
                values[:10]
            ),
            2
        )

    return result


# ============================================================
# 判斷資料狀態
# ============================================================

def get_status(data):

    d5 = data.get(
        "main_force_5d"
    )

    d10 = data.get(
        "main_force_10d"
    )

    if d5 is not None and d10 is not None:

        return "complete"

    if d5 is not None or d10 is not None:

        return "partial"

    return "insufficient"


# ============================================================
# 主力資料抓取
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

            # 只保留最近 10 筆
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

            d5 = record[
                "main_force_5d"
            ]

            d10 = record[
                "main_force_10d"
            ]

            log(
                f"   主力5日："
                f"{d5 if d5 is not None else 'N/A'}"
            )

            log(
                f"   主力10日："
                f"{d10 if d10 is not None else 'N/A'}"
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

            log(
                "   主力5日：N/A"
            )

            log(
                "   主力10日：N/A"
            )

            log(
                "   ⚠️ 籌碼資料不足"
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

    log(
        f"主力5/10日完整：{complete}"
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

    # 至少 5 日資料必須存在
    if valid_5d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力5日資料。"
        )

    # 不要求 100% 成功
    # 避免單一來源短暫異常導致資料完全中斷

    log(
        "✓ 籌碼資料驗證通過"
    )


# ============================================================
# 儲存 chip.json
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
        f"✓ chip.json 建立成功"
    )

    log(
        f"檔案：{CHIP_FILE}"
    )

    log(
        f"大小："
        f"{CHIP_FILE.stat().st_size / 1024 / 1024:.2f} MB"
    )


# ============================================================
# 主程式
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

        # ----------------------------------------------------
        # 1. Universe
        # ----------------------------------------------------

        stocks = load_universe()

        # ----------------------------------------------------
        # 2. 抓主力
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient
        ) = fetch_all(
            stocks
        )

        # ----------------------------------------------------
        # 3. 驗證
        # ----------------------------------------------------

        validate(
            results,
            len(stocks),
            complete,
            partial,
            insufficient
        )

        # ----------------------------------------------------
        # 4. 寫檔
        # ----------------------------------------------------

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
