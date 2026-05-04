import yfinance as yf
import pandas as pd
from itertools import product
from ai_engine import _calc_rsi, _calc_macd

TICKERS = [
    "7203.T", "6758.T", "9984.T", "7974.T", "6861.T",
    "8306.T", "8035.T", "6501.T", "4063.T", "8316.T",
]

PARAM_GRID = {
    "rsi_buy":    [25, 35],
    "rsi_sell":   [65, 75],
    "score_buy":  [2, 3],
    "score_sell": [-2, -3],
    "sl_pct":     [8, 12],
    "tp_pct":     [15, 25],
    "ratio":      [0.20, 0.25],
    "max_pos":    [5],
}


def fetch_data(period="1y"):
    raw = yf.download(TICKERS, period=period, interval="1d",
                      auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"][TICKERS].ffill()
    else:
        closes = pd.DataFrame(raw["Close"]).ffill()
    return closes


def _precompute(closes):
    """全銘柄のテクニカル指標を事前計算（シミュレーションを高速化）"""
    result = {}
    for t in closes.columns:
        s = closes[t].dropna()
        if len(s) < 30:
            continue
        rsi = _calc_rsi(s)
        _, _, hist = _calc_macd(s)
        ma5  = s.rolling(5).mean()
        ma25 = s.rolling(25).mean()
        df = pd.DataFrame({
            "close":     s,
            "rsi":       rsi,
            "hist":      hist,
            "hist_prev": hist.shift(1),
            "ma5":       ma5,
            "ma25":      ma25,
        }).dropna()
        result[t] = df
    return result


def _calc_score(row, rsi_buy, rsi_sell):
    score = 0
    rsi = row["rsi"]
    if rsi < rsi_buy - 10:    score += 3
    elif rsi < rsi_buy:        score += 2
    elif rsi > rsi_sell + 10:  score -= 3
    elif rsi > rsi_sell:       score -= 2

    h, hp = row["hist"], row["hist_prev"]
    if h > 0 and hp <= 0:    score += 2
    elif h < 0 and hp >= 0:  score -= 2
    elif h > 0:               score += 1
    else:                     score -= 1

    c, m5, m25 = row["close"], row["ma5"], row["ma25"]
    if c > m5 > m25:    score += 2
    elif c > m5:         score += 1
    elif c < m5 < m25:  score -= 2
    elif c < m5:         score -= 1

    return score


def simulate(pre, params, initial_cash=100_000):
    score_buy  = params["score_buy"]
    score_sell = params["score_sell"]
    sl_pct     = params["sl_pct"]
    tp_pct     = params["tp_pct"]
    ratio      = params["ratio"]
    max_pos    = params["max_pos"]
    rsi_buy    = params["rsi_buy"]
    rsi_sell   = params["rsi_sell"]

    cash        = float(initial_cash)
    holdings    = {}   # ticker -> {shares, avg_price}
    equity      = []
    last_prices = {}

    all_dates = sorted(set.union(*(set(df.index) for df in pre.values())))

    for date in all_dates:
        # 当日の指標・価格を取得
        day_data = {}
        for t, df in pre.items():
            if date in df.index:
                row = df.loc[date]
                day_data[t] = row
                last_prices[t] = float(row["close"])

        # 損切り・利確
        for t in list(holdings.keys()):
            p = last_prices.get(t)
            if not p:
                continue
            unr = (p - holdings[t]["avg_price"]) / holdings[t]["avg_price"] * 100
            if unr <= -sl_pct or unr >= tp_pct:
                cash += holdings[t]["shares"] * p * 0.999
                del holdings[t]

        # 売りシグナル
        for t in list(holdings.keys()):
            row = day_data.get(t)
            if row is None:
                continue
            if _calc_score(row, rsi_buy, rsi_sell) <= score_sell:
                p = last_prices[t]
                cash += holdings[t]["shares"] * p * 0.999
                del holdings[t]

        # 買いシグナル（スコア降順）
        buy_cands = sorted(
            [(t, _calc_score(row, rsi_buy, rsi_sell))
             for t, row in day_data.items()
             if t not in holdings],
            key=lambda x: -x[1],
        )
        for t, sc in buy_cands:
            if sc < score_buy or len(holdings) >= max_pos:
                break
            p = last_prices.get(t)
            if not p:
                continue
            shares = int(cash * ratio / p)
            cost   = shares * p * 1.001
            if shares > 0 and cost <= cash:
                cash -= cost
                holdings[t] = {"shares": shares, "avg_price": p}

        # 資産評価
        hval = sum(
            holdings[t]["shares"] * last_prices.get(t, holdings[t]["avg_price"])
            for t in holdings
        )
        equity.append({"date": date, "value": cash + hval})

    # 残保有を清算
    for t, h in holdings.items():
        cash += h["shares"] * last_prices.get(t, h["avg_price"]) * 0.999

    df_eq = pd.DataFrame(equity).set_index("date")
    peak  = df_eq["value"].cummax()
    mdd   = ((peak - df_eq["value"]) / peak * 100).max()

    return {
        "return_pct":   (cash - initial_cash) / initial_cash * 100,
        "final":        cash,
        "max_drawdown": float(mdd),
        "equity":       df_eq,
    }


def optimize(initial_cash=100_000, period="1y", cb=None):
    """グリッドサーチで最適パラメータを探索して返す"""
    closes = fetch_data(period)
    pre    = _precompute(closes)

    keys   = list(PARAM_GRID.keys())
    combos = list(product(*PARAM_GRID.values()))
    total  = len(combos)

    best_params = None
    best_ret    = -9999
    best_equity = None
    rows        = []

    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        res    = simulate(pre, params, initial_cash)
        rows.append({
            **params,
            "リターン%":   round(res["return_pct"], 2),
            "最大ドローダウン%": round(res["max_drawdown"], 2),
            "最終資産":    int(res["final"]),
        })
        if res["return_pct"] > best_ret:
            best_ret    = res["return_pct"]
            best_params = params
            best_equity = res["equity"]

        if cb:
            cb((i + 1) / total)

    df_results = pd.DataFrame(rows).sort_values("リターン%", ascending=False)
    return best_params, best_equity, df_results
