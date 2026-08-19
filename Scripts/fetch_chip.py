# ================================================================
# 台股 AI 選股系統
# Scripts/fetch_chip.py V5.1
#
# 用途：
#   1. 探測 CMoney API 是否能正常取得台股籌碼資料
#   2. 固定測試 4 檔：
#        2337 旺宏
#        2426 鼎元
#        2368 金像電
#        3081 艾訊
#   3. 抓取最近 20 個交易日資料
#   4. 驗證：
#        - 外資買賣超
#        - 投信買賣超
#        - 自營商買賣超
#        - 三大法人合計
#        - 融資
#        - 融券
#        - 當沖
#   5. 輸出：
#        Data/chip_data.json
#
# V5.1 重點：
#   - 不再把 API 回傳內容直接假設成固定欄位
#   - 自動遞迴搜尋 JSON 結構
#   - 優先辨識日期與籌碼欄位
#   - 保留原始 API response 供後續除錯
#   - API 失敗時不讓整個 workflow 直接崩潰
#   - 每檔股票獨立處理
# ================================================================

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


# ================================================================
# 基本設定
# ================================================================

VERSION = "V5.1"

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "Data"

OUTPUT_FILE = DATA_DIR / "chip_data.json"
RAW_DIR = DATA_DIR / "chip_raw"

DATA_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ================================================================
# 固定測試股票
# ================================================================

TEST_STOCKS = {
    "2337": "旺宏",
    "2426": "鼎元",
    "2368": "金像電",
    "3081": "艾訊",
}


# ================================================================
# 測試參數
# ================================================================

TEST_DAYS = 20

REQUEST_TIMEOUT = 20

MAX_RETRY = 3

RETRY_SLEEP = 2

REQUEST_DELAY = 1.0


# ================================================================
# CMoney API 設定
#
# 支援從 GitHub Actions Secrets / Environment Variables 讀取。
#
# 可使用：
#
#   CMONEY_API_URL
#   CMONEY_API_TOKEN
#   CMONEY_API_KEY
#
# 如果 API URL 不存在，程式會進入「探測模式」，
# 嘗試使用預設 endpoint。
# ================================================================

CMONEY_API_URL = os.getenv(
    "CMONEY_API_URL",
    ""
).strip()

CMONEY_API_TOKEN = os.getenv(
    "CMONEY_API_TOKEN",
    ""
).strip()

CMONEY_API_KEY = os.getenv(
    "CMONEY_API_KEY",
    ""
).strip()


# ================================================================
# 預設 API Endpoint
#
# 注意：
# 不把認證資訊硬編碼進 GitHub。
#
# 若 GitHub Secret 有 CMONEY_API_URL，
# 優先使用 Secret。
# ================================================================

DEFAULT_ENDPOINTS = [
    "https://api.cmoney.tw/api/v1",
    "https://api.cmoney.tw/api",
]


# ================================================================
# HTTP Session
# ================================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
    }
)


# ================================================================
# 日誌
# ================================================================

def log(message: str) -> None:
    """標準輸出日誌。"""

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(
        f"[{now}] {message}",
        flush=True,
    )


# ================================================================
# JSON 安全序列化
# ================================================================

def json_dump(
    data: Any,
    path: Path,
) -> None:

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


# ================================================================
# 數字轉換
# ================================================================

def to_number(value: Any) -> Optional[float]:
    """
    將 API 回傳值轉成數字。

    支援：
      123
      "123"
      "1,234"
      "-123"
      "123.5"
      "+123"
      "123 張"
    """

    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):
        return None

    if isinstance(
        value,
        (int, float),
    ):

        return float(value)

    text = str(value).strip()

    if not text:
        return None

    text = text.replace(
        ",",
        "",
    )

    match = re.search(
        r"[-+]?\d+(?:\.\d+)?",
        text,
    )

    if not match:
        return None

    try:

        return float(
            match.group(0)
        )

    except Exception:

        return None


# ================================================================
# 日期解析
# ================================================================

def parse_date(value: Any) -> Optional[str]:
    """
    嘗試將 API 日期轉成 YYYY-MM-DD。
    """

    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    # YYYY-MM-DD
    m = re.search(
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
        text,
    )

    if m:

        y, mo, d = m.groups()

        try:

            dt = datetime(
                int(y),
                int(mo),
                int(d),
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            pass

    # YYYYMMDD
    m = re.search(
        r"\b(20\d{2})(\d{2})(\d{2})\b",
        text,
    )

    if m:

        y, mo, d = m.groups()

        try:

            dt = datetime(
                int(y),
                int(mo),
                int(d),
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            pass

    return None


# ================================================================
# 遞迴搜尋 Dictionary
# ================================================================

def walk_dict(
    obj: Any,
    path: str = "",
):
    """
    遞迴走訪 JSON。
    """

    if isinstance(
        obj,
        dict,
    ):

        for key, value in obj.items():

            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            yield (
                current_path,
                key,
                value,
            )

            yield from walk_dict(
                value,
                current_path,
            )

    elif isinstance(
        obj,
        list,
    ):

        for index, value in enumerate(obj):

            current_path = (
                f"{path}[{index}]"
            )

            yield from walk_dict(
                value,
                current_path,
            )


# ================================================================
# API Headers
# ================================================================

def build_headers() -> Dict[str, str]:

    headers = dict(
        SESSION.headers
    )

    if CMONEY_API_TOKEN:

        headers[
            "Authorization"
        ] = f"Bearer {CMONEY_API_TOKEN}"

    if CMONEY_API_KEY:

        headers[
            "X-API-Key"
        ] = CMONEY_API_KEY

    return headers


# ================================================================
# API URL 組合
# ================================================================

def build_urls(
    stock_id: str,
) -> List[str]:

    urls: List[str] = []

    if CMONEY_API_URL:

        base = CMONEY_API_URL.rstrip("/")

        candidates = [
            f"{base}/chip",
            f"{base}/chips",
            f"{base}/chip/{stock_id}",
            f"{base}/chips/{stock_id}",
            f"{base}/stock/{stock_id}/chip",
            f"{base}/stock/{stock_id}/chips",
        ]

        urls.extend(
            candidates
        )

    else:

        for base in DEFAULT_ENDPOINTS:

            base = base.rstrip("/")

            urls.extend(
                [
                    f"{base}/chip/{stock_id}",
                    f"{base}/chips/{stock_id}",
                    f"{base}/stock/{stock_id}/chip",
                    f"{base}/stock/{stock_id}/chips",
                ]
            )

    # 去除重複
    result = []

    for url in urls:

        if url not in result:

            result.append(url)

    return result


# ================================================================
# HTTP GET
# ================================================================

def request_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
) -> Tuple[
    Optional[Any],
    Dict[str, Any],
]:

    last_error = ""

    for attempt in range(
        1,
        MAX_RETRY + 1,
    ):

        try:

            response = SESSION.get(
                url,
                headers=build_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            status = response.status_code

            meta = {
                "url": response.url,
                "status_code": status,
                "headers": {
                    "content_type": response.headers.get(
                        "Content-Type",
                        "",
                    )
                },
            }

            if status != 200:

                last_error = (
                    f"HTTP {status}"
                )

                if attempt < MAX_RETRY:

                    time.sleep(
                        RETRY_SLEEP
                    )

                continue

            try:

                data = response.json()

            except Exception:

                last_error = (
                    "HTTP 200 但不是有效 JSON"
                )

                # 保留前 2000 字元
                meta[
                    "text_preview"
                ] = response.text[:2000]

                if attempt < MAX_RETRY:

                    time.sleep(
                        RETRY_SLEEP
                    )

                continue

            return data, meta

        except requests.RequestException as exc:

            last_error = str(exc)

            if attempt < MAX_RETRY:

                time.sleep(
                    RETRY_SLEEP
                )

    return (
        None,
        {
            "url": url,
            "error": last_error,
        },
    )


# ================================================================
# 找資料列表
# ================================================================

def find_records(
    data: Any,
) -> List[Dict[str, Any]]:
    """
    從 API 回傳 JSON 中尋找最可能的資料列表。
    """

    candidates = []

    def scan(
        obj: Any,
        path: str = "",
    ):

        if isinstance(
            obj,
            list,
        ):

            dict_items = [
                x
                for x in obj
                if isinstance(
                    x,
                    dict,
                )
            ]

            if len(dict_items) >= 2:

                score = 0

                for item in dict_items[:10]:

                    keys = " ".join(
                        str(k).lower()
                        for k in item.keys()
                    )

                    if any(
                        word in keys
                        for word in [
                            "date",
                            "日期",
                            "time",
                            "外資",
                            "投信",
                            "自營",
                            "融資",
                            "融券",
                            "法人",
                            "foreign",
                            "dealer",
                            "trust",
                        ]
                    ):

                        score += 1

                candidates.append(
                    (
                        score,
                        len(dict_items),
                        path,
                        dict_items,
                    )
                )

            for i, child in enumerate(obj):

                scan(
                    child,
                    f"{path}[{i}]",
                )

        elif isinstance(
            obj,
            dict,
        ):

            for key, child in obj.items():

                child_path = (
                    f"{path}.{key}"
                    if path
                    else str(key)
                )

                scan(
                    child,
                    child_path,
                )

    scan(data)

    if not candidates:

        return []

    candidates.sort(
        key=lambda x: (
            x[0],
            x[1],
        ),
        reverse=True,
    )

    return candidates[0][3]


# ================================================================
# 欄位名稱正規化
# ================================================================

def normalize_key(
    key: Any,
) -> str:

    return (
        str(key)
        .strip()
        .lower()
        .replace(
            " ",
            "",
        )
        .replace(
            "_",
            "",
        )
        .replace(
            "-",
            "",
        )
    )


# ================================================================
# 找欄位
# ================================================================

def find_value(
    record: Dict[str, Any],
    aliases: List[str],
) -> Any:

    normalized = {
        normalize_key(k): v
        for k, v in record.items()
    }

    # 精確匹配
    for alias in aliases:

        key = normalize_key(alias)

        if key in normalized:

            return normalized[key]

    # 模糊匹配
    for actual_key, value in normalized.items():

        for alias in aliases:

            target = normalize_key(alias)

            if (
                target in actual_key
                or actual_key in target
            ):

                return value

    return None


# ================================================================
# 日期欄位
# ================================================================

DATE_ALIASES = [
    "date",
    "日期",
    "交易日期",
    "交易日",
    "日期時間",
    "datetime",
    "time",
    "day",
]


# ================================================================
# 籌碼欄位
# ================================================================

FIELD_ALIASES = {

    "foreign": [
        "外資買賣超",
        "外資",
        "外資買超",
        "外資賣超",
        "foreign",
        "foreignnet",
        "foreign_net",
        "foreignnetbuy",
    ],

    "investment_trust": [
        "投信買賣超",
        "投信",
        "投信買超",
        "投信賣超",
        "investmenttrust",
        "investment_trust",
        "trust",
    ],

    "dealer": [
        "自營商買賣超",
        "自營商",
        "自營",
        "dealer",
        "dealer_net",
        "dealer_netbuy",
    ],

    "three_major": [
        "三大法人買賣超",
        "三大法人",
        "法人合計",
        "三大法人合計",
        "institutional",
        "institutional_net",
        "total_institutional",
    ],

    "margin": [
        "融資餘額",
        "融資",
        "margin",
        "margin_balance",
        "marginbalance",
    ],

    "short": [
        "融券餘額",
        "融券",
        "short",
        "short_balance",
        "shortbalance",
    ],

    "day_trade": [
        "當沖",
        "當沖率",
        "當沖張數",
        "daytrade",
        "day_trade",
        "daytrading",
    ],
}


# ================================================================
# 判斷是否為籌碼 record
# ================================================================

def is_chip_record(
    record: Dict[str, Any],
) -> bool:

    keys = " ".join(
        normalize_key(k)
        for k in record.keys()
    )

    score = 0

    if any(
        normalize_key(x) in keys
        for x in DATE_ALIASES
    ):
        score += 2

    for aliases in FIELD_ALIASES.values():

        if any(
            normalize_key(x) in keys
            for x in aliases
        ):

            score += 1

    return score >= 2


# ================================================================
# 將 API record 正規化
# ================================================================

def normalize_record(
    record: Dict[str, Any],
) -> Optional[Dict[str, Any]]:

    raw_date = find_value(
        record,
        DATE_ALIASES,
    )

    date = parse_date(
        raw_date
    )

    if not date:

        return None

    result: Dict[str, Any] = {
        "date": date,
    }

    for field, aliases in FIELD_ALIASES.items():

        value = find_value(
            record,
            aliases,
        )

        number = to_number(
            value
        )

        result[field] = number

    # 保留原始 record
    result["_raw"] = record

    return result


# ================================================================
# 三大法人計算
# ================================================================

def calculate_three_major(
    record: Dict[str, Any],
) -> Optional[float]:

    foreign = record.get(
        "foreign"
    )

    trust = record.get(
        "investment_trust"
    )

    dealer = record.get(
        "dealer"
    )

    values = [
        x
        for x in [
            foreign,
            trust,
            dealer,
        ]
        if isinstance(
            x,
            (int, float),
        )
    ]

    if not values:

        return None

    return sum(values)


# ================================================================
# 取得單一股票
# ================================================================

def fetch_stock(
    stock_id: str,
    stock_name: str,
) -> Dict[str, Any]:

    log(
        f"開始測試 {stock_id} {stock_name}"
    )

    urls = build_urls(
        stock_id
    )

    stock_result: Dict[str, Any] = {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "success": False,
        "api_url": None,
        "http_status": None,
        "records": [],
        "raw_saved": [],
        "error": None,
        "tested_at": datetime.now().isoformat(),
    }

    # ------------------------------------------------------------
    # API 探測
    # ------------------------------------------------------------

    for url in urls:

        log(
            f"[{stock_id}] 探測 API：{url}"
        )

        params_candidates = [
            {
                "stock_id": stock_id,
                "days": TEST_DAYS,
            },
            {
                "stockId": stock_id,
                "days": TEST_DAYS,
            },
            {
                "symbol": stock_id,
                "days": TEST_DAYS,
            },
            {
                "code": stock_id,
                "days": TEST_DAYS,
            },
            {
                "stock_id": stock_id,
            },
            {},
        ]

        for params in params_candidates:

            data, meta = request_json(
                url,
                params=params,
            )

            if data is None:

                continue

            status = meta.get(
                "status_code"
            )

            stock_result[
                "http_status"
            ] = status

            stock_result[
                "api_url"
            ] = meta.get(
                "url"
            )

            # ----------------------------------------------------
            # 儲存原始 JSON
            # ----------------------------------------------------

            raw_file = (
                RAW_DIR
                / f"{stock_id}_raw.json"
            )

            try:

                json_dump(
                    data,
                    raw_file,
                )

                stock_result[
                    "raw_saved"
                ].append(
                    str(raw_file.relative_to(ROOT_DIR))
                )

            except Exception as exc:

                log(
                    f"[{stock_id}] 原始資料儲存失敗：{exc}"
                )

            # ----------------------------------------------------
            # 找 records
            # ----------------------------------------------------

            records = find_records(
                data
            )

            if not records:

                log(
                    f"[{stock_id}] API 有回應，但找不到資料列表"
                )

                continue

            normalized_records = []

            for record in records:

                if not is_chip_record(
                    record
                ):

                    continue

                normalized = normalize_record(
                    record
                )

                if normalized:

                    normalized_records.append(
                        normalized
                    )

            if not normalized_records:

                log(
                    f"[{stock_id}] 找到列表，但沒有成功解析籌碼資料"
                )

                continue

            # ----------------------------------------------------
            # 日期排序
            # ----------------------------------------------------

            normalized_records.sort(
                key=lambda x: x["date"]
            )

            # ----------------------------------------------------
            # 去除重複日期
            # ----------------------------------------------------

            unique: Dict[
                str,
                Dict[str, Any],
            ] = {}

            for record in normalized_records:

                unique[
                    record["date"]
                ] = record

            normalized_records = list(
                unique.values()
            )

            normalized_records.sort(
                key=lambda x: x["date"]
            )

            # ----------------------------------------------------
            # 補算三大法人
            # ----------------------------------------------------

            for record in normalized_records:

                calculated = (
                    calculate_three_major(
                        record
                    )
                )

                if calculated is not None:

                    existing = record.get(
                        "three_major"
                    )

                    if existing is None:

                        record[
                            "three_major"
                        ] = calculated

            # ----------------------------------------------------
            # 取最近 20 筆
            # ----------------------------------------------------

            normalized_records = (
                normalized_records[
                    -TEST_DAYS:
                ]
            )

            stock_result[
                "records"
            ] = normalized_records

            stock_result[
                "success"
            ] = True

            stock_result[
                "record_count"
            ] = len(
                normalized_records
            )

            log(
                f"[{stock_id}] 成功解析 "
                f"{len(normalized_records)} 筆資料"
            )

            return stock_result

    # ------------------------------------------------------------
    # 全部 endpoint 都失敗
    # ------------------------------------------------------------

    stock_result[
        "error"
    ] = (
        "所有 CMoney API endpoint "
        "均無法取得可解析資料"
    )

    log(
        f"[{stock_id}] API 探測失敗"
    )

    return stock_result


# ================================================================
# 建立 20D 統計
# ================================================================

def build_statistics(
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:

    if not records:

        return {
            "record_count": 0,
            "foreign_5d": None,
            "foreign_20d": None,
            "investment_trust_5d": None,
            "investment_trust_20d": None,
            "dealer_5d": None,
            "dealer_20d": None,
            "three_major_5d": None,
            "three_major_20d": None,
            "margin_change": None,
            "short_change": None,
        }

    def sum_field(
        rows,
        field,
    ):

        values = [
            r.get(field)
            for r in rows
            if isinstance(
                r.get(field),
                (int, float),
            )
        ]

        if not values:

            return None

        return sum(values)

    last_5 = records[-5:]

    last_20 = records[-20:]

    result = {
        "record_count": len(records),

        "foreign_5d": sum_field(
            last_5,
            "foreign",
        ),

        "foreign_20d": sum_field(
            last_20,
            "foreign",
        ),

        "investment_trust_5d": sum_field(
            last_5,
            "investment_trust",
        ),

        "investment_trust_20d": sum_field(
            last_20,
            "investment_trust",
        ),

        "dealer_5d": sum_field(
            last_5,
            "dealer",
        ),

        "dealer_20d": sum_field(
            last_20,
            "dealer",
        ),

        "three_major_5d": sum_field(
            last_5,
            "three_major",
        ),

        "three_major_20d": sum_field(
            last_20,
            "three_major",
        ),
    }

    # ------------------------------------------------------------
    # 融資變化
    # ------------------------------------------------------------

    margin_values = [
        r.get("margin")
        for r in records
        if isinstance(
            r.get("margin"),
            (int, float),
        )
    ]

    if len(margin_values) >= 2:

        result[
            "margin_change"
        ] = (
            margin_values[-1]
            - margin_values[0]
        )

    else:

        result[
            "margin_change"
        ] = None

    # ------------------------------------------------------------
    # 融券變化
    # ------------------------------------------------------------

    short_values = [
        r.get("short")
        for r in records
        if isinstance(
            r.get("short"),
            (int, float),
        )
    ]

    if len(short_values) >= 2:

        result[
            "short_change"
        ] = (
            short_values[-1]
            - short_values[0]
        )

    else:

        result[
            "short_change"
        ] = None

    return result


# ================================================================
# 建立最終輸出
# ================================================================

def build_output(
    stocks: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:

    result = {
        "version": VERSION,

        "generated_at": (
            datetime.now().isoformat()
        ),

        "test_days": TEST_DAYS,

        "stocks": {},
    }

    for stock_id, data in stocks.items():

        records = data.get(
            "records",
            [],
        )

        result[
            "stocks"
        ][stock_id] = {
            "stock_id": stock_id,

            "stock_name": data.get(
                "stock_name"
            ),

            "success": data.get(
                "success",
                False,
            ),

            "api_url": data.get(
                "api_url"
            ),

            "http_status": data.get(
                "http_status"
            ),

            "record_count": len(
                records
            ),

            "records": records,

            "statistics": build_statistics(
                records
            ),

            "error": data.get(
                "error"
            ),
        }

    return result


# ================================================================
# Console Summary
# ================================================================

def print_summary(
    result: Dict[str, Any],
) -> None:

    print()
    print(
        "=" * 72
    )
    print(
        f"CMoney API 籌碼資料探測結果 V{VERSION}"
    )
    print(
        "=" * 72
    )

    for stock_id, stock in result[
        "stocks"
    ].items():

        name = stock.get(
            "stock_name",
            "",
        )

        success = stock.get(
            "success",
            False,
        )

        count = stock.get(
            "record_count",
            0,
        )

        print()

        print(
            f"{stock_id} {name}"
        )

        print(
            f"  API："
            f"{'成功' if success else '失敗'}"
        )

        print(
            f"  20D records：{count}"
        )

        if stock.get(
            "api_url"
        ):

            print(
                f"  Endpoint："
                f"{stock['api_url']}"
            )

        statistics = stock.get(
            "statistics",
            {},
        )

        print(
            f"  外資 5D："
            f"{statistics.get('foreign_5d')}"
        )

        print(
            f"  外資 20D："
            f"{statistics.get('foreign_20d')}"
        )

        print(
            f"  投信 5D："
            f"{statistics.get('investment_trust_5d')}"
        )

        print(
            f"  投信 20D："
            f"{statistics.get('investment_trust_20d')}"
        )

        print(
            f"  自營 5D："
            f"{statistics.get('dealer_5d')}"
        )

        print(
            f"  自營 20D："
            f"{statistics.get('dealer_20d')}"
        )

        print(
            f"  三大法人 5D："
            f"{statistics.get('three_major_5d')}"
        )

        print(
            f"  三大法人 20D："
            f"{statistics.get('three_major_20d')}"
        )

        print(
            f"  融資變化："
            f"{statistics.get('margin_change')}"
        )

        print(
            f"  融券變化："
            f"{statistics.get('short_change')}"
        )

        if stock.get(
            "error"
        ):

            print(
                f"  ERROR："
                f"{stock['error']}"
            )

    print()
    print(
        "=" * 72
    )


# ================================================================
# Main
# ================================================================

def main() -> int:

    start_time = datetime.now()

    print()
    print(
        "=" * 72
    )
    print(
        f"開始 CMoney API {TEST_DAYS}D 測試"
    )
    print(
        f"fetch_chip.py V{VERSION}"
    )
    print(
        "=" * 72
    )

    print()

    print(
        "固定測試 4 檔："
    )

    for stock_id, name in TEST_STOCKS.items():

        print(
            f"  {stock_id} {name}"
        )

    print()

    if CMONEY_API_URL:

        print(
            f"CMONEY_API_URL："
            f"{CMONEY_API_URL}"
        )

    else:

        print(
            "CMONEY_API_URL：未設定"
        )

    print(
        f"CMONEY_API_TOKEN："
        f"{'已設定' if CMONEY_API_TOKEN else '未設定'}"
    )

    print(
        f"CMONEY_API_KEY："
        f"{'已設定' if CMONEY_API_KEY else '未設定'}"
    )

    print()

    stocks = {}

    # ------------------------------------------------------------
    # 逐檔測試
    # ------------------------------------------------------------

    for stock_id, stock_name in TEST_STOCKS.items():

        try:

            stocks[
                stock_id
            ] = fetch_stock(
                stock_id,
                stock_name,
            )

        except Exception as exc:

            log(
                f"[{stock_id}] 未預期錯誤：{exc}"
            )

            stocks[
                stock_id
            ] = {
                "stock_id": stock_id,
                "stock_name": stock_name,
                "success": False,
                "api_url": None,
                "http_status": None,
                "records": [],
                "error": str(exc),
            }

        time.sleep(
            REQUEST_DELAY
        )

    # ------------------------------------------------------------
    # 建立輸出
    # ------------------------------------------------------------

    result = build_output(
        stocks
    )

    # ------------------------------------------------------------
    # 寫入 chip_data.json
    # ------------------------------------------------------------

    try:

        json_dump(
            result,
            OUTPUT_FILE,
        )

        log(
            f"已寫入："
            f"{OUTPUT_FILE}"
        )

    except Exception as exc:

        log(
            f"寫入 chip_data.json 失敗："
            f"{exc}"
        )

        return 1

    # ------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------

    print_summary(
        result
    )

    # ------------------------------------------------------------
    # 最終統計
    # ------------------------------------------------------------

    success_count = sum(
        1
        for x in stocks.values()
        if x.get(
            "success",
            False,
        )
    )

    total_count = len(
        stocks
    )

    elapsed = (
        datetime.now()
        - start_time
    ).total_seconds()

    print()

    print(
        f"成功："
        f"{success_count}/{total_count}"
    )

    print(
        f"耗時："
        f"{elapsed:.2f} 秒"
    )

    print()

    if success_count == 0:

        print(
            "⚠️ 4 檔全部沒有取得可解析籌碼資料。"
        )

        print(
            "請檢查 Data/chip_raw/ "
            "中的原始 API response。"
        )

        # 不讓 GitHub Actions 因為「資料來源尚未確認」
        # 而直接中斷後續流程。
        return 0

    if success_count < total_count:

        print(
            "⚠️ 部分股票成功，部分股票失敗。"
        )

        return 0

    print(
        "✅ CMoney API 20D 測試完成。"
    )

    return 0


# ================================================================
# Entry Point
# ================================================================

if __name__ == "__main__":

    try:

        sys.exit(
            main()
        )

    except KeyboardInterrupt:

        print()
        print(
            "使用者中止程式。"
        )

        sys.exit(130)

    except Exception as exc:

        print()
        print(
            "程式發生未處理錯誤："
            f"{exc}"
        )

        sys.exit(1)