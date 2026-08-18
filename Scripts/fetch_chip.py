#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V4.0

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

而是 CMoney「主力進出」頁面的：

「買賣超」

單位：
張。

正數 = 主力買超
負數 = 主力賣超

主力 5 日：
最近 5 個交易日每日主力買賣超加總。

主力 10 日：
最近 10 個交易日每日主力買賣超加總。

============================================================
資料來源
============================================================

CMoney 公開個股主力進出頁面。

============================================================
重要修正 V4.0
============================================================

V3.0 的解析方式：

日期後第一個數字 = 主力買賣超

這個假設錯誤。

CMoney 實際表格：

日期 | 收盤價 | 買賣超 | 家數差 | 5日集中 | 20日集中

因此 V4.0：

1. 優先讀取 table header
2. 找到「買賣超」欄位
3. 依欄位位置取得真正的主力買賣超
4. 不再把收盤價當成主力買賣超
5. 若找不到明確 header，不使用猜測方式
6. 降級使用嚴格文字結構解析
7. 若仍無法確認，該股票標記失敗

============================================================
輸出
============================================================

Data/chip.json
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

VERSION = "V4.0"

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

# 備援 mobile
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
# 輸出工具
# ============================================================

def log(message=""):
    print(message, flush=True)


def section(title):

    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# Universe
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

    items = data.get("items", [])

    if not isinstance(items, list):

        raise RuntimeError(
            "universe.json items 不是 list"
        )

    stocks = []

    seen = set()

    for item in items:

        if not isinstance(item, dict):
            continue

        # ----------------------------------------------------
        # 優先使用 code
        # ----------------------------------------------------

        symbol = item.get("code")

        if symbol is None:
            symbol = item.get("symbol")

        if symbol is None:
            continue

        symbol = str(symbol).strip()

        # ----------------------------------------------------
        # symbol 可能是：
        #
        # 2337
        # 2337.TW
        # 2337.TWO
        #
        # CMoney 只需要 4~6 碼代號
        # ----------------------------------------------------

        symbol = symbol.upper()

        symbol = re.sub(
            r"\.(TW|TWO)$",
            "",
            symbol
        )

        if not re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol
        ):
            continue

        if symbol in seen:
            continue

        seen.add(symbol)

        stocks.append({
            "symbol": symbol,
            "name": str(
                item.get("name", "")
            ).strip(),
            "market": str(
                item.get("market", "")
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

    # --------------------------------------------------------
    # 中文/空白/符號
    # --------------------------------------------------------

    text = text.replace(
        ",",
        ""
    )

    text = text.replace(
        "張",
        ""
    )

    text = text.replace(
        "%",
        ""
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

    # --------------------------------------------------------
    # 數字
    # --------------------------------------------------------

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
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
# 日期
# ============================================================

DATE_PATTERNS = [
    r"\d{4}/\d{1,2}/\d{1,2}",
    r"\d{4}-\d{1,2}-\d{1,2}",
]


def normalize_date(text):

    text = str(text).strip()

    for pattern in DATE_PATTERNS:

        match = re.fullmatch(
            pattern,
            text
        )

        if match:

            return text.replace(
                "-",
                "/"
            )

    return None


# ============================================================
# Header 正規化
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(text)

    text = text.replace(
        "\n",
        ""
    )

    text = text.replace(
        "\r",
        ""
    )

    text = text.replace(
        " ",
        ""
    )

    text = text.replace(
        "\u3000",
        ""
    )

    return text.strip()


# ============================================================
# 判斷是否為「買賣超」欄位
# ============================================================

def is_main_force_header(text):

    header = normalize_header(text)

    # 精確優先
    if header == "買賣超":
        return True

    # CMoney 可能存在變體
    if (
        "買賣超" in header
        and "家數" not in header
        and "集中" not in header
    ):
        return True

    return False


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
# 解析 Table
#
# 正確結構：
#
# 日期 | 收盤價 | 買賣超 | 家數差 | 5日集中 | 20日集中
#
# ============================================================

def parse_table_with_header(
    soup
):

    tables = soup.find_all(
        "table"
    )

    for table in tables:

        rows = table.find_all(
            "tr"
        )

        if not rows:
            continue

        # ----------------------------------------------------
        # 找 header
        # ----------------------------------------------------

        header_row = None
        header_cells = None

        for tr in rows[:10]:

            cells = tr.find_all(
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

            has_date = any(
                h == "日期"
                or "日期" in h
                for h in headers
            )

            has_main_force = any(
                is_main_force_header(h)
                for h in headers
            )

            if (
                has_date
                and has_main_force
            ):

                header_row = tr
                header_cells = cells
                break

        if header_row is None:
            continue

        headers = [
            normalize_header(
                cell.get_text(
                    " ",
                    strip=True
                )
            )
            for cell in header_cells
        ]

        # ----------------------------------------------------
        # 找欄位
        # ----------------------------------------------------

        date_index = -1
        force_index = -1

        for index, header in enumerate(
            headers
        ):

            if (
                date_index < 0
                and (
                    header == "日期"
                    or "日期" in header
                )
            ):

                date_index = index

            if (
                force_index < 0
                and is_main_force_header(
                    header
                )
            ):

                force_index = index

        if (
            date_index < 0
            or force_index < 0
        ):
            continue

        log(
            f"   ✓ 找到 CMoney 主力表格："
            f"日期欄={date_index}, "
            f"買賣超欄={force_index}"
        )

        result = []

        # ----------------------------------------------------
        # 逐列解析
        # ----------------------------------------------------

        for tr in rows:

            if tr is header_row:
                continue

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

            # ------------------------------------------------
            # 日期
            # ------------------------------------------------

            date_text = normalize_date(
                values[date_index]
            )

            if not date_text:
                continue

            # ------------------------------------------------
            # 主力買賣超
            # ------------------------------------------------

            force_value = parse_number(
                values[force_index]
            )

            if force_value is None:
                continue

            result.append({
                "date": date_text,
                "main_force": force_value
            })

        if result:
            return result

    return []


# ============================================================
# 嚴格文字備援
#
# 只有在 HTML table 無法直接解析時使用。
#
# 不再使用：
# 「日期後第一個數字」
#
# 而是使用：
# 日期 → 收盤價 → 買賣超
#
# ============================================================

def parse_text_fallback(
    soup
):

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

    for i, line in enumerate(
        lines
    ):

        date_text = normalize_date(
            line
        )

        if not date_text:
            continue

        # ----------------------------------------------------
        # 日期後尋找：
        #
        # 第一個數字 = 收盤價
        # 第二個數字 = 買賣超
        #
        # 並且最多只搜尋 6 行
        # ----------------------------------------------------

        numeric_values = []

        for j in range(
            i + 1,
            min(
                i + 7,
                len(lines)
            )
        ):

            number = parse_number(
                lines[j]
            )

            if number is not None:

                numeric_values.append(
                    number
                )

            if len(numeric_values) >= 2:
                break

        if len(numeric_values) < 2:
            continue

        # 第二個數字才是買賣超
        force_value = numeric_values[1]

        result.append({
            "date": date_text,
            "main_force": force_value
        })

    return result


# ============================================================
# 解析主力歷史資料
# ============================================================

def parse_main_force_table(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # --------------------------------------------------------
    # 第一優先：
    # 明確 Header 定位
    # --------------------------------------------------------

    rows = parse_table_with_header(
        soup
    )

    if rows:

        return clean_history(
            rows
        )

    # --------------------------------------------------------
    # 第二優先：
    # 嚴格文字結構
    # --------------------------------------------------------

    rows = parse_text_fallback(
        soup
    )

    if rows:

        return clean_history(
            rows
        )

    return []


# ============================================================
# 清理歷史資料
# ============================================================

def clean_history(
    rows
):

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

        try:

            datetime.strptime(
                date,
                "%Y/%m/%d"
            )

        except Exception:

            continue

        unique[date] = float(
            value
        )

    result = []

    for date, value in unique.items():

        result.append({
            "date": date,
            "main_force": value
        })

    result.sort(
        key=lambda x: datetime.strptime(
            x["date"],
            "%Y/%m/%d"
        ),
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
            "找不到 CMoney 主力買賣超資料"
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
        "history_count": len(
            values
        ),
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
# 狀態
# ============================================================

def get_status(
    data
):

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
# 全市場
# ============================================================

def fetch_all(
    stocks
):

    section(
        "開始取得主力買賣超"
    )

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

            # ------------------------------------------------
            # 只保留最近 10 個交易日
            # ------------------------------------------------

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
                "   主力1日：N/A"
            )

            log(
                "   主力5日：N/A"
            )

            log(
                "   主力10日：N/A"
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

    section(
        "籌碼資料驗證"
    )

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

    valid_1d = 0
    valid_5d = 0
    valid_10d = 0

    for record in results.values():

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

    log(
        f"主力1日有效：{valid_1d}"
    )

    log(
        f"主力5日有效：{valid_5d}"
    )

    log(
        f"主力10日有效：{valid_10d}"
    )

    if not results:

        raise RuntimeError(
            "沒有任何股票資料"
        )

    if valid_5d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力5日資料"
        )

    if valid_10d == 0:

        raise RuntimeError(
            "本次完全沒有取得有效主力10日資料"
        )

    log(
        "✓ 籌碼資料驗證通過"
    )


# ============================================================
# 儲存
# ============================================================

def save_chip(
    results,
    total,
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
            "chip.json 寫入後驗證失敗"
        )

    verify_stocks = verify.get(
        "stocks"
    )

    if not isinstance(
        verify_stocks,
        dict
    ):

        raise RuntimeError(
            "chip.json stocks 格式錯誤"
        )

    if len(verify_stocks) != len(
        results
    ):

        raise RuntimeError(
            "chip.json 股票數量驗證失敗"
        )

    temp_file.replace(
        CHIP_FILE
    )

    log(
        "✓ chip.json 建立成功"
    )

    log(
        f"股票：{len(results)}"
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

        # DEBUG：確認 2337 / 2426 是否進入 Chip Universe

        # ----------------------------------------------------

        log("")

        log("=" * 64)

        log("DEBUG：檢查指定股票是否進入 fetch_chip Universe")

        log("=" * 64)

        for target in ["2337", "2426"]:

            matches = [

                stock

                for stock in stocks

                if stock.get("symbol") == target

            ]

            if matches:

                log(

                    f"✓ {target} 已進入 Chip Universe"

                )

                for stock in matches:

                    log(

                        f"  symbol={stock.get('symbol')}, "

                        f"name={stock.get('name')}, "

                        f"market={stock.get('market')}"

                    )

            else:

                log(

                    f"❌ {target} 不在 Chip Universe"

                )

        log("")
        # ----------------------------------------------------
        # 2. CMoney
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


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )