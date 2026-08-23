#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
fetch_prices.py V4.2

============================================================
用途
============================================================

1. 讀取 Data/universe.json
2. 相容 build_universe.py V10.x Universe schema
3. 正確辨識：
   - TWSE 上市 → .TW
   - TPEX 上櫃 → .TWO
4. 僅抓 Stock，不抓 ETF
5. 從 Yahoo Finance 取得歷史日線
6. 保留技術分析必要欄位：
   - date
   - high
   - low
   - close
   - volume
7. 分檔寫入 Data/prices/
8. 每 100 檔股票一個 JSON
9. 產生 Data/prices/manifest.json
10. 驗證成功率
11. 驗證所有分檔
12. 驗證 manifest
13. 全部驗證通過後才替換舊 Data/prices/
14. 不產生 Data/prices.json

============================================================
V4.2 核心修正
============================================================

修正 V4.1：

ERROR：
    universe.json 缺少有效 items

原因：
    fetch_prices.py 將 Universe schema 寫死為：

        {
            "items": [...]
        }

但 build_universe.py V10.x 已經負責建立新的 Universe
結構，因此 fetch_prices 必須與 build_universe 的正式 schema
相容。

V4.2：

✓ 相容 items=list
✓ 相容 items=dict
✓ 相容 stocks=list
✓ 相容 stocks=dict
✓ 相容 etfs=list
✓ 相容 etfs=dict
✓ 支援 full_symbol
✓ 支援 symbol
✓ 支援 code
✓ 支援 stock_code
✓ 支援多種 market 欄位
✓ 支援多種 type 欄位
✓ Stock / ETF 分離
✓ fetch_prices 僅抓 Stock
✓ ETF 不會混入個股價格資料

============================================================
安全機制
============================================================

✓ Universe 必須有效
✓ Universe 不得為空
✓ 股票數量必須合理
✓ 成功率低於 80% 不更新
✓ 任一股票歷史資料不足不計入成功
✓ 任一分檔寫入失敗不更新
✓ 任一分檔驗證失敗不更新
✓ Manifest 驗證失敗不更新
✓ 單檔超過 80 MB 不更新
✓ 使用暫存目錄建立完整資料
✓ 全部驗證通過後才替換正式 Data/prices/
✓ 不會產生半成品正式資料
✓ 任何未預期錯誤 exit code 1
"""

from __future__ import annotations

import json
import math
import shutil
import sys
import tempfile
import time

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# 基本設定
# ============================================================

VERSION = "V4.2"

SCHEMA_VERSION = "prices-v4.2"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 0.80

MIN_HISTORY_ROWS = 100

MAX_FILE_SIZE_MB = 80.0

MAX_FILE_SIZE_BYTES = int(
    MAX_FILE_SIZE_MB * 1024 * 1024
)


# ============================================================
# HTTP Session
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0 Safari/537.36"
        ),
        "Accept": (
            "application/json,"
            "text/plain,"
            "*/*"
        ),
        "Accept-Language": (
            "zh-TW,zh;q=0.9,"
            "en-US;q=0.8,en;q=0.7"
        ),
        "Connection": "keep-alive",
    }
)


# ============================================================
# Log
# ============================================================

def log(message: str = "") -> None:
    print(message, flush=True)


def section(title: str) -> None:
    log("")
    log("=" * 64)
    log(title)
    log("=" * 64)


# ============================================================
# JSON
# ============================================================

def load_json(path: Path) -> Any:

    with path.open(
        "r",
        encoding="utf-8-sig",
    ) as file:

        return json.load(file)


def save_json(
    path: Path,
    data: Any,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


# ============================================================
# 數值
# ============================================================

def safe_float(
    value: Any,
) -> Optional[float]:

    if value is None:
        return None

    try:

        number = float(value)

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def safe_int(
    value: Any,
) -> int:

    if value is None:
        return 0

    try:

        number = float(value)

        if not math.isfinite(number):
            return 0

        return int(number)

    except Exception:

        return 0


# ============================================================
# 日期
# ============================================================

def date_to_timestamp(
    date_string: str,
) -> int:

    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d",
    )

    dt = dt.replace(
        tzinfo=timezone.utc
    )

    return int(
        dt.timestamp()
    )


# ============================================================
# 文字清理
# ============================================================

def clean_text(
    value: Any,
) -> str:

    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u3000", " ")
        .strip()
    )


# ============================================================
# 股票代號
# ============================================================

def extract_code(
    value: Any,
) -> Optional[str]:

    if value is None:
        return None

    text = clean_text(
        value
    ).upper()

    if not text:
        return None

    if text.endswith(".TWO"):

        text = text[:-4]

    elif text.endswith(".TW"):

        text = text[:-3]

    if not text.isdigit():
        return None

    if not (
        4 <= len(text) <= 6
    ):
        return None

    return text


# ============================================================
# 完整 Yahoo symbol
# ============================================================

def extract_full_symbol(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    keys = [
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
        "ticker",
    ]

    for key in keys:

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if text.endswith(
            ".TW"
        ):

            code = extract_code(
                text
            )

            if code:
                return code + ".TW"

        if text.endswith(
            ".TWO"
        ):

            code = extract_code(
                text
            )

            if code:
                return code + ".TWO"

    return None


# ============================================================
# 市場判斷
# ============================================================

def detect_market(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    keys = [
        "market",
        "exchange",
        "market_type",
        "marketType",
        "board",
        "市場",
        "市場別",
        "交易所",
        "掛牌市場",
        "上市櫃",
        "上市櫃別",
    ]

    for key in keys:

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if not text:
            continue

        # ----------------------------------------------------
        # TPEX
        # ----------------------------------------------------

        if (
            text == "TPEX"
            or text == "TWO"
            or text == "OTC"
            or text == "O"
            or "TPEX" in text
            or "OTC" in text
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):

            return "TWO"

        # ----------------------------------------------------
        # TWSE
        # ----------------------------------------------------

        if (
            text == "TWSE"
            or text == "TW"
            or text == "TSE"
            or text == "上市"
            or "TWSE" in text
            or "上市" in text
        ):

            return "TW"

    return None


# ============================================================
# Type 判斷
# ============================================================

def detect_type(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):

        return "Stock"

    keys = [
        "type",
        "security_type",
        "securityType",
        "category",
        "類型",
        "商品類型",
        "證券類型",
    ]

    for key in keys:

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if (
            text == "ETF"
            or "ETF" in text
        ):

            return "ETF"

        if (
            text == "STOCK"
            or "STOCK" in text
            or "股票" in text
        ):

            return "Stock"

    return "Stock"


# ============================================================
# 股票名稱
# ============================================================

def extract_name(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):

        return ""

    keys = [
        "name",
        "stock_name",
        "company_name",
        "security_name",
        "名稱",
        "證券名稱",
        "公司名稱",
    ]

    for key in keys:

        value = item.get(key)

        text = clean_text(
            value
        )

        if text:
            return text

    return ""


# ============================================================
# Yahoo Symbol
# ============================================================

def build_yahoo_symbol(
    code: str,
    market: Optional[str],
    full_symbol: Optional[str] = None,
) -> Optional[str]:

    if full_symbol:

        full = clean_text(
            full_symbol
        ).upper()

        if (
            full.endswith(".TW")
            or full.endswith(".TWO")
        ):

            return full

    if not code:
        return None

    if market == "TWO":

        return code + ".TWO"

    return code + ".TW"


# ============================================================
# Universe item 正規化
# ============================================================

def normalize_item(
    item: Any,
    forced_type: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    # --------------------------------------------------------
    # 字串形式
    # --------------------------------------------------------

    if isinstance(
        item,
        str,
    ):

        full_symbol = (
            clean_text(item)
            .upper()
        )

        code = extract_code(
            full_symbol
        )

        if not code:
            return None

        if full_symbol.endswith(
            ".TWO"
        ):

            market = "TWO"

        else:

            market = "TW"

        return {
            "symbol": build_yahoo_symbol(
                code,
                market,
                full_symbol,
            ),
            "code": code,
            "market": market,
            "name": "",
            "type": forced_type
            or "Stock",
        }

    # --------------------------------------------------------
    # Dict
    # --------------------------------------------------------

    if not isinstance(
        item,
        dict,
    ):

        return None

    full_symbol = extract_full_symbol(
        item
    )

    code = None

    possible_code_keys = [
        "symbol",
        "code",
        "stock_id",
        "stock_code",
        "ticker",
        "證券代號",
        "有價證券代號",
        "代號",
    ]

    for key in possible_code_keys:

        code = extract_code(
            item.get(key)
        )

        if code:
            break

    if code is None and full_symbol:

        code = extract_code(
            full_symbol
        )

    if code is None:
        return None

    market = detect_market(
        item
    )

    if market is None and full_symbol:

        if full_symbol.endswith(
            ".TWO"
        ):

            market = "TWO"

        elif full_symbol.endswith(
            ".TW"
        ):

            market = "TW"

    if market is None:

        market = "TW"

    security_type = (
        forced_type
        or detect_type(item)
    )

    yahoo_symbol = build_yahoo_symbol(
        code,
        market,
        full_symbol,
    )

    if not yahoo_symbol:
        return None

    return {
        "symbol": yahoo_symbol,
        "code": code,
        "market": market,
        "name": extract_name(item),
        "type": security_type,
    }


# ============================================================
# Universe 容器轉換
# ============================================================

def expand_collection(
    collection: Any,
    forced_type: Optional[str] = None,
) -> List[Any]:

    if collection is None:

        return []

    # --------------------------------------------------------
    # list
    # --------------------------------------------------------

    if isinstance(
        collection,
        list,
    ):

        return collection

    # --------------------------------------------------------
    # dict
    #
    # 例如：
    #
    # {
    #     "2337": {...},
    #     "2426": {...}
    # }
    #
    # 將 key 補入 symbol/code
    # --------------------------------------------------------

    if isinstance(
        collection,
        dict,
    ):

        result = []

        for key, value in collection.items():

            if isinstance(
                value,
                dict,
            ):

                item = dict(value)

                if not any(
                    item.get(k)
                    for k in (
                        "symbol",
                        "code",
                        "stock_id",
                        "stock_code",
                        "ticker",
                        "full_symbol",
                    )
                ):

                    item["symbol"] = str(
                        key
                    )

                result.append(
                    item
                )

            elif isinstance(
                value,
                str,
            ):

                result.append(
                    value
                )

            else:

                result.append(
                    {
                        "symbol": str(key),
                        "type": forced_type
                        or "Stock",
                    }
                )

        return result

    return []


# ============================================================
# Universe 載入
# ============================================================

def load_universe() -> Dict[str, Any]:

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            f"找不到：{UNIVERSE_FILE}"
        )

    try:

        data = load_json(
            UNIVERSE_FILE
        )

    except Exception as exc:

        raise RuntimeError(
            f"universe.json 讀取失敗：{exc}"
        ) from exc

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            "universe.json 格式錯誤："
            "頂層必須是 object"
        )

    log(
        f"Schema version："
        f"{data.get('schema_version')}"
    )

    log(
        f"Universe count："
        f"{data.get('universe_count')}"
    )

    log(
        f"Stock count："
        f"{data.get('stock_count')}"
    )

    log(
        f"ETF count："
        f"{data.get('etf_count')}"
    )

    # --------------------------------------------------------
    # V10.x 正式來源
    # --------------------------------------------------------

    raw_items = data.get(
        "items"
    )

    if raw_items is not None:

        items = expand_collection(
            raw_items
        )

        log(
            f"Universe items："
            f"{len(items)}"
        )

        if items:

            return {
                "root": data,
                "items": items,
            }

    # --------------------------------------------------------
    # 相容 stocks / etfs 分開的 schema
    # --------------------------------------------------------

    stocks = expand_collection(
        data.get("stocks"),
        forced_type="Stock",
    )

    etfs = expand_collection(
        data.get("etfs"),
        forced_type="ETF",
    )

    combined = (
        stocks
        + etfs
    )

    if combined:

        log(
            f"Universe stocks："
            f"{len(stocks)}"
        )

        log(
            f"Universe ETFs："
            f"{len(etfs)}"
        )

        return {
            "root": data,
            "items": combined,
        }

    # --------------------------------------------------------
    # 相容 data.items / data.stocks
    # --------------------------------------------------------

    nested = data.get(
        "data"
    )

    if isinstance(
        nested,
        dict,
    ):

        nested_items = expand_collection(
            nested.get("items")
        )

        if not nested_items:

            nested_items = expand_collection(
                nested.get("stocks"),
                forced_type="Stock",
            )

        if nested_items:

            log(
                f"Nested Universe："
                f"{len(nested_items)}"
            )

            return {
                "root": data,
                "items": nested_items,
            }

    raise RuntimeError(
        "universe.json 找不到有效 Universe 資料；"
        "需要 items / stocks / data.items / data.stocks"
    )


# ============================================================
# 解析 Universe
# ============================================================

def extract_symbols(
    universe: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:

    section(
        "解析 Universe"
    )

    items = universe.get(
        "items",
        [],
    )

    if not isinstance(
        items,
        list,
    ):

        raise RuntimeError(
            "Universe normalized items 必須是 list"
        )

    all_records = {}

    stock_count = 0
    etf_count = 0

    for item in items:

        parsed = normalize_item(
            item
        )

        if parsed is None:
            continue

        security_type = parsed[
            "type"
        ]

        if security_type == "ETF":

            etf_count += 1

        else:

            stock_count += 1

            symbol = parsed[
                "symbol"
            ]

            if symbol not in all_records:

                all_records[
                    symbol
                ] = parsed

            else:

                old_name = all_records[
                    symbol
                ].get(
                    "name",
                    "",
                )

                new_name = parsed.get(
                    "name",
                    "",
                )

                if (
                    not old_name
                    and new_name
                ):

                    all_records[
                        symbol
                    ]["name"] = new_name

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    records = dict(
        sorted(
            all_records.items(),
            key=lambda pair: (
                0
                if pair[1]["market"] == "TW"
                else 1,
                pair[1]["code"],
            ),
        )
    )

    twse_count = sum(
        1
        for item in records.values()
        if item["market"] == "TW"
    )

    tpex_count = sum(
        1
        for item in records.values()
        if item["market"] == "TWO"
    )

    log(
        f"Universe item："
        f"{len(items)}"
    )

    log(
        f"Stock："
        f"{len(records)}"
    )

    log(
        f"ETF："
        f"{etf_count}"
    )

    log(
        f"TWSE："
        f"{twse_count}"
    )

    log(
        f"TPEX："
        f"{tpex_count}"
    )

    if not records:

        raise RuntimeError(
            "Universe 沒有解析出任何 Stock"
        )

    # --------------------------------------------------------
    # 固定測試股票
    # --------------------------------------------------------

    required_tests = {
        "2337": "旺宏",
        "2426": "鼎元",
        "2368": "金像電",
        "3081": "聯亞",
    }

    log("")
    log(
        "固定測試股票："
    )

    for code, expected_name in (
        required_tests.items()
    ):

        candidates = [
            item
            for item in records.values()
            if item["code"] == code
        ]

        if not candidates:

            raise RuntimeError(
                f"Universe 缺少固定測試股票："
                f"{code}"
            )

        item = candidates[0]

        log(
            f"{code} | "
            f"{item['name']} | "
            f"{item['market']}"
        )

        if code == "3081":

            if item["market"] != "TWO":

                raise RuntimeError(
                    "3081 市場錯誤："
                    f"{item['market']}"
                )

            if (
                item["name"]
                and item["name"] != expected_name
            ):

                log(
                    "⚠️ 3081 名稱目前為："
                    f"{item['name']}"
                )

    return records


# ============================================================
# Yahoo 歷史資料
# ============================================================

def fetch_history(
    symbol: str,
) -> List[Dict[str, Any]]:

    url = YAHOO_URL.format(
        symbol=symbol
    )

    params = {
        "period1": date_to_timestamp(
            START_DATE
        ),
        "period2": (
            int(time.time())
            + 86400
        ),
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "false",
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            chart = payload.get(
                "chart",
                {},
            )

            error = chart.get(
                "error"
            )

            if error:

                if isinstance(
                    error,
                    dict,
                ):

                    description = (
                        error.get(
                            "description"
                        )
                        or "Yahoo API error"
                    )

                else:

                    description = str(
                        error
                    )

                raise RuntimeError(
                    description
                )

            results = chart.get(
                "result"
            )

            if not results:

                raise RuntimeError(
                    "Yahoo 沒有回傳 result"
                )

            result = results[0]

            timestamps = result.get(
                "timestamp"
            )

            indicators = result.get(
                "indicators",
                {},
            )

            quotes = indicators.get(
                "quote",
                [],
            )

            if not timestamps:

                raise RuntimeError(
                    "沒有 timestamp"
                )

            if not quotes:

                raise RuntimeError(
                    "沒有 quote"
                )

            quote = quotes[0]

            highs = quote.get(
                "high",
                [],
            )

            lows = quote.get(
                "low",
                [],
            )

            closes = quote.get(
                "close",
                [],
            )

            volumes = quote.get(
                "volume",
                [],
            )

            rows = []

            for index, timestamp in enumerate(
                timestamps
            ):

                if index >= len(
                    highs
                ):
                    continue

                if index >= len(
                    lows
                ):
                    continue

                if index >= len(
                    closes
                ):
                    continue

                high = safe_float(
                    highs[index]
                )

                low = safe_float(
                    lows[index]
                )

                close = safe_float(
                    closes[index]
                )

                volume = (
                    safe_int(
                        volumes[index]
                    )
                    if index < len(
                        volumes
                    )
                    else 0
                )

                if (
                    high is None
                    or low is None
                    or close is None
                ):

                    continue

                try:

                    date = (
                        datetime.fromtimestamp(
                            timestamp,
                            tz=timezone.utc,
                        ).strftime(
                            "%Y-%m-%d"
                        )
                    )

                except Exception:

                    continue

                rows.append(
                    {
                        "date": date,
                        "high": round(
                            high,
                            4,
                        ),
                        "low": round(
                            low,
                            4,
                        ),
                        "close": round(
                            close,
                            4,
                        ),
                        "volume": volume,
                    }
                )

            rows.sort(
                key=lambda row: row[
                    "date"
                ]
            )

            if len(rows) < MIN_HISTORY_ROWS:

                raise RuntimeError(
                    "歷史資料不足："
                    f"{len(rows)} rows"
                )

            return rows

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY
                    * attempt
                )

    raise RuntimeError(
        f"{symbol} 取得歷史資料失敗："
        f"{last_error}"
    )


# ============================================================
# 驗證歷史資料
# ============================================================

def validate_history(
    symbol: str,
    rows: Any,
) -> bool:

    if not isinstance(
        rows,
        list,
    ):

        raise RuntimeError(
            f"{symbol}: history 不是 list"
        )

    if len(rows) < MIN_HISTORY_ROWS:

        raise RuntimeError(
            f"{symbol}: history 不足"
        )

    previous_date = None

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):

            raise RuntimeError(
                f"{symbol}: history row 不是 object"
            )

        for key in (
            "date",
            "high",
            "low",
            "close",
            "volume",
        ):

            if key not in row:

                raise RuntimeError(
                    f"{symbol}: 缺少 {key}"
                )

        date = row["date"]

        high = safe_float(
            row["high"]
        )

        low = safe_float(
            row["low"]
        )

        close = safe_float(
            row["close"]
        )

        volume = safe_int(
            row["volume"]
        )

        if not isinstance(
            date,
            str,
        ):

            raise RuntimeError(
                f"{symbol}: date 格式錯誤"
            )

        if (
            high is None
            or low is None
            or close is None
        ):

            raise RuntimeError(
                f"{symbol}: 價格資料無效"
            )

        if high <= 0:
            raise RuntimeError(
                f"{symbol}: high <= 0"
            )

        if low <= 0:
            raise RuntimeError(
                f"{symbol}: low <= 0"
            )

        if close <= 0:
            raise RuntimeError(
                f"{symbol}: close <= 0"
            )

        if high < low:

            raise RuntimeError(
                f"{symbol}: high < low"
            )

        if volume < 0:

            raise RuntimeError(
                f"{symbol}: volume < 0"
            )

        if (
            previous_date is not None
            and date < previous_date
        ):

            raise RuntimeError(
                f"{symbol}: 日期未排序"
            )

        previous_date = date

    return True


# ============================================================
# 建立股票資料
# ============================================================

def build_stock_record(
    record: Dict[str, str],
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:

    return {
        "symbol": record["symbol"],
        "code": record["code"],
        "market": record["market"],
        "name": record.get(
            "name",
            "",
        ),
        "history": history,
    }


# ============================================================
# 建立分檔
# ============================================================

def build_price_files(
    temp_dir: Path,
    successful_records: Dict[
        str,
        Dict[str, Any],
    ],
) -> List[Dict[str, Any]]:

    section(
        "建立價格分檔"
    )

    items = list(
        successful_records.items()
    )

    items.sort(
        key=lambda pair: pair[0]
    )

    chunks = []

    for start in range(
        0,
        len(items),
        STOCKS_PER_FILE,
    ):

        chunks.append(
            items[
                start:
                start + STOCKS_PER_FILE
            ]
        )

    if not chunks:

        raise RuntimeError(
            "沒有成功股票可建立分檔"
        )

    manifest_files = []

    for index, chunk in enumerate(
        chunks,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            temp_dir
            / filename
        )

        stocks = {}

        for symbol, stock in chunk:

            stocks[symbol] = stock

        payload = {
            "version": VERSION,
            "schema_version": SCHEMA_VERSION,
            "file": filename,
            "stocks": len(stocks),
            "generated_at": (
                datetime.now(
                    timezone.utc
                )
                .isoformat()
                .replace(
                    "+00:00",
                    "Z",
                )
            ),
            "data": stocks,
        }

        save_json(
            path,
            payload,
        )

        size_bytes = path.stat().st_size

        size_mb = (
            size_bytes
            / 1024
            / 1024
        )

        if (
            size_bytes
            > MAX_FILE_SIZE_BYTES
        ):

            raise RuntimeError(
                f"{filename} 超過安全大小："
                f"{size_mb:.2f} MB"
            )

        manifest_files.append(
            {
                "file": filename,
                "stocks": len(stocks),
                "size_bytes": size_bytes,
                "size_mb": round(
                    size_mb,
                    2,
                ),
            }
        )

        log(
            f"✓ {filename} | "
            f"{len(stocks)} stocks | "
            f"{size_mb:.2f} MB"
        )

    return manifest_files


# ============================================================
# Manifest
# ============================================================

def build_manifest(
    universe_total: int,
    successful_records: Dict[
        str,
        Dict[str, Any],
    ],
    failed_records: Dict[str, str],
    manifest_files: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:

    success_count = len(
        successful_records
    )

    failed_count = len(
        failed_records
    )

    total = (
        success_count
        + failed_count
    )

    if total <= 0:

        raise RuntimeError(
            "Manifest total 不得為 0"
        )

    success_rate = (
        success_count / total
    )

    return {
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at": (
            datetime.now(
                timezone.utc
            )
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        ),
        "universe_total": universe_total,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": round(
            success_rate,
            6,
        ),
        "file_count": len(
            manifest_files
        ),
        "files": manifest_files,
    }


# ============================================================
# Manifest 驗證
# ============================================================

def validate_manifest(
    manifest: Dict[str, Any],
    successful_records: Dict[
        str,
        Dict[str, Any],
    ],
    failed_records: Dict[str, str],
) -> bool:

    section(
        "驗證 manifest.json"
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 必須是 object"
        )

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):

        raise RuntimeError(
            "manifest.files 必須是 list"
        )

    file_count = manifest.get(
        "file_count"
    )

    if file_count != len(files):

        raise RuntimeError(
            "manifest.file_count 不一致"
        )

    success_count = manifest.get(
        "success_count"
    )

    failed_count = manifest.get(
        "failed_count"
    )

    universe_total = manifest.get(
        "universe_total"
    )

    if success_count != len(
        successful_records
    ):

        raise RuntimeError(
            "manifest.success_count 不一致"
        )

    if failed_count != len(
        failed_records
    ):

        raise RuntimeError(
            "manifest.failed_count 不一致"
        )

    if universe_total != (
        success_count
        + failed_count
    ):

        raise RuntimeError(
            "manifest universe_total 不一致"
        )

    calculated_rate = (
        success_count
        / universe_total
        if universe_total
        else 0
    )

    manifest_rate = manifest.get(
        "success_rate"
    )

    if not isinstance(
        manifest_rate,
        (int, float),
    ):

        raise RuntimeError(
            "manifest.success_rate 格式錯誤"
        )

    if abs(
        float(manifest_rate)
        - calculated_rate
    ) > 0.00001:

        raise RuntimeError(
            "manifest.success_rate 不一致"
        )

    calculated_stocks = 0

    seen_files = set()

    for item in files:

        # ----------------------------------------------------
        # V4.2：
        # 每筆 manifest.files 必須是 dict
        # ----------------------------------------------------

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "manifest.files "
                "存在非 object 項目"
            )

        filename = item.get(
            "file"
        )

        stocks = item.get(
            "stocks"
        )

        size_bytes = item.get(
            "size_bytes"
        )

        size_mb = item.get(
            "size_mb"
        )

        if not isinstance(
            filename,
            str,
        ) or not filename:

            raise RuntimeError(
                "manifest file 欄位錯誤"
            )

        if filename in seen_files:

            raise RuntimeError(
                f"manifest file 重複："
                f"{filename}"
            )

        seen_files.add(
            filename
        )

        if not isinstance(
            stocks,
            int,
        ):

            raise RuntimeError(
                f"{filename}: stocks 欄位錯誤"
            )

        if stocks <= 0:

            raise RuntimeError(
                f"{filename}: stocks <= 0"
            )

        if not isinstance(
            size_bytes,
            int,
        ):

            raise RuntimeError(
                f"{filename}: size_bytes 欄位錯誤"
            )

        if size_bytes <= 0:

            raise RuntimeError(
                f"{filename}: size_bytes <= 0"
            )

        if not isinstance(
            size_mb,
            (int, float),
        ):

            raise RuntimeError(
                f"{filename}: size_mb 欄位錯誤"
            )

        if size_bytes > MAX_FILE_SIZE_BYTES:

            raise RuntimeError(
                f"{filename}: 超過安全大小"
            )

        calculated_stocks += stocks

    if calculated_stocks != success_count:

        raise RuntimeError(
            "Manifest 分檔股票數量與成功數不一致："
            f"{calculated_stocks} != "
            f"{success_count}"
        )

    if success_count <= 0:

        raise RuntimeError(
            "Manifest success_count <= 0"
        )

    if (
        calculated_rate
        < MIN_SUCCESS_RATE
    ):

        raise RuntimeError(
            "成功率低於安全門檻："
            f"{calculated_rate:.2%}"
        )

    log(
        f"✓ Manifest files："
        f"{file_count}"
    )

    log(
        f"✓ Manifest stocks："
        f"{calculated_stocks}"
    )

    log(
        f"✓ Success rate："
        f"{calculated_rate:.2%}"
    )

    log(
        "✓ Manifest validation passed"
    )

    return True


# ============================================================
# 價格分檔驗證
# ============================================================

def validate_price_files(
    temp_dir: Path,
    manifest: Dict[str, Any],
    successful_records: Dict[
        str,
        Dict[str, Any],
    ],
) -> bool:

    section(
        "驗證所有價格分檔"
    )

    files = manifest.get(
        "files"
    )

    expected_symbols = set(
        successful_records.keys()
    )

    actual_symbols = set()

    for item in files:

        if not isinstance(
            item,
            dict,
        ):

            raise RuntimeError(
                "分檔 manifest item "
                "不是 object"
            )

        filename = item.get(
            "file"
        )

        expected_stocks = item.get(
            "stocks"
        )

        path = (
            temp_dir
            / filename
        )

        if not path.exists():

            raise RuntimeError(
                f"找不到分檔："
                f"{filename}"
            )

        actual_size = path.stat().st_size

        if (
            actual_size
            > MAX_FILE_SIZE_BYTES
        ):

            raise RuntimeError(
                f"{filename} 超過安全大小"
            )

        payload = load_json(
            path
        )

        if not isinstance(
            payload,
            dict,
        ):

            raise RuntimeError(
                f"{filename}: "
                "頂層不是 object"
            )

        if payload.get(
            "file"
        ) != filename:

            raise RuntimeError(
                f"{filename}: file 欄位錯誤"
            )

        data = payload.get(
            "data"
        )

        if not isinstance(
            data,
            dict,
        ):

            raise RuntimeError(
                f"{filename}: data 不是 object"
            )

        if payload.get(
            "stocks"
        ) != len(data):

            raise RuntimeError(
                f"{filename}: stocks 數量不一致"
            )

        if len(data) != expected_stocks:

            raise RuntimeError(
                f"{filename}: "
                f"預期 {expected_stocks}，"
                f"實際 {len(data)}"
            )

        for symbol, stock in data.items():

            if symbol in actual_symbols:

                raise RuntimeError(
                    f"股票重複：{symbol}"
                )

            actual_symbols.add(
                symbol
            )

            if symbol not in expected_symbols:

                raise RuntimeError(
                    f"{filename}: "
                    f"出現非成功股票 {symbol}"
                )

            if not isinstance(
                stock,
                dict,
            ):

                raise RuntimeError(
                    f"{symbol}: "
                    "stock record 不是 object"
                )

            if stock.get(
                "symbol"
            ) != symbol:

                raise RuntimeError(
                    f"{symbol}: symbol 不一致"
                )

            history = stock.get(
                "history"
            )

            validate_history(
                symbol,
                history,
            )

        log(
            f"✓ {filename} validated | "
            f"{len(data)} stocks"
        )

    if actual_symbols != expected_symbols:

        missing = (
            expected_symbols
            - actual_symbols
        )

        extra = (
            actual_symbols
            - expected_symbols
        )

        raise RuntimeError(
            "分檔股票集合不一致；"
            f"missing={len(missing)}；"
            f"extra={len(extra)}"
        )

    log(
        f"✓ 全部分檔驗證通過："
        f"{len(actual_symbols)} stocks"
    )

    return True


# ============================================================
# Staging 完整驗證
# ============================================================

def validate_staging_area(
    temp_dir: Path,
    manifest: Dict[str, Any],
    successful_records: Dict[
        str,
        Dict[str, Any],
    ],
    failed_records: Dict[str, str],
) -> bool:

    section(
        "最終資料完整性驗證"
    )

    validate_manifest(
        manifest,
        successful_records,
        failed_records,
    )

    validate_price_files(
        temp_dir,
        manifest,
        successful_records,
    )

    expected_files = {
        item["file"]
        for item in manifest["files"]
    }

    actual_files = {
        path.name
        for path in temp_dir.glob(
            "prices_*.json"
        )
    }

    if actual_files != expected_files:

        missing = (
            expected_files
            - actual_files
        )

        extra = (
            actual_files
            - expected_files
        )

        raise RuntimeError(
            "暫存目錄分檔集合不一致；"
            f"missing={missing}; "
            f"extra={extra}"
        )

    manifest_path = (
        temp_dir
        / "manifest.json"
    )

    if not manifest_path.exists():

        raise RuntimeError(
            "manifest.json 不存在"
        )

    # --------------------------------------------------------
    # 重新讀取 manifest
    # --------------------------------------------------------

    reloaded_manifest = load_json(
        manifest_path
    )

    if reloaded_manifest != manifest:

        raise RuntimeError(
            "重新讀取 manifest 後內容不一致"
        )

    log(
        "✓ staging area 完整性驗證通過"
    )

    return True


# ============================================================
# 原子替換
# ============================================================

def replace_output_directory(
    staging_dir: Path,
) -> None:

    section(
        "替換正式 Data/prices/"
    )

    OUTPUT_DIR.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    backup_dir = None

    if OUTPUT_DIR.exists():

        backup_dir = (
            OUTPUT_DIR.parent
            / (
                ".prices_backup_"
                + str(
                    int(
                        time.time()
                    )
                )
            )
        )

        log(
            f"建立舊資料備份："
            f"{backup_dir}"
        )

        OUTPUT_DIR.rename(
            backup_dir
        )

    try:

        shutil.move(
            str(staging_dir),
            str(OUTPUT_DIR),
        )

    except Exception:

        if (
            not OUTPUT_DIR.exists()
            and backup_dir is not None
            and backup_dir.exists()
        ):

            backup_dir.rename(
                OUTPUT_DIR
            )

        raise

    if (
        backup_dir is not None
        and backup_dir.exists()
    ):

        try:

            shutil.rmtree(
                backup_dir
            )

        except Exception as exc:

            log(
                "⚠️ 舊資料備份刪除失敗："
                f"{exc}"
            )

    log(
        "✓ Data/prices/ 替換完成"
    )


# ============================================================
# 顯示失敗
# ============================================================

def print_failed_records(
    failed_records: Dict[str, str],
) -> None:

    if not failed_records:
        return

    log("")
    log(
        f"失敗股票："
        f"{len(failed_records)}"
    )

    for symbol, reason in list(
        failed_records.items()
    )[:30]:

        log(
            f"  ✗ {symbol}："
            f"{reason}"
        )

    if len(failed_records) > 30:

        log(
            f"  ... 其餘 "
            f"{len(failed_records) - 30} 檔"
        )


# ============================================================
# 主流程
# ============================================================

def main() -> int:

    section(
        "台股 AI 選股系統 "
        f"fetch_prices.py {VERSION}"
    )

    log(
        f"BASE_DIR：{BASE_DIR}"
    )

    log(
        f"DATA_DIR：{DATA_DIR}"
    )

    log(
        f"UNIVERSE：{UNIVERSE_FILE}"
    )

    log(
        f"OUTPUT：{OUTPUT_DIR}"
    )

    log(
        f"START_DATE：{START_DATE}"
    )

    log(
        f"STOCKS_PER_FILE："
        f"{STOCKS_PER_FILE}"
    )

    log(
        f"MIN_SUCCESS_RATE："
        f"{MIN_SUCCESS_RATE:.0%}"
    )

    log(
        f"MIN_HISTORY_ROWS："
        f"{MIN_HISTORY_ROWS}"
    )

    log(
        f"MAX_FILE_SIZE："
        f"{MAX_FILE_SIZE_MB:.1f} MB"
    )

    staging_dir = None

    try:

        DATA_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ====================================================
        # 1. Load Universe
        # ====================================================

        universe = load_universe()

        records = extract_symbols(
            universe
        )

        universe_total = len(
            records
        )

        if universe_total <= 0:

            raise RuntimeError(
                "Universe Stock total <= 0"
            )

        # ----------------------------------------------------
        # 最低股票數量防呆
        # ----------------------------------------------------

        if universe_total < 1000:

            raise RuntimeError(
                "Universe 股票數量異常過低："
                f"{universe_total}"
            )

        # ====================================================
        # 2. Staging
        # ====================================================

        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=".prices_staging_",
                dir=DATA_DIR,
            )
        )

        log("")
        log(
            f"暫存目錄："
            f"{staging_dir}"
        )

        # ====================================================
        # 3. Fetch Yahoo
        # ====================================================

        section(
            "開始取得 Yahoo Finance 歷史資料"
        )

        successful_records = {}

        failed_records = {}

        processed = 0

        for symbol, record in records.items():

            processed += 1

            try:

                history = fetch_history(
                    symbol
                )

                validate_history(
                    symbol,
                    history,
                )

                successful_records[
                    symbol
                ] = build_stock_record(
                    record,
                    history,
                )

                log(
                    f"[{processed}/{universe_total}] "
                    f"✓ {symbol} "
                    f"{len(history)} rows"
                )

            except Exception as exc:

                failed_records[
                    symbol
                ] = str(exc)

                log(
                    f"[{processed}/{universe_total}] "
                    f"✗ {symbol} "
                    f"{exc}"
                )

            time.sleep(
                REQUEST_DELAY
            )

        # ====================================================
        # 4. 統計
        # ====================================================

        section(
            "資料取得統計"
        )

        success_count = len(
            successful_records
        )

        failed_count = len(
            failed_records
        )

        total_count = (
            success_count
            + failed_count
        )

        success_rate = (
            success_count
            / total_count
            if total_count
            else 0
        )

        log(
            f"Universe Stock："
            f"{total_count}"
        )

        log(
            f"成功："
            f"{success_count}"
        )

        log(
            f"失敗："
            f"{failed_count}"
        )

        log(
            f"成功率："
            f"{success_rate:.2%}"
        )

        print_failed_records(
            failed_records
        )

        # ====================================================
        # 5. Success rate safety gate
        # ====================================================

        if (
            success_rate
            < MIN_SUCCESS_RATE
        ):

            raise RuntimeError(
                "成功率低於安全門檻："
                f"{success_rate:.2%} "
                f"< "
                f"{MIN_SUCCESS_RATE:.2%}"
            )

        if success_count <= 0:

            raise RuntimeError(
                "沒有任何成功股票"
            )

        # ====================================================
        # 6. 建立價格分檔
        # ====================================================

        manifest_files = build_price_files(
            staging_dir,
            successful_records,
        )

        # ====================================================
        # 7. Manifest
        # ====================================================

        manifest = build_manifest(
            universe_total,
            successful_records,
            failed_records,
            manifest_files,
        )

        manifest_path = (
            staging_dir
            / "manifest.json"
        )

        save_json(
            manifest_path,
            manifest,
        )

        # ====================================================
        # 8. Final staging validation
        # ====================================================

        validate_staging_area(
            staging_dir,
            manifest,
            successful_records,
            failed_records,
        )

        # ====================================================
        # 9. Atomic replace
        # ====================================================

        replace_output_directory(
            staging_dir
        )

        staging_dir = None

        # ====================================================
        # 10. Success
        # ====================================================

        section(
            "FETCH PRICES SUCCESS"
        )

        log(
            f"✓ Version："
            f"{VERSION}"
        )

        log(
            f"✓ Universe Stock："
            f"{universe_total}"
        )

        log(
            f"✓ 成功："
            f"{success_count}"
        )

        log(
            f"✓ 失敗："
            f"{failed_count}"
        )

        log(
            f"✓ 成功率："
            f"{success_rate:.2%}"
        )

        log(
            f"✓ 分檔："
            f"{len(manifest_files)}"
        )

        log(
            f"✓ Output："
            f"{OUTPUT_DIR}"
        )

        return 0

    except KeyboardInterrupt:

        log("")
        log(
            "⚠️ 使用者中止執行"
        )

        return 130

    except Exception as exc:

        section(
            "FETCH PRICES FAILED"
        )

        log(
            f"ERROR：{exc}"
        )

        # ----------------------------------------------------
        # 清除 staging
        # ----------------------------------------------------

        if (
            staging_dir is not None
            and staging_dir.exists()
        ):

            try:

                shutil.rmtree(
                    staging_dir
                )

                log(
                    "✓ 已清除暫存資料"
                )

            except Exception as cleanup_exc:

                log(
                    "⚠️ 暫存資料清除失敗："
                    f"{cleanup_exc}"
                )

        # ----------------------------------------------------
        # 正式資料完全不動
        # ----------------------------------------------------

        if OUTPUT_DIR.exists():

            log(
                "✓ 保留既有 "
                "Data/prices/"
            )

        else:

            log(
                "ℹ️ 目前沒有既有 "
                "Data/prices/"
            )

        return 1


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    sys.exit(
        main()
    )
