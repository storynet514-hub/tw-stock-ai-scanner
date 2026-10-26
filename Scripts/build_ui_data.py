#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""台股 AI UI 資料建置器 UI-DATA-3.0。

資料責任：Universe=商品身分，Analysis=技術訊號，Chip=法人籌碼，
Market=大盤；無來源的價格、週均線、停損與目標一律為 null。
保留原有 index.html 所需 tabs/stocks/market 契約。
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'Data'
VERSION = 'UI-DATA-3.0'
STRENGTH_RANK = {'強勢': 3, '中性': 2, '弱勢': 1}
BUYABLE = {'偏多，可分批', '積極關注', '續抱觀察'}


def clean(v: Any) -> str:
    return '' if v is None else re.sub(r'\s+', ' ', str(v).replace('\ufeff', '')).strip()


def symbol(v: Any) -> str:
    s = clean(v).upper()
    for suffix in ('.TWSE', '.TPEX', '.TWO', '.TSE', '.OTC', '.TW'):
        if s.endswith(suffix):
            return s[:-len(suffix)]
    return s


def num(v: Any):
    try:
        if v is None or isinstance(v, bool) or str(v).strip() in ('', '--', '—', 'N/A'):
            return None
        n = float(str(v).replace(',', '').replace('%', ''))
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def pick_num(*values):
    for v in values:
        n = num(v)
        if n is not None:
            return n
    return None


def obj(v):
    return v if isinstance(v, dict) else {}


def read(path: Path):
    if not path.is_file():
        raise RuntimeError(f'缺少必要檔案：{path}')
    with path.open(encoding='utf-8-sig') as f:
        return json.load(f)


def stocks_from(path: Path):
    root = read(path)
    if not isinstance(root, dict) or not isinstance(root.get('stocks'), dict):
        raise RuntimeError(f'{path.name} 缺少 stocks object')
    result = {}
    for k, v in root['stocks'].items():
        if not isinstance(v, dict):
            continue
        code = symbol(k)
        if not code or code in result:
            raise RuntimeError(f'{path.name} 無效或重複代號：{k}')
        result[code] = v
    if not result:
        raise RuntimeError(f'{path.name} 無有效商品')
    return root, result


def atomic_write(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def kind(u: dict) -> str:
    raw = clean(u.get('type') or u.get('official_type') or u.get('security_type')).upper()
    if raw in ('STOCK', 'STOCKS'):
        return 'stock'
    if raw != 'ETF':
        raise RuntimeError(f"{u.get('symbol', '')} Universe type 無效：{raw}")
    cat = clean(u.get('category') or u.get('asset_category') or u.get('instrument_category')).upper()
    return 'bond' if 'BOND' in cat or '債' in cat else 'etf'


def exchange(u: dict) -> str:
    raw = clean(u.get('market') or u.get('exchange') or u.get('market_type')).upper()
    if raw in ('TWSE', 'TSE'):
        return 'TWSE'
    if raw in ('TPEX', 'OTC'):
        return 'TPEX'
    raise RuntimeError(f'Universe market 無效：{raw}')


def display_name(code: str, u: dict) -> str:
    # 僅使用 Universe 官方 ISIN 商品主檔，不使用 Analysis 英文代號冒充名稱。
    for key in ('display_name_zh', 'name_zh', 'chinese_name', 'security_name_zh',
                'stock_name', '基金簡稱', '證券簡稱', '公司簡稱', 'display_name', 'name', 'security_name'):
        value = clean(u.get(key))
        if value and value != code and not re.fullmatch(r'\d+', value):
            return value
    raise RuntimeError(f'{code} Universe 缺少官方商品名稱')


def strength(a: dict) -> str:
    s, m = obj(a.get('short_term')), obj(a.get('metrics'))
    for v in (s.get('strength'), s.get('technical_strength'), a.get('strength'), a.get('strength_label'), m.get('strength')):
        v = clean(v)
        if '強' in v or '多' in v or v.lower() in ('strong', 'bullish'):
            return '強勢'
        if '弱' in v or '空' in v or v.lower() in ('weak', 'bearish'):
            return '弱勢'
        if '中' in v or v.lower() in ('neutral', 'flat'):
            return '中性'
    return '中性'


def recommendation(a: dict) -> str:
    s, m = obj(a.get('short_term')), obj(a.get('metrics'))
    for v in (a.get('recommendation'), a.get('signal'), s.get('recommendation'), m.get('recommendation')):
        v = clean(v)
        if '立即進場' in v:
            return '積極關注'
        if '買進' in v or '偏多' in v:
            return '偏多，可分批'
        if '回測' in v:
            return '等回測'
        if '減碼' in v:
            return '考慮減碼'
        if '暫停' in v:
            return '暫停操作'
        if '續抱' in v or '觀察' in v:
            return '續抱觀察'
    return '續抱觀察'


def entry(a: dict) -> dict:
    e = obj(a.get('entry_timing'))
    current = obj(e.get('current'))
    zone = obj(current.get('pullback_zone'))
    lo, hi = num(zone.get('lower')), num(zone.get('upper'))
    if lo is not None and hi is not None and lo > hi:
        lo, hi = None, None
    return {
        'status': clean(current.get('status')) or '資料不足',
        'preferred': clean(current.get('preferred')) or '資料不足',
        'price_position': clean(current.get('price_position')) or '資料不足',
        'current_price': num(current.get('current_price')),
        'ma20': num(current.get('ma20')),
        'pullback_zone': {'lower': lo, 'upper': hi} if lo is not None and hi is not None else None,
        'message': clean(current.get('message')) or '無已驗證進場區間',
    }


def make_record(code: str, u: dict, a: dict, chip: dict, date: str) -> dict:
    m, s = obj(a.get('metrics')), obj(a.get('short_term'))
    t = entry(a)
    inst = obj(chip.get('institutional'))
    d5, d20 = obj(inst.get('5d')), obj(inst.get('20d'))
    zone = obj(t.get('pullback_zone'))
    lo, hi = num(zone.get('lower')), num(zone.get('upper'))
    # 未有正式已驗證交易計畫欄位，不得從價格乘百分比臆造停損/目標。
    verified = obj(a.get('verified_trade_plan'))
    stop = num(verified.get('stop_loss'))
    target = num(verified.get('target_price'))
    rr = None
    if None not in (lo, hi, stop, target):
        midpoint = (lo + hi) / 2
        if stop < midpoint < target:
            rr = round((target - midpoint) / (midpoint - stop), 2)
    plan_ok = all(v is not None for v in (lo, hi, stop, target, rr))
    return {
        'symbol': code,
        'name': display_name(code, u),
        'market': exchange(u),
        'instrument_type': kind(u),
        'industry': clean(u.get('industry') or u.get('industry_name')) or '未分類',
        'price': pick_num(m.get('price'), m.get('close'), m.get('latest_price'), m.get('last_price'), a.get('price'), a.get('close')),
        'change': pick_num(m.get('change'), m.get('price_change'), a.get('change')),
        'change_pct': pick_num(m.get('change_pct'), m.get('change_percent'), m.get('change1_pct'), a.get('change_pct')),
        'strength': strength(a),
        'recommendation': recommendation(a),
        'technical': {
            'ma20': pick_num(m.get('ma20'), m.get('MA20'), t.get('ma20')),
            'weekly_ma20': pick_num(m.get('weekly_ma20'), s.get('weekly_ma20'), a.get('weekly_ma20')),
            'rsi': pick_num(m.get('rsi'), s.get('rsi')),
            'kd_k': pick_num(m.get('kd_k'), m.get('k'), s.get('kd_k')),
            'kd_d': pick_num(m.get('kd_d'), m.get('d'), s.get('kd_d')),
            'macd': pick_num(m.get('macd'), s.get('macd')),
            'volume': pick_num(m.get('volume'), s.get('volume')),
        },
        'entry_timing': t,
        'trade_plan': {
            'entry_low': lo, 'entry_high': hi,
            'weekly_ma20': pick_num(m.get('weekly_ma20'), s.get('weekly_ma20'), a.get('weekly_ma20')),
            'stop_loss': stop, 'target_price': target, 'risk_reward': rr,
            'status': '待確認' if plan_ok else '資料不足',
        },
        'chip': {
            'date': clean(inst.get('date') or chip.get('latest_trade_date')) or None,
            'institutional_net_1d': num(inst.get('total_net')),
            'institutional_net_5d': num(d5.get('total_net')),
            'institutional_net_20d': num(d20.get('total_net')),
            'foreign_net_1d': num(inst.get('foreign_net')),
            'trust_net_1d': num(inst.get('trust_net')),
            'dealer_net_1d': num(inst.get('dealer_net')),
        },
        'backend': {'status': 'ok', 'latest_date': date},
        'holding': {'shares': None, 'average_cost': None, 'market_value': None, 'profit': None, 'return_pct': None},
    }


def candidates(root: dict) -> list[str]:
    raw = root.get('short_term_candidates') or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        code = symbol(item.get('symbol') or item.get('code') or item.get('ticker')) if isinstance(item, dict) else symbol(item)
        if code and code not in out:
            out.append(code)
    return out


def explicit(a: dict) -> bool:
    s = obj(a.get('short_term'))
    return any(a.get(k) is True or s.get(k) is True for k in ('qualified', 'is_candidate', 'candidate', 'short_term_qualified'))


def dashboard(public: dict, today: list[str], date: str, index: dict) -> dict:
    industries = defaultdict(lambda: {'net_buy': 0.0, 'valid_stocks': 0})
    for r in public.values():
        if r['instrument_type'] != 'stock':
            continue
        c = r['chip']
        if c['date'] != date or c['institutional_net_1d'] is None:
            continue
        group = industries[r['industry']]
        group['net_buy'] += c['institutional_net_1d']
        group['valid_stocks'] += 1
    inflow = sorted(({'industry': k, 'net_buy': round(v['net_buy'], 2), 'valid_stocks': v['valid_stocks']}
                     for k, v in industries.items() if v['net_buy'] > 0),
                    key=lambda v: v['net_buy'], reverse=True)[:10]
    plans = []
    for code in today:
        r = public[code]
        plans.append({'symbol': code, 'name': r['name'], 'industry': r['industry'],
                      'price': r['price'], 'change_pct': r['change_pct'],
                      'institutional_net_1d': r['chip']['institutional_net_1d'],
                      **r['trade_plan']})
    return {'latest_trading_date': date, 'market_index': index,
            'capital_inflow_industries': inflow, 'candidate_trade_plans': plans,
            'data_quality': {'valid_industries': len(inflow),
                             'complete_trade_plans': sum(p['status'] == '待確認' for p in plans)}}


def validate(payload: dict):
    if payload.get('schema_version') != VERSION or payload.get('status') != 'ok':
        raise RuntimeError('UI schema/status 無效')
    stocks = payload.get('stocks')
    if not isinstance(stocks, dict) or not stocks:
        raise RuntimeError('UI stocks 必須是非空 object')
    for code, r in stocks.items():
        if r.get('symbol') != code or not clean(r.get('name')):
            raise RuntimeError(f'{code} 名稱/代號無效')
        if r.get('instrument_type') not in ('stock', 'etf', 'bond'):
            raise RuntimeError(f'{code} 商品類型無效')
        if r.get('strength') not in STRENGTH_RANK:
            raise RuntimeError(f'{code} 強弱分類無效')
        if not isinstance(r.get('trade_plan'), dict) or not isinstance(r.get('chip'), dict):
            raise RuntimeError(f'{code} 交易計畫/籌碼結構無效')
    if '00400A' in stocks and stocks['00400A']['instrument_type'] != 'etf':
        raise RuntimeError('00400A 必須是 ETF')
    tabs = payload.get('tabs')
    required = {'today_picks', 'top10', 'etf', 'bond', 'watchlist'}
    if not isinstance(tabs, dict) or set(tabs) != required:
        raise RuntimeError('UI tabs 契約無效')
    if any(not isinstance(tabs[k], list) for k in required):
        raise RuntimeError('UI tabs 欄位不是 list')
    if len(tabs['top10']) > 10 or not set(tabs['top10']).issubset(set(tabs['today_picks'])):
        raise RuntimeError('Top10 不是 Today Picks 子集合')
    for tab, expected in (('today_picks', 'stock'), ('etf', 'etf'), ('bond', 'bond')):
        if tab in ('etf', 'bond') and len(tabs[tab]) > 10:
            raise RuntimeError(f'{tab} 超過 10 檔')
        for code in tabs[tab]:
            if code not in stocks or stocks[code]['instrument_type'] != expected:
                raise RuntimeError(f'{tab} 商品分類錯誤：{code}')
    board = payload.get('dashboard')
    if not isinstance(board, dict) or not isinstance(board.get('capital_inflow_industries'), list) or not isinstance(board.get('candidate_trade_plans'), list):
        raise RuntimeError('dashboard 契約無效')


def main():
    print('BUILD UI DATA - ' + VERSION, flush=True)
    _, universe = stocks_from(DATA / 'universe.json')
    analysis_root, analysis = stocks_from(DATA / 'analysis.json')
    _, chips = stocks_from(DATA / 'chip.json')
    market = read(DATA / 'market.json')
    if not isinstance(market, dict):
        raise RuntimeError('market.json 根節點不是 object')
    date = clean(market.get('latest_trading_date'))
    if not date:
        raise RuntimeError('market.json 缺少 latest_trading_date')
    public = {code: make_record(code, u, analysis[code], chips.get(code, {}), date)
              for code, u in universe.items() if code in analysis}
    if not public:
        raise RuntimeError('Universe 與 Analysis 無交集')
    missing = set(universe) - set(analysis)
    if missing:
        print(f'WARNING Universe 未匹配 Analysis：{len(missing)}', flush=True)
    today = [c for c in candidates(analysis_root) if c in public and public[c]['instrument_type'] == 'stock']
    if not today:
        today = [c for c in public if public[c]['instrument_type'] == 'stock' and explicit(analysis[c])]
    def rank(code):
        s = obj(analysis[code].get('short_term'))
        score = pick_num(s.get('core_score'), analysis[code].get('core_score'), analysis[code].get('score')) or 0
        return (STRENGTH_RANK.get(public[code]['strength'], 0), score, code)
    today = sorted(set(today), key=rank, reverse=True)
    etf = sorted((c for c, r in public.items() if r['instrument_type'] == 'etf' and (r['strength'] == '強勢' or r['recommendation'] in BUYABLE)), key=rank, reverse=True)[:10]
    bond = sorted((c for c, r in public.items() if r['instrument_type'] == 'bond' and (r['strength'] == '強勢' or r['recommendation'] in BUYABLE)), key=rank, reverse=True)[:10]
    index = obj(market.get('index'))
    sentiment = market.get('sentiment')
    payload = {
        'schema_version': VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(), 'status': 'ok',
        'market': {'name': '台股市場', 'timezone': 'Asia/Taipei', 'status': market.get('market_status'),
                   'latest_trading_date': date, 'index': index, 'sentiment': sentiment},
        'summary': {'today_picks_count': len(today), 'holdings': False, 'index': index, 'sentiment': sentiment},
        'dashboard': dashboard(public, today, date, index),
        'tabs': {'today_picks': today, 'top10': today[:10], 'etf': etf, 'bond': bond, 'watchlist': []},
        'stocks': public,
    }
    validate(payload)
    output = DATA / 'ui_data.json'
    atomic_write(output, payload)
    validate(read(output))
    print(f'UI DATA BUILD PASS: stocks={len(public)}, today={len(today)}, ETF={len(etf)}, bond={len(bond)}', flush=True)
    print('Atomic write / read-back validation PASS', flush=True)


if __name__ == '__main__':
    main()