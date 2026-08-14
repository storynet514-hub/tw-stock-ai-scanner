# ============================================================
# 台股 AI 選股・零股定投・動態風控
# fetch_data.py V7.2 正式版
#
# V7.2 主要修正：
#
# 1. 自動建立台股全市場清單
#    - TWSE 上市股票
#    - TPEx 上櫃股票
#    - TWSE ETF
#
# 2. Yahoo Finance 市場代號修正
#    - 上市股票：XXXX.TW
#    - ETF：XXXX.TW
#    - 上櫃股票：XXXX.TWO
#
# 3. 不再使用固定 STOCK_LIST 作為主要掃描來源
#
# 4. 0050 / 0056 / 00713 / 00878 / 00919 等 ETF
#    納入全市場掃描
#
# 5. 保留 V6/V7 原有 JSON 結構
#
# 6. RSI 強制限制 0~100
#
# 7. KD 強制限制 0~100
#
# 8. 單一標的抓取失敗不影響其他標的
#
# 9. 自動去除重複代號
#
# 10. AI SCORE 保留原本邏輯
#
# 11. DCA 四段式價格保留
#
# 12. rankings / statistics 保留
#
# 13. 增加市場來源與分類資訊
#
# ============================================================

import os
import sys
import json
import math
import time
import traceback
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

try:
    import requests
except ImportError:
    requests = None


# ============================================================
# 基本設定
# ============================================================

VERSION = "V7.2"

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DATA_DIR = os.path.join(
    BASE_DIR,
    "Data"
)

OUTPUT_FILE = os.path.join(
    DATA_DIR,
    "prices.json"
)

os.makedirs(
    DATA_DIR,
    exist_ok=True
)


# ============================================================
# 台灣時區
# ============================================================

TW_TZ = timezone(
    timedelta(hours=8)
)


# ============================================================
# 網路設定
# ============================================================

REQUEST_TIMEOUT = 20

YF_PERIOD = "1y"

YF_INTERVAL = "1d"

REQUEST_DELAY = 0.08

MAX_RETRY = 2


# ============================================================
# User-Agent
# ============================================================

HEADERS = {
    "User-Agent":
        (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        )
}


# ============================================================
# 手動保底 ETF
#
# 即使官方 ETF 清單 API 暫時異常，
# 這些常見 ETF 仍會被加入。
#
# 這不是固定掃描股票清單，
# 而是 ETF API 的安全保底。
# ============================================================

ETF_FALLBACK = [

    ("0050", "元大台灣50"),
    ("0051", "元大中型100"),
    ("0052", "富邦科技"),
    ("0053", "元大電子"),
    ("0055", "元大MSCI金融"),
    ("0056", "元大高股息"),
    ("0057", "富邦摩台"),
    ("0061", "元大寶滬深"),
    ("006203", "元大MSCI台灣"),
    ("006208", "富邦台50"),
    ("00631L", "元大台灣50正2"),
    ("00632R", "元大台灣50反1"),
    ("00633L", "富邦上証正2"),
    ("00634R", "富邦上証反1"),
    ("00635U", "期元大S&P黃金"),
    ("00636", "國泰中國A50"),
    ("00637L", "元大滬深300正2"),
    ("00638R", "元大滬深300反1"),
    ("00639", "富邦深100"),
    ("00640L", "富邦日本正2"),
    ("00641R", "富邦日本反1"),
    ("00642U", "期元大S&P石油"),
    ("00643", "群益深証中小"),
    ("00646", "元大S&P500"),
    ("00647L", "元大S&P500正2"),
    ("00648R", "元大S&P500反1"),
    ("00650L", "復華香港正2"),
    ("00651R", "復華香港反1"),
    ("00652", "富邦印度"),
    ("00653L", "元大印度2X"),
    ("00654R", "元大日本反1"),
    ("00655L", "國泰中國A50正2"),
    ("00656R", "國泰中國A50反1"),
    ("00657", "國泰日經225"),
    ("00660", "元大歐洲50"),
    ("00661", "元大日經225"),
    ("00662", "富邦NASDAQ"),
    ("00663L", "國泰臺灣加權正2"),
    ("00664R", "國泰臺灣加權反1"),
    ("00665L", "富邦恒生國企正2"),
    ("00666R", "富邦恒生國企反1"),
    ("00668", "國泰美國道瓊"),
    ("00669R", "國泰美國道瓊反1"),
    ("00670L", "富邦NASDAQ正2"),
    ("00671R", "富邦NASDAQ反1"),
    ("00672L", "元大S&P原油正2"),
    ("00673R", "元大S&P原油反1"),
    ("00674R", "元大滬深300反1"),
    ("00675L", "富邦臺灣加權正2"),
    ("00676R", "富邦臺灣加權反1"),
    ("00677U", "富邦VIX"),
    ("00678", "群益NBI生技"),
    ("00679B", "元大美債20年"),
    ("00680L", "元大美債20正2"),
    ("00681R", "元大美債20反1"),
    ("00682U", "期元大美元指正2"),
    ("00683L", "元大美元指數正2"),
    ("00684R", "元大美元指數反1"),
    ("00685L", "群益臺灣加權正2"),
    ("00686R", "群益臺灣加權反1"),
    ("00688L", "國泰20年美債正2"),
    ("00689R", "國泰20年美債反1"),
    ("00690", "兆豐藍籌30"),
    ("00692", "富邦公司治理"),
    ("00700", "富邦恒生國企"),
    ("00701", "國泰股利精選30"),
    ("00702", "國泰恒生中國企業"),
    ("00703", "台新MSCI中國"),
    ("00706L", "新光中國政金綠債"),
    ("00707", "統一FANG+"),
    ("00708L", "期元大S&P黃金正2"),
    ("00709", "富邦歐洲"),
    ("00710B", "復華彭博非投等債"),
    ("00711", "復華彭博投資級債"),
    ("00713", "元大台灣高息低波"),
    ("00714", "群益道瓊美國地產"),
    ("00715L", "期街口布蘭特正2"),
    ("00717", "富邦美國特別股"),
    ("00720B", "元大投資級公司債"),
    ("00727B", "國泰1-5Y非投等債"),
    ("00730", "富邦臺灣優質高息"),
    ("00731", "FH富時高息低波"),
    ("00733", "富邦臺灣中小"),
    ("00735", "國泰臺韓科技"),
    ("00736", "國泰新興市場"),
    ("00737", "國泰AI+Robo"),
    ("00739", "元大MSCI A股"),
    ("00740B", "富邦全球投等債"),
    ("00741B", "富邦全球非投等債"),
    ("00742", "新光內需收益"),
    ("00743", "國泰中國A50"),
    ("00752", "中信中國50"),
    ("00753L", "中信中國50正2"),
    ("00757", "統一FANG+"),
    ("00758B", "復華能源債"),
    ("00759B", "復華製藥債"),
    ("00760B", "復華新興債"),
    ("00761B", "國泰A級公司債"),
    ("00762", "元大全球AI"),
    ("00763", "期街口道瓊銅"),
    ("00770", "國泰北美科技"),
    ("00771", "元大US高息特別股"),
    ("00772B", "中信高評級公司債"),
    ("00773B", "中信優先金融債"),
    ("00774B", "新光投等債15+"),
    ("00775B", "新光投等債20+"),
    ("00779B", "凱基美國非投等債"),
    ("00782B", "國泰A級公用債"),
    ("00783", "富邦中國ETF"),
    ("00784B", "富邦中國投等債"),
    ("00785B", "富邦金融投等債"),
    ("00786B", "元大10年IG銀行債"),
    ("00787B", "元大10年IG醫療債"),
    ("00788B", "元大10年IG電信債"),
    ("00789B", "復華金融債"),
    ("00790", "復華次順位金融債"),
    ("00791B", "新光投等債20年"),
    ("00793B", "中信優先金融債"),
    ("00795B", "中信美國公債20年"),
    ("00796B", "台新20年美債"),
    ("00830", "國泰費城半導體"),
    ("00850", "元大臺灣ESG永續"),
    ("00851", "台新全球AI"),
    ("00852L", "國泰美國道瓊正2"),
    ("00853L", "統一FANG+正2"),
    ("00854", "新光日本半導體"),
    ("00858", "永豐台灣ESG"),
    ("00859B", "群益0-1年美債"),
    ("00860B", "群益1-5年美債"),
    ("00861", "元大全球未來通訊"),
    ("00865B", "國泰A級公司債"),
    ("00875", "國泰網路資安"),
    ("00876", "元大全球5G"),
    ("00878", "國泰永續高股息"),
    ("00881", "國泰台灣5G+"),
    ("00882", "中信中國高股息"),
    ("00885", "富邦越南"),
    ("00886", "永豐台灣ESG低碳"),
    ("00887", "永豐中國科技50大"),
    ("00891", "中信小資高價30"),
    ("00892", "富邦台灣半導體"),
    ("00893", "國泰智能電動車"),
    ("00894", "中信小資高價30"),
    ("00895", "富邦基因免疫生技"),
    ("00896", "中信綠能及電動車"),
    ("00897", "北富銀台灣ESG"),
    ("00898", "富邦金融投等債"),
    ("00899", "聯邦投等債"),
    ("00900", "富邦特選高股息30"),
    ("00901", "永豐智能車供應鏈"),
    ("00902", "中信電池及儲能"),
    ("00903", "富邦入息REITs+"),
    ("00904", "新光臺灣半導體30"),
    ("00905", "FT臺灣Smart"),
    ("00907", "永豐優息存股"),
    ("00908", "富邦入息"),
    ("00909", "國泰數位支付服務"),
    ("00910", "第一金太空衛星"),
    ("00911", "兆豐洲際半導體"),
    ("00912", "中信臺灣智慧50"),
    ("00913", "兆豐台灣晶圓製造"),
    ("00915", "凱基優選高股息30"),
    ("00916", "國泰全球品牌50"),
    ("00917", "中信特選金融"),
    ("00918", "大華優利高填息30"),
    ("00919", "群益台灣精選高息"),
    ("00920", "野村臺灣智慧優選主動式"),
    ("00921", "兆豐龍頭等權重"),
    ("00922", "國泰台灣領袖50"),
    ("00923", "群益台ESG低碳50"),
    ("00924", "復華美國標普500低波"),
    ("00925", "野村趨勢動能高息"),
    ("00926", "凱基全球菁英55"),
    ("00927", "群益半導體收益"),
    ("00928", "中信上櫃ESG30"),
    ("00929", "復華台灣科技優息"),
    ("00930", "永豐ESG低碳高息"),
    ("00932", "兆豐永續高息等權"),
    ("00934", "中信成長高股息"),
    ("00935", "野村臺灣新科技50"),
    ("00936", "台新永續高息中小"),
    ("00937B", "群益ESG投等債20+"),
    ("00938", "凱基優選30"),
    ("00939", "統一台灣高息動能"),
    ("00940", "元大台灣價值高息"),
    ("00941", "中信上游半導體"),
    ("00942", "台新美A公司債20+"),
    ("00943", "兆豐電子高息等權"),
    ("00944", "野村趨勢航太"),
    ("00945B", "凱基優選投等債"),
    ("00946", "群益科技高息成長"),
    ("00947", "台新臺灣IC設計"),
    ("00949", "復華日本龍頭"),
    ("00950", "野村臺灣智慧優選主動式"),
    ("00951", "中信日本商社"),
    ("00952", "凱基台灣優選30"),
    ("00953B", "群益優選非投等債"),
    ("00954", "中信日本半導體"),
    ("00955", "中信日本商社"),
    ("00956", "中信成長高股息"),
]


# ============================================================
# 安全數字
# ============================================================

def safe_float(value, default=None):

    try:

        if value is None:
            return default

        if isinstance(
            value,
            (
                list,
                tuple,
                dict
            )
        ):
            return default

        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:

        return default


# ============================================================
# 四捨五入
# ============================================================

def round_value(
    value,
    digits=2
):

    number = safe_float(
        value
    )

    if number is None:
        return None

    return round(
        number,
        digits
    )


# ============================================================
# 股票代號清理
# ============================================================

def clean_code(value):

    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in [
        "",
        "nan",
        "none",
        "null"
    ]:
        return None

    # 移除可能出現的 .TW / .TWO
    text = (
        text
        .replace(
            ".TW",
            ""
        )
        .replace(
            ".TWO",
            ""
        )
        .strip()
    )

    return text


# ============================================================
# 判斷是否為有效台股代號
# ============================================================

def valid_taiwan_code(
    code
):

    code = clean_code(
        code
    )

    if not code:
        return False

    # 台股代號通常 4~6 碼
    if len(code) < 4 or len(code) > 6:
        return False

    # 英數字
    for char in code:

        if not (
            char.isdigit()
            or
            char.isalpha()
        ):

            return False

    return True


# ============================================================
# Yahoo symbol
# ============================================================

def yahoo_symbol(
    code,
    market="上市"
):

    code = clean_code(
        code
    )

    if market == "上櫃":

        return (
            f"{code}.TWO"
        )

    return (
        f"{code}.TW"
    )


# ============================================================
# HTTP GET
# ============================================================

def http_get_json(
    url,
    params=None
):

    if requests is None:

        print(
            "requests 套件不存在"
        )

        return None

    for attempt in range(
        MAX_RETRY
    ):

        try:

            response = requests.get(
                url,
                params=params,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as error:

            print(
                f"HTTP 失敗 "
                f"{attempt + 1}/{MAX_RETRY}: "
                f"{error}"
            )

            time.sleep(
                1
            )

    return None


# ============================================================
# TWSE 上市股票清單
# ============================================================

def fetch_twse_stock_list():

    print(
        ""
    )

    print(
        "取得 TWSE 上市股票清單..."
    )

    url = (
        "https://openapi.twse.com.tw/"
        "v1/exchangeReport/STOCK_DAY_ALL"
    )

    data = http_get_json(
        url
    )

    result = []

    if isinstance(
        data,
        list
    ):

        for row in data:

            code = clean_code(
                row.get(
                    "Code"
                )
            )

            name = str(
                row.get(
                    "Name",
                    ""
                )
            ).strip()

            if not valid_taiwan_code(
                code
            ):

                continue

            if not name:
                continue

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "上市",
                    "type": "STOCK",
                    "source": "TWSE"
                }
            )

    print(
        f"TWSE 上市股票："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TPEx 上櫃股票清單
#
# 使用 TPEx 公開資訊 API。
# ============================================================

def fetch_tpex_stock_list():

    print(
        ""
    )

    print(
        "取得 TPEx 上櫃股票清單..."
    )

    url = (
        "https://www.tpex.org.tw/"
        "openapi/v1/tpex_mainboard_peratio_analysis"
    )

    data = http_get_json(
        url
    )

    result = []

    if isinstance(
        data,
        list
    ):

        for row in data:

            code = None
            name = None

            # 可能欄位名稱
            for key in [
                "SecuritiesCompanyCode",
                "SecuritiesCompanyCode",
                "Code",
                "證券代號"
            ]:

                if key in row:

                    code = clean_code(
                        row.get(
                            key
                        )
                    )

                    if code:
                        break

            for key in [
                "CompanyName",
                "Name",
                "公司名稱",
                "證券名稱"
            ]:

                if key in row:

                    value = row.get(
                        key
                    )

                    if value is not None:

                        name = str(
                            value
                        ).strip()

                    if name:
                        break

            if not valid_taiwan_code(
                code
            ):

                continue

            if not name:
                name = code

            result.append(
                {
                    "id": code,
                    "name": name,
                    "market": "上櫃",
                    "type": "STOCK",
                    "source": "TPEx"
                }
            )

    # --------------------------------------------------------
    # 若 API 格式變更，嘗試其他公開 API
    # --------------------------------------------------------

    if len(result) < 500:

        print(
            "TPEx 第一來源資料不足，"
            "嘗試備援來源..."
        )

        backup_urls = [

            (
                "https://www.tpex.org.tw/"
                "openapi/v1/tpex_mainboard_quotes"
            ),

            (
                "https://www.tpex.org.tw/"
                "openapi/v1/tpex_mainboard_daily_close_quotes"
            )

        ]

        for url in backup_urls:

            data = http_get_json(
                url
            )

            if not isinstance(
                data,
                list
            ):

                continue

            temp = []

            for row in data:

                code = None
                name = None

                for key in [
                    "SecuritiesCompanyCode",
                    "Code",
                    "證券代號"
                ]:

                    if key in row:

                        code = clean_code(
                            row.get(
                                key
                            )
                        )

                        if code:
                            break

                for key in [
                    "CompanyName",
                    "Name",
                    "證券名稱"
                ]:

                    if key in row:

                        value = row.get(
                            key
                        )

                        if value is not None:

                            name = str(
                                value
                            ).strip()

                        if name:
                            break

                if (
                    valid_taiwan_code(code)
                    and
                    name
                ):

                    temp.append(
                        {
                            "id": code,
                            "name": name,
                            "market": "上櫃",
                            "type": "STOCK",
                            "source": "TPEx"
                        }
                    )

            if len(temp) > len(result):

                result = temp

            if len(result) >= 500:
                break

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        unique[
            item["id"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"TPEx 上櫃股票："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# TWSE ETF 清單
#
# ETF 官方清單取得失敗時使用保底清單。
# ============================================================

def fetch_twse_etf_list():

    print(
        ""
    )

    print(
        "取得 ETF 清單..."
    )

    result = []

    # --------------------------------------------------------
    # 官方 ETF 頁面
    # --------------------------------------------------------

    urls = [

        (
            "https://www.twse.com.tw/"
            "rwd/zh/ETF/etfinfo"
        ),

        (
            "https://www.twse.com.tw/"
            "rwd/zh/ETF/etfDetail"
        ),

        (
            "https://www.twse.com.tw/"
            "exchangeReport/BWIBBU_d"
        )

    ]

    for url in urls:

        data = http_get_json(
            url
        )

        if not isinstance(
            data,
            dict
        ):

            continue

        candidates = []

        for key in [
            "data",
            "data1",
            "tables"
        ]:

            value = data.get(
                key
            )

            if isinstance(
                value,
                list
            ):

                candidates.extend(
                    value
                )

        for row in candidates:

            if not isinstance(
                row,
                list
            ):

                continue

            if len(row) < 2:
                continue

            code = clean_code(
                row[0]
            )

            name = str(
                row[1]
            ).strip()

            if (
                valid_taiwan_code(code)
                and
                name
            ):

                # ETF 代號通常 00 開頭
                if code.startswith(
                    "00"
                ):

                    result.append(
                        {
                            "id": code,
                            "name": name,
                            "market": "上市",
                            "type": "ETF",
                            "source": "TWSE ETF"
                        }
                    )

        if len(result) >= 50:
            break

    # --------------------------------------------------------
    # 保底 ETF
    # --------------------------------------------------------

    existing = {
        item["id"]
        for item in result
    }

    for code, name in ETF_FALLBACK:

        if code in existing:
            continue

        result.append(
            {
                "id": code,
                "name": name,
                "market": "上市",
                "type": "ETF",
                "source": "ETF fallback"
            }
        )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in result:

        unique[
            item["id"]
        ] = item

    result = list(
        unique.values()
    )

    print(
        f"ETF："
        f"{len(result)} 檔"
    )

    return result


# ============================================================
# 建立全市場清單
# ============================================================

def build_market_list():

    twse = fetch_twse_stock_list()

    tpex = fetch_tpex_stock_list()

    etf = fetch_twse_etf_list()

    all_items = []

    all_items.extend(
        twse
    )

    all_items.extend(
        tpex
    )

    all_items.extend(
        etf
    )

    # --------------------------------------------------------
    # 去重
    # --------------------------------------------------------

    unique = {}

    for item in all_items:

        code = item.get(
            "id"
        )

        if not code:
            continue

        # ETF 優先
        if (
            code not in unique
            or
            item.get("type") == "ETF"
        ):

            unique[
                code
            ] = item

    market_list = list(
        unique.values()
    )

    # --------------------------------------------------------
    # 排序
    # --------------------------------------------------------

    def sort_key(item):

        market_order = {
            "上市": 1,
            "上櫃": 2
        }

        type_order = {
            "STOCK": 1,
            "ETF": 2
        }

        return (
            market_order.get(
                item.get(
                    "market"
                ),
                9
            ),
            type_order.get(
                item.get(
                    "type"
                ),
                9
            ),
            item.get(
                "id",
                ""
            )
        )

    market_list.sort(
        key=sort_key
    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "全市場清單完成"
    )

    print(
        f"市場清單："
        f"{len(market_list)} 檔"
    )

    print(
        f"上市股票："
        f"{sum(1 for x in market_list if x['market'] == '上市' and x['type'] == 'STOCK')} 檔"
    )

    print(
        f"上櫃股票："
        f"{sum(1 for x in market_list if x['market'] == '上櫃' and x['type'] == 'STOCK')} 檔"
    )

    print(
        f"ETF："
        f"{sum(1 for x in market_list if x['type'] == 'ETF')} 檔"
    )

    print(
        "================================================"
    )

    return market_list


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    close,
    period=14
):

    close = pd.to_numeric(
        close,
        errors="coerce"
    )

    delta = close.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    rsi = rsi.where(
        avg_loss != 0,
        100
    )

    rsi = rsi.clip(
        0,
        100
    )

    return rsi


# ============================================================
# KD
# ============================================================

def calculate_kd(
    high,
    low,
    close,
    period=9
):

    lowest_low = low.rolling(
        period
    ).min()

    highest_high = high.rolling(
        period
    ).max()

    denominator = (
        highest_high -
        lowest_low
    )

    denominator = denominator.replace(
        0,
        np.nan
    )

    rsv = (
        (
            close -
            lowest_low
        )
        /
        denominator
    ) * 100

    rsv = rsv.clip(
        0,
        100
    )

    k = rsv.ewm(
        com=2,
        adjust=False
    ).mean()

    d = k.ewm(
        com=2,
        adjust=False
    ).mean()

    k = k.clip(
        0,
        100
    )

    d = d.clip(
        0,
        100
    )

    return k, d


# ============================================================
# MACD
# ============================================================

def calculate_macd(
    close,
    fast=12,
    slow=26,
    signal=9
):

    ema_fast = close.ewm(
        span=fast,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=slow,
        adjust=False
    ).mean()

    macd = (
        ema_fast -
        ema_slow
    )

    signal_line = macd.ewm(
        span=signal,
        adjust=False
    ).mean()

    histogram = (
        macd -
        signal_line
    )

    return (
        macd,
        signal_line,
        histogram
    )


# ============================================================
# 最新值
# ============================================================

def get_last_value(
    series
):

    try:

        series = pd.to_numeric(
            series,
            errors="coerce"
        ).dropna()

        if len(series) == 0:
            return None

        return float(
            series.iloc[-1]
        )

    except Exception:

        return None


# ============================================================
# 下載股票資料
# ============================================================

def download_stock(
    code,
    market
):

    symbol = yahoo_symbol(
        code,
        market
    )

    for attempt in range(
        MAX_RETRY
    ):

        try:

            df = yf.download(
                symbol,
                period=YF_PERIOD,
                interval=YF_INTERVAL,
                auto_adjust=False,
                progress=False,
                threads=False
            )

            if df is None:
                raise ValueError(
                    "Yahoo 回傳 None"
                )

            if df.empty:
                raise ValueError(
                    "Yahoo 無資料"
                )

            # ------------------------------------------------
            # MultiIndex
            # ------------------------------------------------

            if isinstance(
                df.columns,
                pd.MultiIndex
            ):

                df.columns = [
                    column[0]
                    for column in df.columns
                ]

            # ------------------------------------------------
            # 欄位名稱
            # ------------------------------------------------

            df.columns = [
                str(column)
                .strip()
                .lower()
                for column in df.columns
            ]

            required = [
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]

            for column in required:

                if column not in df.columns:

                    raise ValueError(
                        f"缺少欄位 {column}"
                    )

            # ------------------------------------------------
            # 數字化
            # ------------------------------------------------

            for column in required:

                df[column] = pd.to_numeric(
                    df[column],
                    errors="coerce"
                )

            df = df.dropna(
                subset=[
                    "close"
                ]
            )

            if len(df) < 35:

                raise ValueError(
                    f"歷史資料不足：{len(df)}"
                )

            return df

        except Exception as error:

            if attempt + 1 >= MAX_RETRY:

                print(
                    f"{code} "
                    f"{symbol} "
                    f"失敗：{error}"
                )

            else:

                time.sleep(
                    0.5
                )

    return None


# ============================================================
# 分析股票
# ============================================================

def analyze_stock(
    item,
    df
):

    code = item["id"]
    name = item["name"]
    market = item["market"]
    stock_type = item["type"]

    try:

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # ----------------------------------------------------
        # 技術指標
        # ----------------------------------------------------

        ma5 = close.rolling(
            5
        ).mean()

        ma20 = close.rolling(
            20
        ).mean()

        ma60 = close.rolling(
            60
        ).mean()

        rsi = calculate_rsi(
            close,
            14
        )

        k, d = calculate_kd(
            high,
            low,
            close
        )

        macd, macd_signal, macd_hist = (
            calculate_macd(
                close
            )
        )

        volume_ma5 = volume.rolling(
            5
        ).mean()

        # ----------------------------------------------------
        # 最新資料
        # ----------------------------------------------------

        current_price = get_last_value(
            close
        )

        previous_price = (
            safe_float(
                close.iloc[-2]
            )
            if len(close) >= 2
            else None
        )

        current_ma5 = get_last_value(
            ma5
        )

        current_ma20 = get_last_value(
            ma20
        )

        current_ma60 = get_last_value(
            ma60
        )

        current_rsi = get_last_value(
            rsi
        )

        current_k = get_last_value(
            k
        )

        current_d = get_last_value(
            d
        )

        current_macd = get_last_value(
            macd
        )

        current_macd_signal = get_last_value(
            macd_signal
        )

        current_macd_hist = get_last_value(
            macd_hist
        )

        current_volume = get_last_value(
            volume
        )

        current_volume_ma5 = get_last_value(
            volume_ma5
        )

        # ----------------------------------------------------
        # RSI 0~100
        # ----------------------------------------------------

        if current_rsi is not None:

            current_rsi = max(
                0,
                min(
                    100,
                    current_rsi
                )
            )

        # ----------------------------------------------------
        # KD 0~100
        # ----------------------------------------------------

        if current_k is not None:

            current_k = max(
                0,
                min(
                    100,
                    current_k
                )
            )

        if current_d is not None:

            current_d = max(
                0,
                min(
                    100,
                    current_d
                )
            )

        # ----------------------------------------------------
        # 成交量比
        # ----------------------------------------------------

        if (
            current_volume is not None
            and
            current_volume_ma5 is not None
            and
            current_volume_ma5 > 0
        ):

            volume_ratio = (
                current_volume /
                current_volume_ma5
            )

        else:

            volume_ratio = None

        # ----------------------------------------------------
        # 漲跌
        # ----------------------------------------------------

        if (
            current_price is not None
            and
            previous_price is not None
        ):

            change = (
                current_price -
                previous_price
            )

            if previous_price != 0:

                change_percent = (
                    change /
                    previous_price
                    *
                    100
                )

            else:

                change_percent = 0

        else:

            change = None
            change_percent = None

        # ----------------------------------------------------
        # 前一日
        # ----------------------------------------------------

        previous_k = (
            safe_float(
                k.iloc[-2]
            )
            if len(k) >= 2
            else None
        )

        previous_d = (
            safe_float(
                d.iloc[-2]
            )
            if len(d) >= 2
            else None
        )

        previous_ma20 = (
            safe_float(
                ma20.iloc[-2]
            )
            if len(ma20) >= 2
            else None
        )

        # ----------------------------------------------------
        # MACD 黃金交叉
        # ----------------------------------------------------

        macd_golden_cross = False

        if (
            current_macd is not None
            and
            current_macd_signal is not None
            and
            len(macd) >= 2
            and
            len(macd_signal) >= 2
        ):

            previous_macd = safe_float(
                macd.iloc[-2]
            )

            previous_signal = safe_float(
                macd_signal.iloc[-2]
            )

            if (
                previous_macd is not None
                and
                previous_signal is not None
            ):

                macd_golden_cross = (
                    previous_macd <=
                    previous_signal
                    and
                    current_macd >
                    current_macd_signal
                )

        # ----------------------------------------------------
        # KD 黃金交叉
        # ----------------------------------------------------

        kd_golden_cross = False

        if (
            current_k is not None
            and
            current_d is not None
            and
            previous_k is not None
            and
            previous_d is not None
        ):

            kd_golden_cross = (
                previous_k <=
                previous_d
                and
                current_k >
                current_d
            )

        # ----------------------------------------------------
        # 條件
        # ----------------------------------------------------

        rsi_above_50 = (
            current_rsi is not None
            and
            current_rsi > 50
        )

        volume_over_1_5x = (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        )

        above_ma20 = (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_price >
            current_ma20
        )

        ma20_up = (
            current_ma20 is not None
            and
            previous_ma20 is not None
            and
            current_ma20 >
            previous_ma20
        )

        macd_positive = (
            current_macd_hist is not None
            and
            current_macd_hist > 0
        )

        # ----------------------------------------------------
        # 核心訊號
        # ----------------------------------------------------

        short_term_core = all([
            macd_golden_cross,
            kd_golden_cross,
            rsi_above_50,
            volume_over_1_5x,
            above_ma20,
            ma20_up
        ])

        # ====================================================
        # AI SCORE
        # ====================================================

        score = 0

        if macd_golden_cross:

            score += 20

        elif macd_positive:

            score += 10

        if kd_golden_cross:

            score += 15

        elif (
            current_k is not None
            and
            current_d is not None
            and
            current_k >
            current_d
        ):

            score += 8

        if rsi_above_50:

            score += 15

        if (
            volume_ratio is not None
            and
            volume_ratio >= 1.5
        ):

            score += 15

        elif (
            volume_ratio is not None
            and
            volume_ratio >= 1
        ):

            score += 8

        if above_ma20:

            score += 15

        if ma20_up:

            score += 10

        if macd_positive:

            score += 10

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        # ----------------------------------------------------
        # 訊號
        # ----------------------------------------------------

        if short_term_core:

            signal = "強勢核心"

        elif score >= 70:

            signal = "強勢"

        elif score >= 50:

            signal = "偏多"

        elif score >= 30:

            signal = "觀察"

        else:

            signal = "弱勢"

        # ====================================================
        # DCA
        # ====================================================

        if current_ma20 is not None:

            buy_1 = current_ma20
            buy_2 = current_ma20 * 0.97
            buy_3 = current_ma20 * 0.94
            buy_4 = current_ma20 * 0.90

        else:

            buy_1 = None
            buy_2 = None
            buy_3 = None
            buy_4 = None

        if (
            current_price is not None
            and
            current_ma20 is not None
            and
            current_ma20 != 0
        ):

            distance = (
                current_price /
                current_ma20 -
                1
            ) * 100

            if distance <= -10:

                dca_action = "第四批區域"

            elif distance <= -6:

                dca_action = "第三批區域"

            elif distance <= -3:

                dca_action = "第二批區域"

            elif distance <= 3:

                dca_action = "第一批區域"

            elif distance <= 8:

                dca_action = "等待回測"

            else:

                dca_action = "暫緩追價"

        else:

            dca_action = "資料不足"

        # ====================================================
        # JSON
        # ====================================================

        stock = {

            "id":
                str(code),

            "name":
                str(name),

            "symbol":
                str(code),

            "type":
                stock_type,

            "market":
                market,

            "yahoo_symbol":
                yahoo_symbol(
                    code,
                    market
                ),

            "price": {

                "close":
                    round_value(
                        current_price,
                        2
                    ),

                "previous_close":
                    round_value(
                        previous_price,
                        2
                    ),

                "change":
                    round_value(
                        change,
                        2
                    ),

                "change_percent":
                    round_value(
                        change_percent,
                        2
                    )

            },

            "technical": {

                "rsi":
                    round_value(
                        current_rsi,
                        2
                    ),

                "k":
                    round_value(
                        current_k,
                        2
                    ),

                "d":
                    round_value(
                        current_d,
                        2
                    ),

                "macd":
                    round_value(
                        current_macd,
                        4
                    ),

                "macd_signal":
                    round_value(
                        current_macd_signal,
                        4
                    ),

                "macd_hist":
                    round_value(
                        current_macd_hist,
                        4
                    ),

                "ma5":
                    round_value(
                        current_ma5,
                        2
                    ),

                "ma20":
                    round_value(
                        current_ma20,
                        2
                    ),

                "ma60":
                    round_value(
                        current_ma60,
                        2
                    ),

                "volume":
                    round_value(
                        current_volume,
                        0
                    ),

                "volume_ma5":
                    round_value(
                        current_volume_ma5,
                        0
                    ),

                "volume_ratio":
                    round_value(
                        volume_ratio,
                        2
                    )

            },

            "conditions": {

                "macd_golden_cross":
                    bool(
                        macd_golden_cross
                    ),

                "kd_golden_cross":
                    bool(
                        kd_golden_cross
                    ),

                "rsi_above_50":
                    bool(
                        rsi_above_50
                    ),

                "volume_over_1_5x":
                    bool(
                        volume_over_1_5x
                    ),

                "above_ma20":
                    bool(
                        above_ma20
                    ),

                "ma20_up":
                    bool(
                        ma20_up
                    ),

                "short_term_core":
                    bool(
                        short_term_core
                    )

            },

            "short_term": {

                "score":
                    int(score),

                "signal":
                    signal

            },

            "dca": {

                "buy_1":
                    round_value(
                        buy_1,
                        2
                    ),

                "buy_2":
                    round_value(
                        buy_2,
                        2
                    ),

                "buy_3":
                    round_value(
                        buy_3,
                        2
                    ),

                "buy_4":
                    round_value(
                        buy_4,
                        2
                    ),

                "action":
                    dca_action

            }

        }

        return stock

    except Exception as error:

        print(
            f"{code}: "
            f"分析失敗：{error}"
        )

        traceback.print_exc()

        return None


# ============================================================
# 驗證股票
# ============================================================

def validate_stock(
    stock
):

    if not stock:
        return False

    stock_id = stock.get(
        "id"
    )

    if not stock_id:
        return False

    price = stock.get(
        "price",
        {}
    )

    close = safe_float(
        price.get(
            "close"
        )
    )

    if close is None:
        return False

    technical = stock.get(
        "technical",
        {}
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    rsi = technical.get(
        "rsi"
    )

    if rsi is not None:

        rsi = safe_float(
            rsi
        )

        if (
            rsi is None
            or
            rsi < 0
            or
            rsi > 100
        ):

            print(
                f"{stock_id}: "
                f"RSI 異常 {rsi}"
            )

            return False

    # --------------------------------------------------------
    # KD
    # --------------------------------------------------------

    for field in [
        "k",
        "d"
    ]:

        value = technical.get(
            field
        )

        if value is not None:

            value = safe_float(
                value
            )

            if (
                value is None
                or
                value < 0
                or
                value > 100
            ):

                print(
                    f"{stock_id}: "
                    f"{field} 異常 {value}"
                )

                return False

    return True


# ============================================================
# Rankings
# ============================================================

def build_rankings(
    stocks
):

    ranking_data = sorted(
        stocks,
        key=lambda stock: (
            safe_float(
                stock.get(
                    "short_term",
                    {}
                ).get(
                    "score"
                ),
                0
            ),
            safe_float(
                stock.get(
                    "technical",
                    {}
                ).get(
                    "volume_ratio"
                ),
                0
            )
        ),
        reverse=True
    )

    short_term = [
        str(
            stock["id"]
        )
        for stock in ranking_data
    ]

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------

    core_stocks = [

        stock
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )

    ]

    core_stocks = sorted(
        core_stocks,
        key=lambda stock:
            stock.get(
                "short_term",
                {}
            ).get(
                "score",
                0
            ),
        reverse=True
    )

    # --------------------------------------------------------
    # DCA
    # --------------------------------------------------------

    dca_stocks = [

        stock
        for stock in stocks

        if stock.get(
            "technical",
            {}
        ).get(
            "ma20"
        ) is not None

    ]

    def dca_score(
        stock
    ):

        score = safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        )

        price = safe_float(
            stock.get(
                "price",
                {}
            ).get(
                "close"
            ),
            0
        )

        ma20 = safe_float(
            stock.get(
                "technical",
                {}
            ).get(
                "ma20"
            ),
            0
        )

        if (
            price
            and
            ma20
        ):

            distance = abs(
                price /
                ma20 -
                1
            )

        else:

            distance = 999

        return (
            score,
            -distance
        )

    dca_stocks.sort(
        key=dca_score,
        reverse=True
    )

    return {

        "short_term":
            short_term,

        "core":
            [
                str(
                    stock["id"]
                )
                for stock in core_stocks
            ],

        "dca":
            [
                str(
                    stock["id"]
                )
                for stock in dca_stocks
            ]

    }


# ============================================================
# Statistics
# ============================================================

def build_statistics(
    stocks,
    market_list
):

    total_market = len(
        market_list
    )

    total_stocks = len(
        stocks
    )

    listed = sum(

        1
        for stock in stocks

        if stock.get(
            "market"
        ) == "上市"

        and
        stock.get(
            "type"
        ) == "STOCK"

    )

    otc = sum(

        1
        for stock in stocks

        if stock.get(
            "market"
        ) == "上櫃"

        and
        stock.get(
            "type"
        ) == "STOCK"

    )

    etf = sum(

        1
        for stock in stocks

        if stock.get(
            "type"
        ) == "ETF"

    )

    core_stocks = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "short_term_core",
            False
        )

    )

    ai_70 = sum(

        1
        for stock in stocks

        if safe_float(
            stock.get(
                "short_term",
                {}
            ).get(
                "score"
            ),
            0
        ) >= 70

    )

    macd_golden = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "macd_golden_cross",
            False
        )

    )

    rsi_above_50 = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "rsi_above_50",
            False
        )

    )

    kd_golden = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "kd_golden_cross",
            False
        )

    )

    volume_over_1_5x = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "volume_over_1_5x",
            False
        )

    )

    above_ma20 = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "above_ma20",
            False
        )

    )

    ma20_up = sum(

        1
        for stock in stocks

        if stock.get(
            "conditions",
            {}
        ).get(
            "ma20_up",
            False
        )

    )

    return {

        "total_market":
            total_market,

        "total_stocks":
            total_stocks,

        "listed_stocks":
            listed,

        "otc_stocks":
            otc,

        "etf":
            etf,

        "core_stocks":
            core_stocks,

        "ai_70":
            ai_70,

        "macd_golden":
            macd_golden,

        "rsi_above_50":
            rsi_above_50,

        "kd_golden":
            kd_golden,

        "volume_over_1_5x":
            volume_over_1_5x,

        "above_ma20":
            above_ma20,

        "ma20_up":
            ma20_up

    }


# ============================================================
# 儲存 JSON
# ============================================================

def save_json(
    data
):

    try:

        temp_file = (
            OUTPUT_FILE +
            ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            OUTPUT_FILE
        )

        print(
            ""
        )

        print(
            f"資料已寫入："
            f"{OUTPUT_FILE}"
        )

        return True

    except Exception as error:

        print(
            f"JSON 寫入失敗："
            f"{error}"
        )

        return False


# ============================================================
# 主程式
# ============================================================

def main():

    start_time = time.time()

    now = datetime.now(
        TW_TZ
    )

    print(
        "================================================"
    )

    print(
        f"台股 AI 選股系統 "
        f"fetch_data.py {VERSION}"
    )

    print(
        f"開始時間："
        f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "================================================"
    )

    # ========================================================
    # 1. 建立市場清單
    # ========================================================

    market_list = build_market_list()

    if not market_list:

        print(
            "錯誤："
            "無法取得任何市場清單。"
        )

        sys.exit(1)

    # ========================================================
    # 2. 開始分析
    # ========================================================

    stocks = []

    failed = []

    listed_success = 0
    otc_success = 0
    etf_success = 0

    # ========================================================
    # 3. 逐檔抓取
    # ========================================================

    total = len(
        market_list
    )

    for index, item in enumerate(
        market_list,
        start=1
    ):

        code = item["id"]
        name = item["name"]
        market = item["market"]
        stock_type = item["type"]

        print(
            f"[{index}/{total}] "
            f"{code} "
            f"{name} "
            f"({market}/{stock_type})"
        )

        df = download_stock(
            code,
            market
        )

        if df is None:

            failed.append(
                {
                    "id": code,
                    "name": name,
                    "market": market,
                    "type": stock_type
                }
            )

            continue

        stock = analyze_stock(
            item,
            df
        )

        if stock is None:

            failed.append(
                {
                    "id": code,
                    "name": name,
                    "market": market,
                    "type": stock_type
                }
            )

            continue

        if not validate_stock(
            stock
        ):

            failed.append(
                {
                    "id": code,
                    "name": name,
                    "market": market,
                    "type": stock_type
                }
            )

            continue

        stocks.append(
            stock
        )

        if (
            market == "上市"
            and
            stock_type == "STOCK"
        ):

            listed_success += 1

        elif (
            market == "上櫃"
            and
            stock_type == "STOCK"
        ):

            otc_success += 1

        elif stock_type == "ETF":

            etf_success += 1

        # ----------------------------------------------------
        # 控制請求速度
        # ----------------------------------------------------

        time.sleep(
            REQUEST_DELAY
        )

    # ========================================================
    # 4. 防止空資料
    # ========================================================

    if len(stocks) == 0:

        print(
            ""
        )

        print(
            "錯誤："
            "沒有任何標的成功取得資料。"
        )

        sys.exit(1)

    # ========================================================
    # 5. 排名
    # ========================================================

    rankings = build_rankings(
        stocks
    )

    # ========================================================
    # 6. 統計
    # ========================================================

    statistics = build_statistics(
        stocks,
        market_list
    )

    # ========================================================
    # 7. 最終 JSON
    # ========================================================

    output = {

        "version":
            VERSION,

        "updated_at":
            now.strftime(
                "%Y-%m-%d %H:%M:%S"
            ),

        "market":
            "TW",

        "source":
            "TWSE + TPEx + Yahoo Finance",

        "market_list_count":
            len(market_list),

        "successful_count":
            len(stocks),

        "failed_count":
            len(failed),

        "market_statistics": {

            "listed":
                sum(
                    1
                    for x in market_list
                    if (
                        x["market"] == "上市"
                        and
                        x["type"] == "STOCK"
                    )
                ),

            "otc":
                sum(
                    1
                    for x in market_list
                    if (
                        x["market"] == "上櫃"
                        and
                        x["type"] == "STOCK"
                    )
                ),

            "etf":
                sum(
                    1
                    for x in market_list
                    if x["type"] == "ETF"
                )

        },

        "stocks":
            stocks,

        "rankings":
            rankings,

        "statistics":
            statistics,

        "failed":
            failed

    }

    # ========================================================
    # 8. 儲存
    # ========================================================

    success = save_json(
        output
    )

    if not success:

        sys.exit(1)

    # ========================================================
    # 9. 完成統計
    # ========================================================

    elapsed = (
        time.time() -
        start_time
    )

    market_count = len(
        market_list
    )

    success_count = len(
        stocks
    )

    failed_count = len(
        failed
    )

    market_listed = sum(

        1
        for x in market_list

        if (
            x["market"] == "上市"
            and
            x["type"] == "STOCK"
        )

    )

    market_otc = sum(

        1
        for x in market_list

        if (
            x["market"] == "上櫃"
            and
            x["type"] == "STOCK"
        )

    )

    market_etf = sum(

        1
        for x in market_list

        if x["type"] == "ETF"

    )

    print(
        ""
    )

    print(
        "================================================"
    )

    print(
        "V7.2 全市場掃描完成"
    )

    print(
        "================================================"
    )

    print(
        f"市場清單："
        f"{market_count} 檔"
    )

    print(
        f"成功分析："
        f"{success_count} 檔"
    )

    print(
        f"失敗："
        f"{failed_count} 檔"
    )

    print(
        ""
    )

    print(
        f"上市股票："
        f"{market_listed} 檔"
    )

    print(
        f"上櫃股票："
        f"{market_otc} 檔"
    )

    print(
        f"ETF："
        f"{market_etf} 檔"
    )

    print(
        ""
    )

    print(
        f"成功上市分析："
        f"{listed_success} 檔"
    )

    print(
        f"成功上櫃分析："
        f"{otc_success} 檔"
    )

    print(
        f"成功 ETF 分析："
        f"{etf_success} 檔"
    )

    print(
        ""
    )

    print(
        f"AI ≥ 70："
        f"{statistics['ai_70']} 檔"
    )

    print(
        f"核心訊號："
        f"{statistics['core_stocks']} 檔"
    )

    print(
        f"MACD 黃金交叉："
        f"{statistics['macd_golden']} 檔"
    )

    print(
        f"RSI > 50："
        f"{statistics['rsi_above_50']} 檔"
    )

    print(
        f"KD 黃金交叉："
        f"{statistics['kd_golden']} 檔"
    )

    print(
        f"成交量 > 1.5x："
        f"{statistics['volume_over_1_5x']} 檔"
    )

    print(
        f"站上 MA20："
        f"{statistics['above_ma20']} 檔"
    )

    print(
        f"MA20 向上："
        f"{statistics['ma20_up']} 檔"
    )

    print(
        ""
    )

    print(
        f"耗時："
        f"{elapsed:.2f} 秒"
    )

    print(
        "================================================"
    )

    # ========================================================
    # 10. 重要驗證
    # ========================================================

    print(
        ""
    )

    print(
        "V7.2 關鍵標的驗證："
    )

    target_codes = [
        "0050",
        "0056",
        "00713",
        "00878",
        "00919",
        "2330",
        "2337",
        "2426"
    ]

    stock_map = {
        str(
            stock["id"]
        ):
            stock
        for stock in stocks
    }

    for code in target_codes:

        stock = stock_map.get(
            code
        )

        if stock:

            print(
                f"✓ {code} "
                f"{stock['name']} "
                f"| "
                f"{stock['market']} "
                f"{stock['type']} "
                f"| "
                f"價格="
                f"{stock['price']['close']}"
            )

        else:

            # 確認是不是清單有但 Yahoo 失敗
            market_item = next(
                (
                    item
                    for item in market_list
                    if item["id"] == code
                ),
                None
            )

            if market_item:

                print(
                    f"△ {code} "
                    f"{market_item['name']} "
                    f"已在市場清單，"
                    f"但本次資料抓取失敗"
                )

            else:

                print(
                    f"✗ {code} "
                    f"完全不在市場清單"
                )

    print(
        ""
    )

    print(
        "V7.2 執行結束。"
    )


# ============================================================
# Entry Point
# ============================================================

if __name__ == "__main__":

    main()
