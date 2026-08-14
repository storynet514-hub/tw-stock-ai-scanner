# ============================================================
# 台股 AI 選股
# 6 項條件完整驗證工具 V3
#
# 目的：
# 1. 直接讀取 Data/prices.json
# 2. 使用 fetch_data.py 的 KNOWN_ETFS
# 3. 嚴格區分 STOCK / ETF
# 4. 驗證 6 項選股條件
# 5. 找出真正 6/6 個股
# 6. 找出 5/6、只差一項的個股
# 7. 驗證 statistics 與實際資料數量一致
#
# 6 項條件：
# 1. MACD 黃金交叉
# 2. RSI > 50
# 3. KD 黃金交叉
# 4. 成交量 >= 5 日均量 × 1.5
# 5. 股價 > MA20
# 6. MA20 向上
# ============================================================

import json
import csv
import os
import ast
from collections import Counter


# ============================================================
# 路徑
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

INPUT_FILE = os.path.join(
    BASE_DIR,
    "Data",
    "prices.json"
)

FETCH_FILE = os.path.join(
    BASE_DIR,
    "Scripts",
    "fetch_data.py"
)

OUTPUT_JSON = os.path.join(
    BASE_DIR,
    "Data",
    "qualified_stocks.json"
)

OUTPUT_CSV = os.path.join(
    BASE_DIR,
    "Data",
    "qualified_stocks.csv"
)

CANDIDATE_JSON = os.path.join(
    BASE_DIR,
    "Data",
    "watchlist_5of6.json"
)

CANDIDATE_CSV = os.path.join(
    BASE_DIR,
    "Data",
    "watchlist_5of6.csv"
)


# ============================================================
# 六項正式條件
# ============================================================

CONDITION_KEYS = [
    "macd_golden_cross",
    "rsi_above_50",
    "kd_golden_cross",
    "volume_expand",
    "above_ma20",
    "ma20_up",
]


CONDITION_NAMES = {
    "macd_golden_cross": "MACD 黃金交叉",
    "rsi_above_50": "RSI > 50",
    "kd_golden_cross": "KD 黃金交叉",
    "volume_expand": "成交量 >= MA5 × 1.5",
    "above_ma20": "股價 > MA20",
    "ma20_up": "MA20 向上",
}


# ============================================================
# 載入 fetch_data.py 的 KNOWN_ETFS
# ============================================================

def load_known_etfs():

    if not os.path.exists(FETCH_FILE):

        raise SystemExit(
            "ERROR：找不到 fetch_data.py："
            + FETCH_FILE
        )

    with open(
        FETCH_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        source = f.read()

    try:

        tree = ast.parse(source)

    except SyntaxError as e:

        raise SystemExit(
            f"ERROR：fetch_data.py 語法解析失敗：{e}"
        )

    known_etfs = None

    for node in tree.body:

        if not isinstance(
            node,
            ast.Assign
        ):
            continue

        for target in node.targets:

            if (
                isinstance(
                    target,
                    ast.Name
                )
                and target.id == "KNOWN_ETFS"
            ):

                try:

                    known_etfs = ast.literal_eval(
                        node.value
                    )

                except Exception as e:

                    raise SystemExit(
                        "ERROR：無法讀取 "
                        "KNOWN_ETFS："
                        + str(e)
                    )

    if not isinstance(
        known_etfs,
        (set, list, tuple)
    ):

        raise SystemExit(
            "ERROR：fetch_data.py "
            "沒有找到有效的 KNOWN_ETFS"
        )

    return {
        str(code).strip()
        for code in known_etfs
    }


# ============================================================
# 股票代號
# ============================================================

def get_code(item):

    code = item.get("code")

    if code is None:
        return ""

    return str(code).strip()


# ============================================================
# ETF 判斷
# ============================================================

def is_etf(
    item,
    known_etfs
):

    code = get_code(item)

    return code in known_etfs


# ============================================================
# 取得六項條件
# ============================================================

def get_conditions(item):

    conditions = item.get(
        "conditions",
        {}
    )

    if not isinstance(
        conditions,
        dict
    ):

        conditions = {}

    return {
        key: bool(
            conditions.get(
                key,
                False
            )
        )
        for key in CONDITION_KEYS
    }


# ============================================================
# 計算通過數
# ============================================================

def get_pass_count(
    conditions
):

    return sum(
        1
        for value in conditions.values()
        if value
    )


# ============================================================
# 找出唯一未通過條件
# ============================================================

def get_missing_condition(
    conditions
):

    missing = [
        key
        for key in CONDITION_KEYS
        if not conditions.get(
            key,
            False
        )
    ]

    if len(missing) == 1:

        return missing[0]

    return None


# ============================================================
# 建立股票輸出資料
# ============================================================

def build_stock_record(
    item,
    conditions
):

    return {

        "code":
            item.get("code"),

        "name":
            item.get("name"),

        "price":
            item.get("price"),

        "rsi":
            item.get("rsi"),

        "kd_k":
            item.get("kd_k"),

        "kd_d":
            item.get("kd_d"),

        "macd":
            item.get("macd"),

        "macd_signal":
            item.get(
                "macd_signal"
            ),

        "volume":
            item.get("volume"),

        "volume_ma5":
            item.get(
                "volume_ma5"
            ),

        "ma20":
            item.get("ma20"),

        "ai_score":
            item.get(
                "ai_score"
            ),

        "signal":
            item.get(
                "signal"
            ),

        "conditions":
            conditions,
    }


# ============================================================
# 主程式
# ============================================================

def main():

    print("=" * 80)

    print(
        "台股 6/6 選股完整驗證 V3"
    )

    print("=" * 80)

    print()

    # --------------------------------------------------------
    # 檔案檢查
    # --------------------------------------------------------

    if not os.path.exists(
        INPUT_FILE
    ):

        raise SystemExit(
            "ERROR：找不到 "
            + INPUT_FILE
        )

    # --------------------------------------------------------
    # 載入 KNOWN_ETFS
    # --------------------------------------------------------

    known_etfs = load_known_etfs()

    print(
        "KNOWN_ETFS 數量："
        + str(len(known_etfs))
    )

    print()

    # --------------------------------------------------------
    # 讀取 prices.json
    # --------------------------------------------------------

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)

    stocks = data.get(
        "stocks",
        []
    )

    if not isinstance(
        stocks,
        list
    ):

        raise SystemExit(
            "ERROR：prices.json "
            "中的 stocks 不是陣列"
        )

    print(
        "原始資料筆數："
        + str(len(stocks))
    )

    # --------------------------------------------------------
    # 取得 statistics
    # --------------------------------------------------------

    statistics = data.get(
        "statistics",
        {}
    )

    if not isinstance(
        statistics,
        dict
    ):

        statistics = {}

    expected_total = statistics.get(
        "total"
    )

    expected_stocks = statistics.get(
        "stocks"
    )

    expected_etf = statistics.get(
        "etf"
    )

    print()

    print("=" * 80)
    print("JSON 原始統計")
    print("=" * 80)

    print(
        "statistics.total : "
        + str(expected_total)
    )

    print(
        "statistics.stocks: "
        + str(expected_stocks)
    )

    print(
        "statistics.etf   : "
        + str(expected_etf)
    )

    # --------------------------------------------------------
    # 分類
    # --------------------------------------------------------

    etf_items = []

    stock_items = []

    unknown_items = []

    for item in stocks:

        if not isinstance(
            item,
            dict
        ):
            continue

        code = get_code(item)

        if not code:

            unknown_items.append(
                item
            )

            continue

        if is_etf(
            item,
            known_etfs
        ):

            etf_items.append(
                item
            )

        else:

            stock_items.append(
                item
            )

    # --------------------------------------------------------
    # 實際數量
    # --------------------------------------------------------

    actual_total = (
        len(stock_items)
        + len(etf_items)
        + len(unknown_items)
    )

    print()

    print("=" * 80)
    print("實際分類結果")
    print("=" * 80)

    print(
        "實際 STOCK："
        + str(len(stock_items))
    )

    print(
        "實際 ETF  ："
        + str(len(etf_items))
    )

    print(
        "未知資料   ："
        + str(len(unknown_items))
    )

    print(
        "實際總數   ："
        + str(actual_total)
    )

    # --------------------------------------------------------
    # 數量一致性檢查
    # --------------------------------------------------------

    classification_ok = True

    if (
        expected_total is not None
        and actual_total
        != expected_total
    ):

        classification_ok = False

        print()

        print(
            "ERROR：實際總數與 "
            "statistics.total 不一致"
        )

    if (
        expected_stocks is not None
        and len(stock_items)
        != expected_stocks
    ):

        classification_ok = False

        print()

        print(
            "ERROR：實際 STOCK 數量與 "
            "statistics.stocks 不一致"
        )

    if (
        expected_etf is not None
        and len(etf_items)
        != expected_etf
    ):

        classification_ok = False

        print()

        print(
            "ERROR：實際 ETF 數量與 "
            "statistics.etf 不一致"
        )

    if unknown_items:

        classification_ok = False

        print()

        print(
            "ERROR：存在未知分類資料"
        )

    # --------------------------------------------------------
    # 顯示分類結果
    # --------------------------------------------------------

    print()

    if classification_ok:

        print(
            "分類驗證：PASS"
        )

    else:

        print(
            "分類驗證：FAIL"
        )

        print()

        print(
            "為避免錯誤選股，"
            "程式仍會繼續輸出結果，"
            "但本次結果不能視為最終定案。"
        )

    # --------------------------------------------------------
    # 六項條件掃描
    # --------------------------------------------------------

    qualified = []

    five_of_six = []

    distribution = Counter()

    missing_conditions = Counter()

    # --------------------------------------------------------
    # 逐檔掃描
    # --------------------------------------------------------

    for item in stock_items:

        conditions = get_conditions(
            item
        )

        passed = get_pass_count(
            conditions
        )

        distribution[
            passed
        ] += 1

        if passed == 6:

            record = build_stock_record(
                item,
                conditions
            )

            qualified.append(
                record
            )

        elif passed == 5:

            missing = (
                get_missing_condition(
                    conditions
                )
            )

            record = build_stock_record(
                item,
                conditions
            )

            record[
                "missing_condition"
            ] = missing

            record[
                "missing_condition_name"
            ] = CONDITION_NAMES.get(
                missing,
                missing
            )

            five_of_six.append(
                record
            )

        # ----------------------------------------------------
        # 統計每項未通過
        # ----------------------------------------------------

        for key, value in (
            conditions.items()
        ):

            if not value:

                missing_conditions[
                    key
                ] += 1

    # --------------------------------------------------------
    # 排序 6/6
    # --------------------------------------------------------

    qualified.sort(
        key=lambda x: (
            -(
                x.get(
                    "ai_score"
                )
                or 0
            ),
            str(
                x.get("code")
                or ""
            )
        )
    )

    # --------------------------------------------------------
    # 排序 5/6
    #
    # 先依 AI Score
    # 再依 RSI
    # --------------------------------------------------------

    five_of_six.sort(
        key=lambda x: (
            -(
                x.get(
                    "ai_score"
                )
                or 0
            ),
            -(
                x.get(
                    "rsi"
                )
                or 0
            ),
            str(
                x.get("code")
                or ""
            )
        )
    )

    # --------------------------------------------------------
    # 輸出 6/6 JSON
    # --------------------------------------------------------

    qualified_output = {

        "verification_date":
            data.get(
                "generated_at",
                data.get(
                    "updated_at"
                )
            ),

        "source_file":
            "Data/prices.json",

        "classification_ok":
            classification_ok,

        "total_records":
            actual_total,

        "stock_count":
            len(stock_items),

        "etf_count":
            len(etf_items),

        "qualified_count":
            len(qualified),

        "required_conditions":
            CONDITION_KEYS,

        "condition_names":
            CONDITION_NAMES,

        "distribution": {
            f"{i}/6":
                distribution.get(
                    i,
                    0
                )
            for i in range(7)
        },

        "missing_condition_counts":
            dict(
                missing_conditions
            ),

        "qualified_stocks":
            qualified,
    }

    with open(
        OUTPUT_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            qualified_output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 輸出 6/6 CSV
    # --------------------------------------------------------

    fieldnames = [

        "code",
        "name",
        "price",
        "rsi",
        "kd_k",
        "kd_d",
        "macd",
        "macd_signal",
        "volume",
        "volume_ma5",
        "ma20",
        "ai_score",
        "signal",

    ]

    with open(
        OUTPUT_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for item in qualified:

            writer.writerow({

                key:
                    item.get(key)

                for key in fieldnames

            })

    # --------------------------------------------------------
    # 輸出 5/6 JSON
    # --------------------------------------------------------

    five_output = {

        "verification_date":
            data.get(
                "generated_at",
                data.get(
                    "updated_at"
                )
            ),

        "source_file":
            "Data/prices.json",

        "classification_ok":
            classification_ok,

        "stock_count":
            len(stock_items),

        "five_of_six_count":
            len(five_of_six),

        "required_conditions":
            CONDITION_KEYS,

        "condition_names":
            CONDITION_NAMES,

        "candidates":
            five_of_six,
    }

    with open(
        CANDIDATE_JSON,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            five_output,
            f,
            ensure_ascii=False,
            indent=2
        )

    # --------------------------------------------------------
    # 輸出 5/6 CSV
    # --------------------------------------------------------

    candidate_fields = [

        "code",
        "name",
        "price",
        "rsi",
        "kd_k",
        "kd_d",
        "macd",
        "macd_signal",
        "volume",
        "volume_ma5",
        "ma20",
        "ai_score",
        "signal",
        "missing_condition",
        "missing_condition_name",

    ]

    with open(
        CANDIDATE_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=candidate_fields
        )

        writer.writeheader()

        for item in five_of_six:

            writer.writerow({

                key:
                    item.get(key)

                for key in candidate_fields

            })

    # ========================================================
    # 顯示最終結果
    # ========================================================

    print()

    print("=" * 80)
    print("6/6 完整符合個股")
    print("=" * 80)

    print(
        "6/6 符合："
        + str(len(qualified))
        + " 檔"
    )

    print()

    if qualified:

        for index, item in enumerate(
            qualified,
            start=1
        ):

            print(

                f"{index:>3}. "

                f"{str(item.get('code') or ''):<8} "

                f"{str(item.get('name') or ''):<12} "

                f"股價={item.get('price')} "

                f"RSI={item.get('rsi')} "

                f"AI={item.get('ai_score')}"

            )

    else:

        print(
            "沒有任何個股達到 6/6。"
        )

    # ========================================================
    # 5/6
    # ========================================================

    print()

    print("=" * 80)
    print("5/6 候選個股：只差一項")
    print("=" * 80)

    print(
        "5/6 符合："
        + str(len(five_of_six))
        + " 檔"
    )

    print()

    if five_of_six:

        for index, item in enumerate(
            five_of_six,
            start=1
        ):

            print(

                f"{index:>3}. "

                f"{str(item.get('code') or ''):<8} "

                f"{str(item.get('name') or ''):<12} "

                f"股價={item.get('price')} "

                f"RSI={item.get('rsi')} "

                f"AI={item.get('ai_score')} "

                f"缺："
                f"{item.get('missing_condition_name')}"

            )

    else:

        print(
            "沒有 5/6 候選個股。"
        )

    # ========================================================
    # 0/6 ～ 6/6 分布
    # ========================================================

    print()

    print("=" * 80)
    print("0/6 ～ 6/6 分布")
    print("=" * 80)

    for i in range(7):

        print(

            f"{i}/6："
            f"{distribution.get(i, 0)} 檔"

        )

    # ========================================================
    # 各條件未通過數
    # ========================================================

    print()

    print("=" * 80)
    print("各條件未通過數")
    print("=" * 80)

    for key in CONDITION_KEYS:

        print(

            f"{CONDITION_NAMES[key]:<28}"

            f"{missing_conditions.get(key, 0)}"

        )

    # ========================================================
    # 輸出檔案
    # ========================================================

    print()

    print("=" * 80)
    print("輸出檔案")
    print("=" * 80)

    print(
        "6/6 JSON："
        + OUTPUT_JSON
    )

    print(
        "6/6 CSV ："
        + OUTPUT_CSV
    )

    print(
        "5/6 JSON："
        + CANDIDATE_JSON
    )

    print(
        "5/6 CSV ："
        + CANDIDATE_CSV
    )

    # ========================================================
    # 最終狀態
    # ========================================================

    print()

    print("=" * 80)

    if classification_ok:

        print(
            "資料分類驗證：PASS"
        )

        print(
            "本次 6/6 結果可以進入正式分析。"
        )

    else:

        print(
            "資料分類驗證：FAIL"
        )

        print(
            "本次結果不得視為最終定案。"
        )

    print("=" * 80)


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
