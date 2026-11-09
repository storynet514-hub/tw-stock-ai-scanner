
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
台股全市場週20MA掃描器
Scripts/scan_weekly_ma20.py

功能：
1. 讀取 Data/universe.json 的有效上市、上櫃普通股。
2. 讀取 Data/prices/manifest.json 所列的歷史行情分片。
3. 使用原始日收盤價彙整週K。
4. 計算最近20根已完成週K的收盤均價。
5. 篩選最新收盤價站上週20MA的股票。
6. 輸出 Data/weekly_ma20_scan.json。

計算規則：
- 週K以 ISO 星期一至星期五為一週。
- 週收盤價採該週最後一個有效交易日收盤價。
- 週一至週四：排除當週尚未完成的週K。
- 週五：納入當週已完成週K。
- 週20MA = 最近20根已完成週K收盤價的算術平均。
- 篩選條件：最新收盤價 > 週20MA。

注意：
本程式不會自行下載行情。
必須先由 fetch_prices.py 完成價格資料更新。
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile

from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# 路徑設定
# ============================================================

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "Data"

UNIVERSE_FILE = DATA_DIR / "universe.json"

PRICE_DIR = DATA_DIR / "prices"

MANIFEST_FILE = PRICE_DIR / "manifest.json"

OUTPUT_FILE = DATA_DIR / "weekly_ma20_scan.json"


# ============================================================
# 版本設定
# ============================================================

PRICE_SCHEMA = "prices-v14.0"

OUTPUT_SCHEMA = "weekly-ma20-scan-v1.0"

MARKETS = {"TWSE", "TPEX"}

REQUIRED_WEEKLY_BARS = 20


# ============================================================
# JSON 讀取
# ============================================================

def load_json(path: Path) -> Any:

    if not path.exists():

        raise FileNotFoundError(
            f"找不到必要資料檔案：{path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig"
    ) as file:

        return json.load(file)


# ============================================================
# 日期轉換
# ============================================================

def parse_date(value: Any) -> date | None:

    if value is None:

        return None

    try:

        return date.fromisoformat(
            str(value)[:10]
        )

    except (ValueError, TypeError):

        return None


# ============================================================
# 數值轉換
# ============================================================

def parse_number(value: Any) -> float | None:

    if value is None:

        return None

    try:

        number = float(value)

        if not math.isfinite(number):

            return None

        if number <= 0:

            return None

        return number

    except (ValueError, TypeError):

        return None


# ============================================================
# 原子寫入
# ============================================================

def atomic_write_json(
    path: Path,
    payload: dict[str, Any]
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    fd, temp_path = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent)
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2
            )

            file.write("\n")

            file.flush()

            os.fsync(file.fileno())

        os.replace(
            temp_path,
            path
        )

    finally:

        if os.path.exists(temp_path):

            os.unlink(temp_path)


# ============================================================
# Universe 驗證
# ============================================================

def load_active_stocks():

    payload = load_json(
        UNIVERSE_FILE
    )

    stocks = payload.get("stocks")

    if not isinstance(stocks, dict):

        raise RuntimeError(
            "universe.json 缺少有效的 stocks 物件"
        )

    active = {}

    for symbol, identity in stocks.items():

        if not isinstance(identity, dict):

            continue

        status = str(
            identity.get("status", "")
        ).lower()

        stock_type = str(
            identity.get("type", "")
        ).upper()

        market = str(
            identity.get("market", "")
        ).upper()

        if status != "active":

            continue

        if stock_type != "STOCK":

            continue

        if market not in MARKETS:

            continue

        active[str(symbol).strip().upper()] = identity

    if not active:

        raise RuntimeError(
            "Universe 中沒有符合條件的有效上市櫃普通股"
        )

    return active


# ============================================================
# 行情分片讀取
# ============================================================

def load_price_shards():

    manifest = load_json(
        MANIFEST_FILE
    )

    schema = manifest.get(
        "schema_version"
    )

    if schema != PRICE_SCHEMA:

        raise RuntimeError(
            "行情版本不符。"
            f"預期：{PRICE_SCHEMA}；"
            f"實際：{schema}"
        )

    files = manifest.get("files")

    if not isinstance(files, list) or not files:

        raise RuntimeError(
            "manifest.json 沒有有效的 files 清單"
        )

    prices_by_symbol = {}

    for item in files:

        if isinstance(item, str):

            filename = item

        elif isinstance(item, dict):

            filename = (
                item.get("file")
                or item.get("path")
                or item.get("filename")
            )

        else:

            filename = None

        if not filename:

            raise RuntimeError(
                f"無效的行情分片項目：{item}"
            )

        filename = str(filename)

        # 防止路徑穿越
        if Path(filename).name != filename:

            raise RuntimeError(
                f"行情分片檔名不合法：{filename}"
            )

        shard_path = PRICE_DIR / filename

        shard = load_json(
            shard_path
        )

        if shard.get("schema_version") != PRICE_SCHEMA:

            raise RuntimeError(
                f"{filename} 行情版本不符"
            )

        shard_stocks = shard.get("stocks")

        if not isinstance(shard_stocks, dict):

            raise RuntimeError(
                f"{filename} 缺少 stocks 物件"
            )

        for symbol, rows in shard_stocks.items():

            symbol = str(
                symbol
            ).strip().upper()

            if symbol in prices_by_symbol:

                raise RuntimeError(
                    f"行情分片出現重複股票代號：{symbol}"
                )

            if not isinstance(rows, list):

                raise RuntimeError(
                    f"{symbol} 的行情資料不是陣列"
                )

            prices_by_symbol[symbol] = rows

    return prices_by_symbol


# ============================================================
# 建立單一股票日行情
# ============================================================

def normalize_daily_prices(rows):

    daily = []

    for row in rows:

        if not isinstance(row, dict):

            continue

        trading_date = parse_date(
            row.get("date")
        )

        close = parse_number(
            row.get("close")
        )

        if trading_date is None:

            continue

        if close is None:

            continue

        daily.append(
            (trading_date, close)
        )

    daily.sort(
        key=lambda item: item[0]
    )

    # 同一交易日若重複，只保留最後一筆
    unique = {}

    for trading_date, close in daily:

        unique[trading_date] = close

    return sorted(
        unique.items(),
        key=lambda item: item[0]
    )


# ============================================================
# 日K轉週K
# ============================================================

def build_weekly_closes(
    daily_prices,
    scan_date
):

    current_week_monday = (
        scan_date
        - timedelta(days=scan_date.weekday())
    )

    # 星期五才視為本週已完成
    include_current_week = (
        scan_date.weekday() == 4
    )

    weekly = {}

    for trading_date, close in daily_prices:

        week_monday = (
            trading_date
            - timedelta(days=trading_date.weekday())
        )

        # 排除尚未完成的當週週K
        if (
            week_monday == current_week_monday
            and not include_current_week
        ):

            continue

        iso = trading_date.isocalendar()

        week_key = (
            iso.year,
            iso.week
        )

        old = weekly.get(
            week_key
        )

        if old is None:

            weekly[week_key] = (
                trading_date,
                close
            )

        elif trading_date > old[0]:

            weekly[week_key] = (
                trading_date,
                close
            )

    return sorted(
        weekly.values(),
        key=lambda item: item[0]
    )


# ============================================================
# 計算週20MA
# ============================================================

def calculate_weekly_ma20(
    weekly_closes
):

    if len(weekly_closes) < REQUIRED_WEEKLY_BARS:

        return None

    latest_20 = weekly_closes[
        -REQUIRED_WEEKLY_BARS:
    ]

    closes = [
        close
        for _, close in latest_20
    ]

    ma20 = sum(closes) / REQUIRED_WEEKLY_BARS

    return {
        "ma20": ma20,
        "bars": len(latest_20),
        "start_date": latest_20[0][0].isoformat(),
        "end_date": latest_20[-1][0].isoformat()
    }


# ============================================================
# 全市場掃描
# ============================================================

def scan_market():

    print("=" * 65)

    print("台股全市場週20MA掃描")

    print("=" * 65)

    active_stocks = load_active_stocks()

    prices_by_symbol = load_price_shards()

    print(
        f"有效上市櫃普通股：{len(active_stocks)}"
    )

    print(
        f"有行情資料股票：{len(prices_by_symbol)}"
    )

    missing = sorted(
        set(active_stocks)
        - set(prices_by_symbol)
    )

    if missing:

        raise RuntimeError(
            "以下有效股票缺少行情資料，"
            f"禁止產生不完整掃描結果：{missing[:30]}"
        )

    # 使用全市場最新行情日期
    all_dates = []

    for symbol in active_stocks:

        rows = prices_by_symbol[symbol]

        for row in rows:

            if not isinstance(row, dict):

                continue

            trading_date = parse_date(
                row.get("date")
            )

            if trading_date:

                all_dates.append(
                    trading_date
                )

    if not all_dates:

        raise RuntimeError(
            "所有股票均沒有有效行情日期"
        )

    scan_date = max(all_dates)

    print(
        f"行情資料最新日期：{scan_date}"
    )

    passed = []

    excluded = Counter()

    # --------------------------------------------------------
    # 逐檔計算
    # --------------------------------------------------------

    for symbol, identity in active_stocks.items():

        daily = normalize_daily_prices(
            prices_by_symbol[symbol]
        )

        if not daily:

            excluded["no_valid_daily_prices"] += 1

            continue

        # 必須有掃描日收盤價
        if daily[-1][0] != scan_date:

            excluded["not_traded_on_scan_date"] += 1

            continue

        current_close = daily[-1][1]

        weekly_closes = build_weekly_closes(
            daily,
            scan_date
        )

        ma_result = calculate_weekly_ma20(
            weekly_closes
        )

        if ma_result is None:

            excluded["insufficient_weekly_history"] += 1

            continue

        weekly_ma20 = ma_result["ma20"]

        if current_close <= weekly_ma20:

            excluded["below_or_equal_weekly_ma20"] += 1

            continue

        distance_pct = (
            current_close / weekly_ma20 - 1
        ) * 100

        passed.append({

            "symbol": symbol,

            "name": identity.get("name"),

            "market": identity.get("market"),

            "close_date": scan_date.isoformat(),

            "close": round(
                current_close,
                2
            ),

            "weekly_ma20": round(
                weekly_ma20,
                4
            ),

            "distance_pct": round(
                distance_pct,
                2
            ),

            "weekly_bars": ma_result["bars"],

            "ma_start_date": ma_result["start_date"],

            "last_completed_week_date": ma_result["end_date"]

        })

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    passed.sort(
        key=lambda item: (
            -item["distance_pct"],
            item["symbol"]
        )
    )

    # --------------------------------------------------------
    # 結果
    # --------------------------------------------------------

    result = {

        "schema_version": OUTPUT_SCHEMA,

        "scan_date": scan_date.isoformat(),

        "generated_at": datetime.now().astimezone().isoformat(),

        "market_scope": [
            "TWSE",
            "TPEX"
        ],

        "instrument_scope": (
            "active common stocks only; ETFs excluded"
        ),

        "calculation": (
            "20-period arithmetic mean of completed weekly closes"
        ),

        "partial_week_policy": (
            "Monday-Thursday current week excluded; "
            "Friday current week included"
        ),

        "price_source": (
            "Data/prices/manifest.json"
        ),

        "universe_count": len(active_stocks),

        "passed_count": len(passed),

        "excluded_counts": dict(excluded),

        "candidates": passed

    }

    # --------------------------------------------------------
    # 原子寫入
    # --------------------------------------------------------

    atomic_write_json(
        OUTPUT_FILE,
        result
    )

    # --------------------------------------------------------
    # 讀回驗證
    # --------------------------------------------------------

    readback = load_json(
        OUTPUT_FILE
    )

    if readback != result:

        raise RuntimeError(
            "週20MA掃描結果讀回驗證失敗"
        )

    if readback["passed_count"] != len(
        readback["candidates"]
    ):

        raise RuntimeError(
            "候選股數量與實際資料筆數不一致"
        )

    # --------------------------------------------------------
    # 輸出摘要
    # --------------------------------------------------------

    print()

    print("=" * 65)

    print("掃描完成")

    print("=" * 65)

    print(
        f"掃描日期：{scan_date}"
    )

    print(
        f"上市櫃普通股：{len(active_stocks)}"
    )

    print(
        f"站上週20MA：{len(passed)}"
    )

    print(
        f"排除統計：{dict(excluded)}"
    )

    print(
        f"輸出檔案：{OUTPUT_FILE}"
    )

    print(
        "JSON 原子寫入：PASS"
    )

    print(
        "讀回驗證：PASS"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # 前20名顯示
    # --------------------------------------------------------

    print()

    print("站上週20MA幅度前20名")

    print("-" * 65)

    print(
        f"{'代號':<8}"
        f"{'名稱':<12}"
        f"{'收盤價':>10}"
        f"{'週20MA':>12}"
        f"{'高於均線':>12}"
    )

    print("-" * 65)

    for item in passed[:20]:

        print(
            f"{item['symbol']:<8}"
            f"{str(item['name'] or ''):<12}"
            f"{item['close']:>10.2f}"
            f"{item['weekly_ma20']:>12.4f}"
            f"{item['distance_pct']:>11.2f}%"
        )

    print("-" * 65)

    return result


# ============================================================
# 主程式
# ============================================================

if __name__ == "__main__":

    try:

        scan_market()

    except Exception as error:

        print()

        print(
            "週20MA掃描失敗：",
            str(error)
        )

        sys.exit(1)
