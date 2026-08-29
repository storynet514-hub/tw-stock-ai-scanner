#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股 AI 選股系統
Scripts/fetch_prices.py
正式修正版 V4.3

============================================================
資料責任
============================================================

1. 讀取 Data/universe.json
2. STOCK 與 ETF 完全分流
3. 只處理 STOCK
4. 依 Universe 的正式 market / full_symbol 判斷：
      TWSE → .TW
      TPEX → .TWO
5. 優先使用 Yahoo Finance
6. Yahoo 失敗或歷史資料不足：
      TPEX 股票 → 官方 TPEx 月資料 fallback
7. 不因單一股票失敗而靜默遺漏
8. 產生：
      Data/prices/
          manifest.json
          prices_001.json
          prices_002.json
          ...
9. 每 100 檔股票一個 shard
10. 所有資料先寫入 temporary directory
11. 完整驗證後才替換正式 Data/prices/
12. 不產生 Data/prices.json

============================================================
V4.3 核心修正
============================================================

V4.2 問題：

1. MIN_HISTORY_ROWS = 100 過於嚴格
2. 新掛牌股票可能正常存在，但尚未累積 100 個交易日
3. Yahoo 個別股票失敗後，TPEX fallback 不完整
4. TPEX fallback 只抓單月份，無法建立完整歷史
5. 7794.TWO 因此可能被錯誤判定為 failed

V4.3：

✓ minimum history 改為 60
✓ Yahoo 成功且 >= 60 筆 → 使用 Yahoo
✓ Yahoo 失敗 → TPEX fallback
✓ Yahoo 少於 60 筆 → TPEX fallback
✓ TPEX fallback 按月份抓取
✓ TPEX fallback 自動合併月份
✓ TPEX fallback 去重
✓ TPEX fallback 排序
✓ TPEX fallback 至少 60 筆才算完整
✓ 若新股歷史本來不足 60 筆：
    使用實際可取得資料，但必須 >= 20 筆
✓ 不會把「新股歷史短」誤判成「沒有資料」
✓ log 明確記錄每檔資料來源
✓ 7794.TWO 可直接走 TPEX 官方 fallback
✓ 不改 Universe
✓ 不抓 ETF
✓ 不使用 CMoney
✓ 不使用舊價格資料冒充最新資料

============================================================
安全機制
============================================================

✓ Universe 不可為空
✓ STOCK 不可為空
✓ 成功率低於 80% → FAIL
✓ 成功股票至少 20 筆歷史 → 才可寫入
✓ 一般股票 >= 60 筆 → 正常
✓ 新掛牌股票 20~59 筆 → 允許並標記 short_history
✓ 分檔驗證失敗 → FAIL
✓ Manifest 驗證失敗 → FAIL
✓ shard 超過 80MB → FAIL
✓ temporary directory
✓ atomic replace
✓ 未預期錯誤 exit 1
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

VERSION = "V4.3"

SCHEMA_VERSION = "prices-v4.3"

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = BASE_DIR / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

OUTPUT_DIR = DATA_DIR / "prices"

START_DATE = "2023-01-01"

YAHOO_URL = (
    "https://query1.finance.yahoo.com/"
    "v8/finance/chart/{symbol}"
)

TPEX_MONTH_URL = (
    "https://www.tpex.org.tw/web/stock/"
    "aftertrading/daily_trading_info/"
    "st43_result.php"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 3

REQUEST_DELAY = 0.08

RETRY_DELAY = 1.5

STOCKS_PER_FILE = 100

MIN_SUCCESS_RATE = 0.80

# ------------------------------------------------------------
# 技術分析真正需要：
# MA20 / KD / MACD / RSI / 60日高低
# analyze_stocks.py minimum_history = 60
# ------------------------------------------------------------
MIN_HISTORY_ROWS = 60

# ------------------------------------------------------------
# 絕對最低可用資料
#
# 新掛牌股票可能暫時不足 60 個交易日。
# 只要有 >= 20 個交易日，就保留資料，
# 後續分析層自行判定指標是否足夠。
# ------------------------------------------------------------
ABSOLUTE_MIN_HISTORY_ROWS = 20

# ------------------------------------------------------------
# TPEX fallback 最多往前抓幾個月
# 7794 這類新掛牌股票只需數個月。
# 12 個月足夠涵蓋一般新掛牌股票。
# ------------------------------------------------------------
TPEX_FALLBACK_MONTHS = 18

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
    log("=" * 72)
    log(title)
    log("=" * 72)


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

        number = float(
            str(value).replace(",", "").strip()
        )

        if not math.isfinite(number):
            return None

        return number

    except Exception:

        return None


def safe_int(
    value: Any,
) -> int:

    number = safe_float(value)

    if number is None:
        return 0

    return int(number)


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


def parse_tpex_date(
    value: Any,
) -> Optional[str]:

    text = str(value or "").strip()

    if not text:
        return None

    # 例如：
    # 115/04/20
    # 115/04/20
    parts = text.split("/")

    if len(parts) == 3:

        try:

            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])

            if year < 1911:
                year += 1911

            return (
                f"{year:04d}-"
                f"{month:02d}-"
                f"{day:02d}"
            )

        except Exception:
            pass

    # 已經是 ISO
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y%m%d",
    ):

        try:

            dt = datetime.strptime(
                text,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            pass

    return None


# ============================================================
# 文字
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
# Full Symbol
# ============================================================

def extract_full_symbol(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    for key in (
        "full_symbol",
        "fullSymbol",
        "yahoo_symbol",
        "yahooSymbol",
    ):

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if text.endswith(".TW"):

            code = extract_code(text)

            if code:
                return (
                    code + ".TW"
                )

        if text.endswith(".TWO"):

            code = extract_code(text)

            if code:
                return (
                    code + ".TWO"
                )

    return None


# ============================================================
# Market
# ============================================================

def detect_market(
    item: Any,
) -> Optional[str]:

    if not isinstance(
        item,
        dict,
    ):
        return None

    # --------------------------------------------------------
    # 先看完整 Yahoo symbol
    # --------------------------------------------------------

    full_symbol = extract_full_symbol(
        item
    )

    if full_symbol:

        if full_symbol.endswith(".TWO"):
            return "TWO"

        if full_symbol.endswith(".TW"):
            return "TW"

    # --------------------------------------------------------
    # 再看 Universe market
    # --------------------------------------------------------

    keys = (
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
    )

    for key in keys:

        value = item.get(key)

        if value is None:
            continue

        text = clean_text(
            value
        ).upper()

        if not text:
            continue

        if (
            text in {
                "TPEX",
                "TWO",
                "OTC",
                "O",
            }
            or "TPEX" in text
            or "OTC" in text
            or "上櫃" in text
            or "上柜" in text
            or "櫃買" in text
            or "柜买" in text
        ):
            return "TWO"

        if (
            text in {
                "TWSE",
                "TW",
                "TSE",
            }
            or "TWSE" in text
            or "上市" in text
        ):
            return "TW"

    return None


# ============================================================
# Type
# ============================================================

def detect_type(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):
        return "Stock"

    for key in (
        "type",
        "security_type",
        "securityType",
        "category",
        "instrument_type",
        "instrumentType",
        "類型",
        "商品類型",
        "證券類型",
    ):

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
# Name
# ============================================================

def extract_name(
    item: Any,
) -> str:

    if not isinstance(
        item,
        dict,
    ):
        return ""

    for key in (
        "name",
        "stock_name",
        "company_name",
        "security_name",
        "名稱",
        "證券名稱",
        "公司名稱",
    ):

        value = clean_text(
            item.get(key)
        )

        if value:
            return value

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
# Universe Normalize
# ============================================================

def normalize_item(
    item: Any,
    forced_type: Optional[str] = None,
) -> Optional[Dict[str, str]]:

    # --------------------------------------------------------
    # string
    # --------------------------------------------------------

    if isinstance(
        item,
        str,
    ):

        text = clean_text(
            item
        ).upper()

        code = extract_code(text)

        if not code:
            return None

        market = (
            "TWO"
            if text.endswith(".TWO")
            else "TW"
        )

        return {
            "symbol": build_yahoo_symbol(
                code,
                market,
                text,
            ),
            "code": code,
            "market": market,
            "name": "",
            "type": forced_type or "Stock",
        }

    # --------------------------------------------------------
    # dict
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

    for key in (
        "symbol",
        "code",
        "stock_id",
        "stock_code",
        "ticker",
        "證券代號",
        "有價證券代號",
        "代號",
    ):

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

    if market is None:

        if full_symbol:

            if full_symbol.endswith(".TWO"):
                market = "TWO"

            elif full_symbol.endswith(".TW"):
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
# Universe Container
# ============================================================

def extract_container(
    universe: Dict[str, Any],
    key: str,
) -> List[Any]:

    value = universe.get(key)

    if isinstance(
        value,
        list,
    ):
        return value

    if isinstance(
        value,
        dict,
    ):
        result = []

        for symbol, item in value.items():

            if isinstance(
                item,
                dict,
            ):

                record = dict(item)

                if not (
                    record.get("symbol")
                    or record.get("code")
                    or record.get("stock_code")
                ):
                    record["symbol"] = symbol

                result.append(record)

            else:

                result.append(
                    {
                        "symbol": symbol,
                        "value": item,
                    }
                )

        return result

    return []


# ============================================================
# Load Universe
# ============================================================

def load_universe() -> List[Dict[str, str]]:

    section(
        "讀取 Data/universe.json"
    )

    if not UNIVERSE_FILE.exists():

        raise RuntimeError(
            "找不到 Data/universe.json"
        )

    universe = load_json(
        UNIVERSE_FILE
    )

    if not isinstance(
        universe,
        dict,
    ):

        raise RuntimeError(
            "universe.json 根節點必須是 object"
        )

    stocks_raw = extract_container(
        universe,
        "stocks",
    )

    if not stocks_raw:

        # 舊 schema
        stocks_raw = extract_container(
            universe,
            "items",
        )

    etfs_raw = extract_container(
        universe,
        "etfs",
    )

    if not stocks_raw:

        raise RuntimeError(
            "Universe 找不到 stocks/items"
        )

    stocks: Dict[
        str,
        Dict[str, str]
    ] = {}

    skipped = 0

    for item in stocks_raw:

        normalized = normalize_item(
            item,
            forced_type="Stock",
        )

        if normalized is None:

            skipped += 1
            continue

        if normalized["type"] != "Stock":

            continue

        symbol = normalized["symbol"]

        if symbol in stocks:
            continue

        stocks[symbol] = normalized

    if not stocks:

        raise RuntimeError(
            "Universe STOCK 為空"
        )

    # --------------------------------------------------------
    # Universe metadata
    # --------------------------------------------------------

    declared_stock_count = universe.get(
        "stock_count"
    )

    if (
        declared_stock_count is not None
        and isinstance(
            declared_stock_count,
            int,
        )
    ):

        log(
            f"Universe metadata stock_count："
            f"{declared_stock_count}"
        )

        log(
            f"實際 STOCK："
            f"{len(stocks)}"
        )

    log(
        f"Universe STOCK：{len(stocks)} 檔"
    )

    log(
        f"Universe ETF：{len(etfs_raw)} 檔"
    )

    if skipped:

        log(
            f"⚠️ 無法解析 STOCK："
            f"{skipped} 檔"
        )

    # --------------------------------------------------------
    # 明確驗證 7794
    # --------------------------------------------------------

    target = stocks.get(
        "7794.TWO"
    )

    if target:

        log("")
        log(
            "✓ 7794 Universe record："
            f"code={target['code']} "
            f"market={target['market']} "
            f"symbol={target['symbol']} "
            f"type={target['type']}"
        )

    return list(
        stocks.values()
    )


# ============================================================
# Yahoo Response Parser
# ============================================================

def parse_yahoo_payload(
    payload: Dict[str, Any],
) -> List[Dict[str, Any]]:

    chart = payload.get(
        "chart",
        {}
    )

    if not isinstance(
        chart,
        dict,
    ):
        return []

    result = chart.get(
        "result"
    )

    if not isinstance(
        result,
        list,
    ) or not result:

        return []

    first = result[0]

    if not isinstance(
        first,
        dict,
    ):
        return []

    timestamps = first.get(
        "timestamp"
    )

    indicators = first.get(
        "indicators",
        {}
    )

    quote_list = indicators.get(
        "quote",
        []
    )

    if not timestamps:
        return []

    if not quote_list:
        return []

    quote = quote_list[0]

    if not isinstance(
        quote,
        dict,
    ):
        return []

    opens = quote.get(
        "open",
        []
    )

    highs = quote.get(
        "high",
        []
    )

    lows = quote.get(
        "low",
        []
    )

    closes = quote.get(
        "close",
        []
    )

    volumes = quote.get(
        "volume",
        []
    )

    rows: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for index, timestamp in enumerate(
        timestamps
    ):

        try:

            dt = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc,
            )

            date_value = (
                dt.strftime(
                    "%Y-%m-%d"
                )
            )

        except Exception:

            continue

        close = (
            safe_float(
                closes[index]
            )
            if index < len(closes)
            else None
        )

        high = (
            safe_float(
                highs[index]
            )
            if index < len(highs)
            else None
        )

        low = (
            safe_float(
                lows[index]
            )
            if index < len(lows)
            else None
        )

        open_value = (
            safe_float(
                opens[index]
            )
            if index < len(opens)
            else None
        )

        volume = (
            safe_int(
                volumes[index]
            )
            if index < len(volumes)
            else 0
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        rows[date_value] = {
            "date": date_value,
            "open": (
                open_value
                if open_value is not None
                else close
            ),
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        rows.values(),
        key=lambda row: row["date"],
    )


# ============================================================
# Yahoo Fetch
# ============================================================

def fetch_yahoo(
    yahoo_symbol: str,
) -> Tuple[
    List[Dict[str, Any]],
    str,
]:

    start_ts = date_to_timestamp(
        START_DATE
    )

    end_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    params = {
        "period1": start_ts,
        "period2": end_ts,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }

    last_error = ""

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                YAHOO_URL.format(
                    symbol=yahoo_symbol
                ),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            rows = parse_yahoo_payload(
                payload
            )

            if rows:

                if len(rows) >= MIN_HISTORY_ROWS:

                    return (
                        rows,
                        "Yahoo Finance",
                    )

                if len(rows) >= ABSOLUTE_MIN_HISTORY_ROWS:

                    # ------------------------------------------------
                    # 重要：
                    # 新掛牌股票不因 <60 被直接丟棄。
                    # 但呼叫端仍會嘗試官方 fallback。
                    # ------------------------------------------------

                    return (
                        rows,
                        "Yahoo Finance (short history)",
                    )

            last_error = (
                f"資料不足：{len(rows)} 筆"
            )

        except Exception as exc:

            last_error = str(exc)

        if attempt < MAX_RETRIES:

            time.sleep(
                RETRY_DELAY * attempt
            )

    return (
        [],
        f"Yahoo failed: {last_error}",
    )


# ============================================================
# TPEX 月份產生器
# ============================================================

def month_sequence(
    months: int,
) -> List[Tuple[int, int]]:

    now = datetime.now(
        timezone.utc
    )

    year = now.year

    month = now.month

    result = []

    for _ in range(months):

        result.append(
            (
                year,
                month,
            )
        )

        month -= 1

        if month == 0:

            month = 12
            year -= 1

    return list(
        reversed(result)
    )


# ============================================================
# TPEX 單月
# ============================================================

def fetch_tpex_month(
    stock_code: str,
    year: int,
    month: int,
) -> List[Dict[str, Any]]:

    roc_year = year - 1911

    date_value = (
        f"{roc_year:03d}/"
        f"{month:02d}"
    )

    params = {
        "l": "zh-tw",
        "d": date_value,
        "stkno": stock_code,
    }

    last_error = ""

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                TPEX_MONTH_URL,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            rows = payload.get(
                "aaData",
                []
            )

            if not isinstance(
                rows,
                list,
            ):
                return []

            result = []

            for row in rows:

                if not isinstance(
                    row,
                    list,
                ):
                    continue

                if len(row) < 7:
                    continue

                date_parsed = parse_tpex_date(
                    row[0]
                )

                if not date_parsed:
                    continue

                # TPEX st43：
                #
                # 0 日期
                # 1 成交股數
                # 2 成交金額
                # 3 開盤
                # 4 最高
                # 5 最低
                # 6 收盤
                # 7 漲跌
                # 8 成交筆數

                open_value = safe_float(
                    row[3]
                )

                high = safe_float(
                    row[4]
                )

                low = safe_float(
                    row[5]
                )

                close = safe_float(
                    row[6]
                )

                volume = safe_int(
                    row[1]
                )

                if (
                    high is None
                    or low is None
                    or close is None
                ):
                    continue

                if close <= 0:
                    continue

                if open_value is None:
                    open_value = close

                result.append(
                    {
                        "date": date_parsed,
                        "open": open_value,
                        "high": high,
                        "low": low,
                        "close": close,
                        "volume": volume,
                    }
                )

            return result

        except Exception as exc:

            last_error = str(exc)

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

    if last_error:

        log(
            f"      ⚠️ TPEx "
            f"{stock_code} "
            f"{year}-{month:02d}："
            f"{last_error}"
        )

    return []


# ============================================================
# TPEX 完整歷史 fallback
# ============================================================

def fetch_tpex_history(
    stock_code: str,
) -> List[Dict[str, Any]]:

    section(
        f"TPEx 官方 fallback：{stock_code}.TWO"
    )

    all_rows: Dict[
        str,
        Dict[str, Any]
    ] = {}

    months = month_sequence(
        TPEX_FALLBACK_MONTHS
    )

    for year, month in months:

        rows = fetch_tpex_month(
            stock_code,
            year,
            month,
        )

        for row in rows:

            all_rows[
                row["date"]
            ] = row

        # ----------------------------------------------------
        # 如果已經拿到 >=60 筆，
        # 可以停止往更舊月份抓。
        # ----------------------------------------------------

        if len(all_rows) >= MIN_HISTORY_ROWS:

            break

        time.sleep(
            REQUEST_DELAY
        )

    result = sorted(
        all_rows.values(),
        key=lambda row: row["date"],
    )

    if len(result) >= MIN_HISTORY_ROWS:

        log(
            f"✓ TPEx fallback 成功："
            f"{stock_code}.TWO "
            f"{len(result)} 筆"
        )

        return result

    if len(result) >= ABSOLUTE_MIN_HISTORY_ROWS:

        log(
            f"⚠️ TPEx fallback："
            f"{stock_code}.TWO "
            f"只有 {len(result)} 筆"
            f"（新股/短歷史）"
        )

        return result

    log(
        f"❌ TPEx fallback 失敗："
        f"{stock_code}.TWO "
        f"只有 {len(result)} 筆"
    )

    return []


# ============================================================
# 取得股票歷史資料
# ============================================================

def fetch_stock_history(
    item: Dict[str, str],
) -> Tuple[
    List[Dict[str, Any]],
    str,
    str,
]:

    symbol = item["symbol"]

    code = item["code"]

    market = item["market"]

    # --------------------------------------------------------
    # 1. Yahoo
    # --------------------------------------------------------

    yahoo_rows, yahoo_source = fetch_yahoo(
        symbol
    )

    # --------------------------------------------------------
    # Yahoo >= 60
    # --------------------------------------------------------

    if (
        len(yahoo_rows)
        >= MIN_HISTORY_ROWS
    ):

        return (
            yahoo_rows,
            "Yahoo Finance",
            "",
        )

    # --------------------------------------------------------
    # Yahoo 20~59
    #
    # 不直接失敗。
    # 如果是 TPEX，先嘗試官方完整資料。
    # --------------------------------------------------------

    if (
        market == "TWO"
        and len(yahoo_rows)
        >= ABSOLUTE_MIN_HISTORY_ROWS
    ):

        log(
            f"⚠️ {symbol} Yahoo "
            f"只有 {len(yahoo_rows)} 筆，"
            f"啟動 TPEx 官方補資料"
        )

        tpex_rows = fetch_tpex_history(
            code
        )

        if len(tpex_rows) >= len(
            yahoo_rows
        ):

            if len(tpex_rows) >= (
                MIN_HISTORY_ROWS
            ):

                return (
                    tpex_rows,
                    "TPEx official",
                    "Yahoo history insufficient",
                )

            return (
                tpex_rows,
                "TPEx official (short history)",
                "Yahoo history insufficient",
            )

        # ----------------------------------------------------
        # TPEX 反而少於 Yahoo：
        # 保留 Yahoo，但記錄原因。
        # ----------------------------------------------------

        return (
            yahoo_rows,
            "Yahoo Finance (short history)",
            "TPEx fallback shorter",
        )

    # --------------------------------------------------------
    # Yahoo 完全失敗
    # TPEX → 官方 fallback
    # --------------------------------------------------------

    if market == "TWO":

        log(
            f"⚠️ {symbol} Yahoo 失敗，"
            f"啟動 TPEx 官方 fallback"
        )

        tpex_rows = fetch_tpex_history(
            code
        )

        if len(tpex_rows) >= (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            source = (
                "TPEx official"
                if len(tpex_rows)
                >= MIN_HISTORY_ROWS
                else
                "TPEx official (short history)"
            )

            return (
                tpex_rows,
                source,
                yahoo_source,
            )

    # --------------------------------------------------------
    # TWSE fallback
    #
    # 本版本不在 fetch_prices 裡假設
    # TWSE 月 API schema，
    # 避免用錯誤 fallback 產生假資料。
    # --------------------------------------------------------

    return (
        [],
        "",
        yahoo_source or "Yahoo failed",
    )


# ============================================================
# 正規化 price record
# ============================================================

def normalize_price_rows(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    normalized: Dict[
        str,
        Dict[str, Any]
    ] = {}

    for row in rows:

        if not isinstance(
            row,
            dict,
        ):
            continue

        date_value = clean_text(
            row.get("date")
        )

        if not date_value:
            continue

        close = safe_float(
            row.get("close")
        )

        high = safe_float(
            row.get("high")
        )

        low = safe_float(
            row.get("low")
        )

        open_value = safe_float(
            row.get("open")
        )

        volume = safe_int(
            row.get("volume")
        )

        if (
            close is None
            or high is None
            or low is None
        ):
            continue

        if close <= 0:
            continue

        if open_value is None:
            open_value = close

        normalized[
            date_value
        ] = {
            "date": date_value,
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }

    return sorted(
        normalized.values(),
        key=lambda row: row["date"],
    )


# ============================================================
# 單檔 fetch
# ============================================================

def fetch_one(
    item: Dict[str, str],
) -> Tuple[
    Optional[Dict[str, Any]],
    str,
]:

    symbol = item["symbol"]

    name = item["name"]

    rows, source, reason = (
        fetch_stock_history(item)
    )

    rows = normalize_price_rows(
        rows
    )

    if len(rows) < ABSOLUTE_MIN_HISTORY_ROWS:

        log(
            f"❌ {symbol} "
            f"{name} "
            f"→ 歷史資料不足："
            f"{len(rows)} 筆"
        )

        return (
            None,
            reason
            or "insufficient_history",
        )

    status = (
        "complete"
        if len(rows) >= MIN_HISTORY_ROWS
        else "short_history"
    )

    result = {
        "symbol": symbol,
        "code": item["code"],
        "market": item["market"],
        "name": name,
        "source": source,
        "history_rows": len(rows),
        "history_status": status,
        "latest_date": rows[-1]["date"],
        "prices": rows,
    }

    if reason:
        result["fallback_reason"] = reason

    log(
        f"✓ {symbol} "
        f"{name} "
        f"→ {len(rows)} 筆 "
        f"→ {source}"
    )

    return (
        result,
        "",
    )


# ============================================================
# Shard 建立
# ============================================================

def build_shards(
    results: Dict[
        str,
        Dict[str, Any]
    ],
) -> List[
    Dict[str, Any]
]:

    symbols = sorted(
        results.keys()
    )

    shards = []

    for start in range(
        0,
        len(symbols),
        STOCKS_PER_FILE,
    ):

        chunk = symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        stocks = {}

        for symbol in chunk:

            record = results[
                symbol
            ]

            stocks[symbol] = (
                record["prices"]
            )

        shards.append(
            {
                "stocks": stocks
            }
        )

    return shards


# ============================================================
# Shard 驗證
# ============================================================

def validate_shard(
    path: Path,
    expected_symbols: List[str],
) -> None:

    if not path.exists():

        raise RuntimeError(
            f"找不到 shard：{path}"
        )

    if path.stat().st_size > (
        MAX_FILE_SIZE_BYTES
    ):

        raise RuntimeError(
            f"shard 超過 "
            f"{MAX_FILE_SIZE_MB} MB："
            f"{path.name}"
        )

    data = load_json(
        path
    )

    if not isinstance(
        data,
        dict,
    ):

        raise RuntimeError(
            f"shard 根節點錯誤："
            f"{path.name}"
        )

    stocks = data.get(
        "stocks"
    )

    if not isinstance(
        stocks,
        dict,
    ):

        raise RuntimeError(
            f"{path.name} 缺少 stocks object"
        )

    actual = set(
        stocks.keys()
    )

    expected = set(
        expected_symbols
    )

    if actual != expected:

        missing = sorted(
            expected - actual
        )

        extra = sorted(
            actual - expected
        )

        raise RuntimeError(
            f"{path.name} 股票不一致；"
            f"missing={missing[:20]} "
            f"extra={extra[:20]}"
        )

    for symbol, rows in stocks.items():

        if not isinstance(
            rows,
            list,
        ):

            raise RuntimeError(
                f"{path.name} "
                f"{symbol} prices 必須是 list"
            )

        if len(rows) < (
            ABSOLUTE_MIN_HISTORY_ROWS
        ):

            raise RuntimeError(
                f"{path.name} "
                f"{symbol} "
                f"歷史資料不足："
                f"{len(rows)}"
            )

        previous_date = ""

        for row in rows:

            if not isinstance(
                row,
                dict,
            ):

                raise RuntimeError(
                    f"{path.name} "
                    f"{symbol} 存在非 object price row"
                )

            required = {
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            }

            missing = (
                required
                - set(row.keys())
            )

            if missing:

                raise RuntimeError(
                    f"{path.name} "
                    f"{symbol} "
                    f"缺少欄位："
                    f"{sorted(missing)}"
                )

            date_value = str(
                row["date"]
            )

            if (
                previous_date
                and date_value
                < previous_date
            ):

                raise RuntimeError(
                    f"{path.name} "
                    f"{symbol} 日期未排序"
                )

            previous_date = date_value


# ============================================================
# Manifest
# ============================================================

def build_manifest(
    shard_files: List[str],
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> Dict[str, Any]:

    complete = 0

    short_history = 0

    source_counts: Dict[
        str,
        int
    ] = {}

    latest_dates = []

    for result in results.values():

        status = result.get(
            "history_status"
        )

        if status == "complete":
            complete += 1

        elif status == "short_history":
            short_history += 1

        source = result.get(
            "source",
            "",
        )

        source_counts[source] = (
            source_counts.get(
                source,
                0,
            ) + 1
        )

        latest_date = result.get(
            "latest_date"
        )

        if latest_date:
            latest_dates.append(
                latest_date
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "generator_version": VERSION,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_stock_count": universe_count,
        "price_stock_count": len(results),
        "complete_history_count": complete,
        "short_history_count": short_history,
        "failed_count": (
            universe_count
            - len(results)
        ),
        "min_history_rows": MIN_HISTORY_ROWS,
        "absolute_min_history_rows": (
            ABSOLUTE_MIN_HISTORY_ROWS
        ),
        "sources": source_counts,
        "latest_date": (
            max(latest_dates)
            if latest_dates
            else None
        ),
        "files": shard_files,
    }


# ============================================================
# Manifest 驗證
# ============================================================

def validate_manifest(
    manifest_path: Path,
    expected_symbols: List[str],
    expected_shards: List[str],
) -> None:

    manifest = load_json(
        manifest_path
    )

    if not isinstance(
        manifest,
        dict,
    ):

        raise RuntimeError(
            "manifest 根節點必須是 object"
        )

    files = manifest.get(
        "files"
    )

    if not isinstance(
        files,
        list,
    ):

        raise RuntimeError(
            "manifest.files 必須是 array"
        )

    files = [
        str(value)
        for value in files
    ]

    if files != expected_shards:

        raise RuntimeError(
            "manifest.files 與實際 shard 不一致"
        )

    if manifest.get(
        "universe_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest universe_stock_count 錯誤"
        )

    if manifest.get(
        "price_stock_count"
    ) != len(expected_symbols):

        raise RuntimeError(
            "manifest price_stock_count 錯誤："
            f"{manifest.get('price_stock_count')} "
            f"!= {len(expected_symbols)}"
        )


# ============================================================
# 寫入 temporary price directory
# ============================================================

def write_price_directory(
    temp_dir: Path,
    results: Dict[
        str,
        Dict[str, Any]
    ],
    universe_count: int,
) -> None:

    temp_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    shards = build_shards(
        results
    )

    shard_files = []

    all_symbols = sorted(
        results.keys()
    )

    for index, shard in enumerate(
        shards,
        start=1,
    ):

        filename = (
            f"prices_{index:03d}.json"
        )

        path = (
            temp_dir
            / filename
        )

        save_json(
            path,
            shard,
        )

        start = (
            (index - 1)
            * STOCKS_PER_FILE
        )

        expected = all_symbols[
            start:
            start + STOCKS_PER_FILE
        ]

        validate_shard(
            path,
            expected,
        )

        shard_files.append(
            filename
        )

    manifest = build_manifest(
        shard_files,
        results,
        universe_count,
    )

    manifest_path = (
        temp_dir
        / "manifest.json"
    )

    save_json(
        manifest_path,
        manifest,
    )

    validate_manifest(
        manifest_path,
        all_symbols,
        shard_files,
    )

    log(
        f"✓ shard 驗證完成："
        f"{len(shard_files)} 個"
    )

    log(
        f"✓ manifest 驗證完成"
    )


# ============================================================
# Atomic Replace
# ============================================================

def replace_output(
    temp_dir: Path,
) -> None:

    backup_dir = (
        DATA_DIR
        / ".prices_backup"
    )

    old_dir = OUTPUT_DIR

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )

    if old_dir.exists():

        old_dir.rename(
            backup_dir
        )

    try:

        temp_dir.rename(
            old_dir
        )

    except Exception:

        if old_dir.exists():

            shutil.rmtree(
                old_dir
            )

        if backup_dir.exists():

            backup_dir.rename(
                old_dir
            )

        raise

    if backup_dir.exists():

        shutil.rmtree(
            backup_dir
        )


# ============================================================
# Main
# ============================================================

def main() -> int:

    start_time = time.time()

    section(
        f"fetch_prices.py {VERSION}"
    )

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    universe = load_universe()

    universe_count = len(
        universe
    )

    expected_symbols = {
        item["symbol"]
        for item in universe
    }

    # --------------------------------------------------------
    # Fetch
    # --------------------------------------------------------

    section(
        f"開始抓取 STOCK："
        f"{universe_count} 檔"
    )

    results: Dict[
        str,
        Dict[str, Any]
    ] = {}

    failures: Dict[
        str,
        str
    ] = {}

    source_counts: Dict[
        str,
        int
    ] = {}

    for index, item in enumerate(
        universe,
        start=1,
    ):

        symbol = item[
            "symbol"
        ]

        log(
            f"[{index}/{universe_count}] "
            f"{symbol} "
            f"{item['name']}"
        )

        try:

            result, reason = fetch_one(
                item
            )

            if result is None:

                failures[
                    symbol
                ] = reason

            else:

                results[
                    symbol
                ] = result

                source = result.get(
                    "source",
                    "",
                )

                source_counts[
                    source
                ] = (
                    source_counts.get(
                        source,
                        0,
                    )
                    + 1
                )

        except Exception as exc:

            failures[
                symbol
            ] = str(exc)

            log(
                f"❌ {symbol} "
                f"未預期錯誤："
                f"{exc}"
            )

        time.sleep(
            REQUEST_DELAY
        )

    # --------------------------------------------------------
    # Result summary
    # --------------------------------------------------------

    success_count = len(
        results
    )

    failed_count = len(
        failures
    )

    success_rate = (
        success_count
        / universe_count
        if universe_count
        else 0.0
    )

    section(
        "價格資料結果"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
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

    for source, count in sorted(
        source_counts.items()
    ):

        log(
            f"來源 {source}："
            f"{count}"
        )

    # --------------------------------------------------------
    # 硬性 Gate
    # --------------------------------------------------------

    if success_rate < (
        MIN_SUCCESS_RATE
    ):

        log(
            "❌ 成功率低於安全門檻："
            f"{MIN_SUCCESS_RATE:.0%}"
        )

        for symbol, reason in list(
            failures.items()
        )[:30]:

            log(
                f"  {symbol}: {reason}"
            )

        return 1

    # --------------------------------------------------------
    # 嚴格檢查 Universe 是否全部有資料
    #
    # 目前專案目標不是接受 1943/1944，
    # 而是盡可能達成 1944/1944。
    #
    # 因此：
    # 若只有少數失敗，不立即破壞舊資料，
    # 但會明確記錄。
    # --------------------------------------------------------

    missing_symbols = (
        expected_symbols
        - set(results.keys())
    )

    if missing_symbols:

        log("")
        log(
            "⚠️ 尚有缺少價格資料："
            f"{len(missing_symbols)} 檔"
        )

        for symbol in sorted(
            missing_symbols
        )[:50]:

            log(
                f"  {symbol}: "
                f"{failures.get(symbol, '')}"
            )

    # --------------------------------------------------------
    # Temporary output
    # --------------------------------------------------------

    temp_root = Path(
        tempfile.mkdtemp(
            prefix="prices_build_",
            dir=str(DATA_DIR),
        )
    )

    temp_dir = (
        temp_root
        / "prices"
    )

    try:

        section(
            "建立 temporary Data/prices"
        )

        write_price_directory(
            temp_dir,
            results,
            universe_count,
        )

        # ----------------------------------------------------
        # 再次確認 7794
        # ----------------------------------------------------

        if "7794.TWO" in expected_symbols:

            if "7794.TWO" in results:

                result = results[
                    "7794.TWO"
                ]

                log("")
                log(
                    "================================================"
                )
                log(
                    "✓ 7794.TWO 最終驗證"
                )
                log(
                    f"資料筆數："
                    f"{result['history_rows']}"
                )
                log(
                    f"資料來源："
                    f"{result['source']}"
                )
                log(
                    f"最新日期："
                    f"{result['latest_date']}"
                )
                log(
                    f"狀態："
                    f"{result['history_status']}"
                )
                log(
                    "================================================"
                )

            else:

                log("")
                log(
                    "❌ 7794.TWO 仍未取得價格資料"
                )

        # ----------------------------------------------------
        # Atomic replace
        # ----------------------------------------------------

        section(
            "替換正式 Data/prices"
        )

        replace_output(
            temp_dir
        )

        log(
            "✓ Data/prices/ 已成功更新"
        )

    except Exception as exc:

        log("")
        log(
            "❌ 價格資料建置失敗："
            f"{exc}"
        )

        # ----------------------------------------------------
        # 保留舊正式資料
        # ----------------------------------------------------

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

        return 1

    finally:

        if temp_root.exists():

            shutil.rmtree(
                temp_root,
                ignore_errors=True,
            )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    elapsed = (
        time.time()
        - start_time
    )

    section(
        "FINAL PRICE RESULT"
    )

    log(
        f"Universe STOCK："
        f"{universe_count}"
    )

    log(
        f"Price 成功："
        f"{success_count}"
    )

    log(
        f"Price 失敗："
        f"{failed_count}"
    )

    log(
        f"成功率："
        f"{success_rate:.2%}"
    )

    if (
        "7794.TWO"
        in expected_symbols
    ):

        if (
            "7794.TWO"
            in results
        ):

            log(
                "✓ 7794.TWO："
                "已成功進入價格資料鏈"
            )

        else:

            log(
                "❌ 7794.TWO："
                "仍缺少價格資料"
            )

    log(
        f"執行時間："
        f"{elapsed:.1f} 秒"
    )

    log(
        "✓ fetch_prices.py 完成"
    )

    return 0


if __name__ == "__main__":

    sys.exit(
        main()
    )