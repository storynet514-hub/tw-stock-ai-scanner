#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_chip.py V6.1

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
V6.1 架構
============================================================

1. 讀取 Data/universe.json
2. Universe 中所有股票全部進入掃描
3. 不因股票名稱空白而排除股票
4. 抓取 CMoney 首頁「買賣超」
5. 讀取上一版 Data/chip.json
6. 合併歷史資料
7. 以日期去重
8. 依日期排序
9. 保留最近 20 個交易日
10. 計算 1D / 5D / 10D / 20D

============================================================
V6.1 修正
============================================================

原 V6.1 問題：

universe.json：

total = 1985
listed_stocks = 1095
otc_stocks = 890

但 TPEx 890 檔股票的 name 欄位目前為空字串。

原程式：

if not name:
    continue

因此直接把 890 檔 TPEx 股票排除。

最後只剩：

1985 - 890 = 1095

造成 Action 實際只掃描 1095 檔。

本版本：

不再因 name 空白排除股票。

只要：

code / symbol 有效

就進入 Universe。

股票名稱：

1. Universe 有名稱 → 使用 Universe 名稱
2. Universe 名稱空白，但上一版 chip.json 已有名稱
   → 使用上一版已保存名稱
3. 兩者都沒有
   → 使用股票代號作為暫時名稱

注意：

名稱 fallback 只處理顯示名稱。

不影響 CMoney 買賣超資料來源。

============================================================
資料來源限制
============================================================

只使用：

CMoney 主力進出
「買賣超」

不探測 API
不猜 pagination
不使用 URL 延伸資料
不使用其他欄位補足 20D

============================================================
輸出
============================================================

Data/chip.json

chip.json 保存歷史資料。

每次 GitHub Actions：

本次抓到的新資料
+
上一版歷史資料

合併後保存。

因此 20D 會持續累積。

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

VERSION = "V6.1"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

CHIP_FILE = DATA_DIR / "chip.json"

REQUEST_TIMEOUT = 30

REQUEST_DELAY = 0.5

MAX_HISTORY = 20


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
# Universe
# ============================================================

def load_universe(previous_data=None):

    section("讀取台股 Universe")

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到 Universe：{UNIVERSE_FILE}"
        )

    try:

        with UNIVERSE_FILE.open(
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except Exception as exc:

        raise RuntimeError(
            f"無法讀取 universe.json：{exc}"
        )

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "universe.json 頂層不是 object"
        )

    items = data.get(
        "items",
        []
    )

    if not isinstance(
        items,
        list
    ):

        raise RuntimeError(
            "universe.json 的 items 不是 list"
        )

    # --------------------------------------------------------
    # Universe metadata
    # --------------------------------------------------------

    universe_total = data.get(
        "total"
    )

    listed_count = data.get(
        "listed_stocks"
    )

    otc_count = data.get(
        "otc_stocks"
    )

    log(
        f"Universe metadata total："
        f"{universe_total}"
    )

    log(
        f"Universe listed_stocks："
        f"{listed_count}"
    )

    log(
        f"Universe otc_stocks："
        f"{otc_count}"
    )

    # --------------------------------------------------------
    # 建立上一版名稱索引
    # --------------------------------------------------------

    previous_name_map = {}

    if isinstance(
        previous_data,
        dict
    ):

        previous_stocks = previous_data.get(
            "stocks",
            {}
        )

        if isinstance(
            previous_stocks,
            dict
        ):

            for symbol, record in previous_stocks.items():

                if not isinstance(
                    record,
                    dict
                ):
                    continue

                old_name = str(
                    record.get(
                        "name",
                        ""
                    )
                ).strip()

                if old_name:
                    previous_name_map[
                        str(symbol).strip().upper()
                    ] = old_name

    stocks = []

    seen = set()

    skipped_invalid = 0

    skipped_duplicate = 0

    empty_universe_name = 0

    # --------------------------------------------------------
    # 讀取全部 items
    # --------------------------------------------------------

    for item in items:

        if not isinstance(
            item,
            dict
        ):

            skipped_invalid += 1

            continue

        symbol = item.get(
            "code"
        )

        if symbol is None:

            symbol = item.get(
                "symbol"
            )

        if symbol is None:

            skipped_invalid += 1

            continue

        symbol = str(
            symbol
        ).strip().upper()

        # ----------------------------------------------------
        # 去除 .TW / .TWO
        # ----------------------------------------------------

        symbol = re.sub(
            r"\.(TW|TWO)$",
            "",
            symbol
        )

        # ----------------------------------------------------
        # 台股代號
        # ----------------------------------------------------

        if not re.fullmatch(
            r"[A-Z0-9]{4,6}",
            symbol
        ):

            skipped_invalid += 1

            continue

        if symbol in seen:

            skipped_duplicate += 1

            continue

        universe_name = str(
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

        # ----------------------------------------------------
        # 重要修正
        #
        # 絕對不能因為 name 空白而排除股票。
        #
        # 原本：
        #
        # if not name:
        #     continue
        #
        # 正是造成：
        #
        # 1985 -> 1095
        #
        # 的原因。
        # ----------------------------------------------------

        if not universe_name:

            empty_universe_name += 1

            # ------------------------------------------------
            # 若上一版已有正確名稱，沿用上一版名稱
            # ------------------------------------------------

            previous_name = previous_name_map.get(
                symbol
            )

            if previous_name:

                name = previous_name

            else:

                # --------------------------------------------
                # 沒有名稱時不得排除股票。
                #
                # 使用代號作為暫時識別名稱，
                # 不影響 CMoney 資料抓取。
                # --------------------------------------------

                name = symbol

        else:

            name = universe_name

        seen.add(
            symbol
        )

        stocks.append({
            "symbol": symbol,
            "name": name,
            "market": market,
            "universe_name": universe_name,
        })

    if not stocks:

        raise RuntimeError(
            "Universe 沒有任何有效股票"
        )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    stocks.sort(
        key=lambda x: x["symbol"]
    )

    # --------------------------------------------------------
    # 最終 Universe 驗證
    # --------------------------------------------------------

    log(
        f"Universe items："
        f"{len(items)}"
    )

    log(
        f"有效股票："
        f"{len(stocks)}"
    )

    log(
        f"名稱空白股票："
        f"{empty_universe_name}"
    )

    log(
        f"無效項目："
        f"{skipped_invalid}"
    )

    log(
        f"重複股票："
        f"{skipped_duplicate}"
    )

    # --------------------------------------------------------
    # 如果 metadata total 存在，檢查是否一致
    # --------------------------------------------------------

    if isinstance(
        universe_total,
        int
    ):

        if len(stocks) != universe_total:

            raise RuntimeError(
                "Universe 股票數量與 "
                "universe.json total 不一致："
                f"metadata={universe_total}, "
                f"actual={len(stocks)}"
            )

    # --------------------------------------------------------
    # 特別確認 2337 / 2426 / 2368 / 3081 / 3088
    # --------------------------------------------------------

    for target in [
        "2337",
        "2426",
        "2368",
        "3081",
        "3088",
    ]:

        target_stock = next(
            (
                stock
                for stock in stocks
                if stock["symbol"] == target
            ),
            None
        )

        if target_stock:

            log(
                f"{target} Universe名稱："
                f"{target_stock['name']} "
                f"(market={target_stock['market']})"
            )

    return stocks


# ============================================================
# Number
# ============================================================

def parse_number(text):

    if text is None:
        return None

    text = str(
        text
    ).strip()

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

        return float(
            match.group(0)
        )

    except Exception:

        return None


# ============================================================
# Date
# ============================================================

def normalize_date(text):

    if text is None:
        return None

    text = str(
        text
    ).strip()

    patterns = [
        r"(\d{4})/(\d{1,2})/(\d{1,2})",
        r"(\d{4})-(\d{1,2})-(\d{1,2})",
    ]

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            text
        )

        if not match:
            continue

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
# Header
# ============================================================

def normalize_header(text):

    if text is None:
        return ""

    text = str(
        text
    )

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

    header = normalize_header(
        text
    )

    return header == "買賣超"


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

            return (
                html,
                url
            )

        except Exception as exc:

            last_error = exc

    if last_error:
        raise last_error

    raise RuntimeError(
        "無法取得 CMoney 頁面"
    )


# ============================================================
# 找日期欄 / 買賣超欄
# ============================================================

def find_column_indexes(
    headers
):

    date_index = None

    force_index = None

    for index, header in enumerate(
        headers
    ):

        normalized = normalize_header(
            header
        )

        if (
            date_index is None
            and normalized == "日期"
        ):

            date_index = index

        if (
            force_index is None
            and is_main_force_header(
                normalized
            )
        ):

            force_index = index

    return (
        date_index,
        force_index
    )


# ============================================================
# 嚴格解析 CMoney 主力進出
# ============================================================

def parse_cmoney_main_force(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    tables = soup.find_all(
        "table"
    )

    target_table = None

    target_date_index = None

    target_force_index = None

    # --------------------------------------------------------
    # 找真正包含：
    #
    # 日期
    # 買賣超
    #
    # 的 table
    # --------------------------------------------------------

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

            (
                date_index,
                force_index
            ) = find_column_indexes(
                headers
            )

            if (
                date_index is not None
                and force_index is not None
            ):

                target_table = table

                target_date_index = (
                    date_index
                )

                target_force_index = (
                    force_index
                )

                break

        if target_table is not None:
            break

    if target_table is None:

        return []

    # --------------------------------------------------------
    # 解析資料
    # --------------------------------------------------------

    result = []

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

        result.append({
            "date": date_value,
            "main_force": force_value,
        })

    # --------------------------------------------------------
    # 日期去重
    # --------------------------------------------------------

    unique = {}

    for row in result:

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

    # --------------------------------------------------------
    # 最新日期在前
    # --------------------------------------------------------

    result.sort(
        key=lambda row:
            datetime.strptime(
                row["date"],
                "%Y/%m/%d"
            ),
        reverse=True
    )

    return result


# ============================================================
# 讀取上一版 chip.json
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
            "⚠️ 無法讀取上一版 chip.json："
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
            row.get(
                "date"
            )
        )

        value = parse_number(
            row.get(
                "main_force"
            )
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

        combined[
            date
        ] = float(
            value
        )

    # --------------------------------------------------------
    # 新資料覆蓋舊資料
    # 同一天以本次 CMoney 抓取值為準
    # --------------------------------------------------------

    for row in new_history:

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

        combined[
            date
        ] = float(
            value
        )

    result = [
        {
            "date": date,
            "main_force": value,
        }
        for date, value in combined.items()
    ]

    result.sort(
        key=lambda row:
            datetime.strptime(
                row["date"],
                "%Y/%m/%d"
            ),
        reverse=True
    )

    return result[:MAX_HISTORY]


# ============================================================
# 計算 1D / 5D / 10D / 20D
# ============================================================

def calculate_periods(
    history
):

    values = [
        float(
            row["main_force"]
        )
        for row in history
        if row.get(
            "main_force"
        ) is not None
    ]

    result = {
        "main_force_1d": None,
        "main_force_5d": None,
        "main_force_10d": None,
        "main_force_20d": None,
        "history_count": len(
            values
        ),
    }

    if len(values) >= 1:

        result[
            "main_force_1d"
        ] = round(
            values[0],
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

    if len(values) >= 20:

        result[
            "main_force_20d"
        ] = round(
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

    symbol = stock[
        "symbol"
    ]

    name = stock[
        "name"
    ]

    log(
        f"CMoney 主力買賣超："
        f"{symbol} {name}"
    )

    html, source_url = (
        request_cmoney_page(
            session,
            symbol
        )
    )

    # --------------------------------------------------------
    # 只解析首頁真正的「買賣超」
    # --------------------------------------------------------

    new_history = (
        parse_cmoney_main_force(
            html
        )
    )

    log(
        "CMoney 首頁有效「買賣超」："
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

    old_history = (
        get_previous_history(
            previous_data,
            symbol
        )
    )

    log(
        "上一版保存歷史："
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
        "合併後歷史："
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
        "主力1日："
        f"{periods['main_force_1d']}"
    )

    log(
        "主力5日："
        f"{periods['main_force_5d']}"
    )

    log(
        "主力10日："
        f"{periods['main_force_10d']}"
    )

    log(
        "主力20日："
        f"{periods['main_force_20d']}"
    )

    log(
        "歷史筆數："
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

        "market":
            stock.get(
                "market",
                ""
            ),

        "source": "CMoney",

        "source_url":
            source_url,

        "source_field":
            "買賣超",

        "main_force_1d":
            periods[
                "main_force_1d"
            ],

        "main_force_5d":
            periods[
                "main_force_5d"
            ],

        "main_force_10d":
            periods[
                "main_force_10d"
            ],

        "main_force_20d":
            periods[
                "main_force_20d"
            ],

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
# 失敗紀錄
# ============================================================

def build_error_record(
    stock,
    error,
    previous_data
):

    symbol = stock[
        "symbol"
    ]

    old_history = (
        get_previous_history(
            previous_data,
            symbol
        )
    )

    periods = calculate_periods(
        old_history
    )

    return {
        "symbol": symbol,

        "name":
            stock["name"],

        "market":
            stock.get(
                "market",
                ""
            ),

        "source":
            "CMoney",

        "source_url":
            CMONEY_URL.format(
                symbol=symbol
            ),

        "source_field":
            "買賣超",

        "main_force_1d":
            periods[
                "main_force_1d"
            ],

        "main_force_5d":
            periods[
                "main_force_5d"
            ],

        "main_force_10d":
            periods[
                "main_force_10d"
            ],

        "main_force_20d":
            periods[
                "main_force_20d"
            ],

        "history_count":
            len(old_history),

        "status":
            get_status(
                periods
            ),

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
        "開始 CMoney 主力買賣超更新"
    )

    total = len(
        stocks
    )

    log(
        "本版本為全市場 Universe 模式"
    )

    log(
        "讀取：Data/universe.json"
    )

    log(
        "不使用固定4檔測試"
    )

    log(
        f"本次掃描股票：{total}"
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

    success = 0

    failed = 0

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
            ""
        )

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

            results[
                symbol
            ] = record

            success += 1

            status = record[
                "status"
            ]

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
                f"❌ {symbol} "
                f"{name} 取得失敗："
                f"{exc}"
            )

            record = build_error_record(
                stock,
                exc,
                previous_data
            )

            results[
                symbol
            ] = record

            if record[
                "main_force_20d"
            ] is not None:

                complete += 1

            elif record[
                "main_force_10d"
            ] is not None:

                partial += 1

            elif record[
                "main_force_1d"
            ] is not None:

                partial += 1

            else:

                insufficient += 1

        time.sleep(
            REQUEST_DELAY
        )

    log("")
    log(
        f"掃描完成："
        f"{len(results)}/{total}"
    )

    log(
        f"成功請求："
        f"{success}"
    )

    log(
        f"失敗請求："
        f"{failed}"
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
    results,
    stocks
):

    section(
        "最終資料驗證"
    )

    expected_count = len(
        stocks
    )

    # --------------------------------------------------------
    # 最重要驗證：
    #
    # Universe 1985
    # results 必須也是 1985
    #
    # 不允許再次出現 1095。
    # --------------------------------------------------------

    if len(results) != expected_count:

        missing_symbols = [
            stock["symbol"]
            for stock in stocks
            if stock["symbol"] not in results
        ]

        preview = ", ".join(
            missing_symbols[:20]
        )

        raise RuntimeError(
            "輸出股票數量錯誤："
            f"expected={expected_count}, "
            f"actual={len(results)}，"
            f"缺少：{preview}"
        )

    valid_1d = 0

    valid_5d = 0

    valid_10d = 0

    valid_20d = 0

    for stock in stocks:

        symbol = stock[
            "symbol"
        ]

        if symbol not in results:

            raise RuntimeError(
                f"缺少 Universe 股票："
                f"{symbol}"
            )

        record = results[
            symbol
        ]

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
        # history 計算一致性
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
                    "計算驗證失敗："
                    f"actual={actual}, "
                    f"expected={expected}"
                )

        # ----------------------------------------------------
        # 名稱
        #
        # 這裡不再要求 Universe name 必須非空。
        # 因為目前 TPEx Universe 有空白名稱。
        #
        # 只要求 fetch 前後名稱一致。
        # ----------------------------------------------------

        expected_name = stock.get(
            "name",
            ""
        )

        actual_name = record.get(
            "name",
            ""
        )

        if actual_name != expected_name:

            raise RuntimeError(
                f"{symbol} 股票名稱不一致："
                f"Universe={expected_name} "
                f"chip={actual_name}"
            )

    log(
        f"Universe 股票："
        f"{expected_count}"
    )

    log(
        f"輸出股票："
        f"{len(results)}"
    )

    log(
        f"有效主力1D："
        f"{valid_1d}"
    )

    log(
        f"有效主力5D："
        f"{valid_5d}"
    )

    log(
        f"有效主力10D："
        f"{valid_10d}"
    )

    log(
        f"有效主力20D："
        f"{valid_20d}"
    )

    # --------------------------------------------------------
    # 5D / 10D 必須至少有資料
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
    # 20D
    # --------------------------------------------------------

    if valid_20d == expected_count:

        log(
            "✓ 全部股票已有完整20D"
        )

    else:

        log(
            f"ℹ️ 部分股票20D尚在歷史累積階段 "
            f"({valid_20d}/{expected_count})"
        )

    # --------------------------------------------------------
    # 特別確認 3081
    # --------------------------------------------------------

    if "3081" in results:

        log(
            "✓ 3081 名稱："
            f"{results['3081'].get('name')}"
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
            "full_market",

        "universe_file":
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

            "universe":
                len(stocks),

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
    # 寫入後重新讀取
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
            "chip.json 股票數量錯誤："
            f"expected={len(stocks)}, "
            f"actual={len(verify_stocks)}"
        )

    # --------------------------------------------------------
    # 驗證所有股票
    # --------------------------------------------------------

    for stock in stocks:

        symbol = stock[
            "symbol"
        ]

        if symbol not in verify_stocks:

            raise RuntimeError(
                f"chip.json 缺少 {symbol}"
            )

        record = verify_stocks[
            symbol
        ]

        # ----------------------------------------------------
        # 名稱驗證
        # ----------------------------------------------------

        if (
            record.get("name")
            != stock.get("name")
        ):

            raise RuntimeError(
                f"{symbol} 名稱驗證失敗："
                f"Universe={stock.get('name')} "
                f"chip={record.get('name')}"
            )

        # ----------------------------------------------------
        # history 驗證
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

        if len(history) > MAX_HISTORY:

            raise RuntimeError(
                f"{symbol} history 超過 "
                f"{MAX_HISTORY} 筆"
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
    # 最終股票數量再次驗證
    # --------------------------------------------------------

    if len(verify_stocks) != len(stocks):

        raise RuntimeError(
            "最終輸出股票數量不一致"
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
        "模式：全市場台股 Universe"
    )

    log(
        "禁止：5日集中 / 20日集中 / 家數差"
    )

    try:

        # ----------------------------------------------------
        # 先讀取上一版 chip
        #
        # 因為 Universe 某些股票目前沒有名稱，
        # 可以利用上一版保存的名稱。
        # ----------------------------------------------------

        previous_data = (
            load_previous_chip()
        )

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
                    "上一版 chip.json 股票："
                    f"{len(previous_stocks)}"
                )

        # ----------------------------------------------------
        # 讀取完整 Universe
        # ----------------------------------------------------

        stocks = load_universe(
            previous_data
        )

        # ----------------------------------------------------
        # 全市場抓取
        # ----------------------------------------------------

        (
            results,
            complete,
            partial,
            insufficient
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
            f"✓ fetch_chip.py "
            f"{VERSION} 完成"
        )

        log("=" * 72)

        log(
            f"Universe 股票："
            f"{len(stocks)}"
        )

        log(
            f"輸出股票："
            f"{len(results)}"
        )

        log(
            f"完整20D："
            f"{valid_20d}"
        )

        log(
            f"部分："
            f"{partial}"
        )

        log(
            f"不足："
            f"{insufficient}"
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
            f"❌ fetch_chip.py "
            f"{VERSION} 執行失敗"
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