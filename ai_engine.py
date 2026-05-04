import yfinance as yf
import pandas as pd


STOCK_NAMES = {
    # 大型株・指数採用銘柄
    "7203.T": "トヨタ自動車",
    "6758.T": "ソニーグループ",
    "9984.T": "ソフトバンクG",
    "7974.T": "任天堂",
    "6861.T": "キーエンス",
    "8306.T": "三菱UFJ",
    "9432.T": "NTT",
    "9433.T": "KDDI",
    "4755.T": "楽天グループ",
    "9983.T": "ファストリ",
    "6501.T": "日立製作所",
    "6902.T": "デンソー",
    "8035.T": "東京エレクトロン",
    "4063.T": "信越化学",
    "2914.T": "JT",
    "7751.T": "キヤノン",
    "4519.T": "中外製薬",
    "8058.T": "三菱商事",
    "6954.T": "ファナック",
    "9022.T": "JR東海",
    "4502.T": "武田薬品",
    "8316.T": "三井住友FG",
    "6367.T": "ダイキン",
    "2802.T": "味の素",
    "3382.T": "セブン&アイ",
    # 追加銘柄
    "7267.T": "ホンダ",
    "7201.T": "日産自動車",
    "6752.T": "パナソニック",
    "6702.T": "富士通",
    "8001.T": "伊藤忠商事",
    "8031.T": "三井物産",
    "4911.T": "資生堂",
    "9101.T": "日本郵船",
    "9104.T": "商船三井",
    "8802.T": "三菱地所",
    "4568.T": "第一三共",
    "6326.T": "クボタ",
    "2503.T": "キリンHD",
    "6506.T": "安川電機",
    "7270.T": "SUBARU",
    "6723.T": "ルネサス",
    "8411.T": "みずほFG",
    "4704.T": "トレンドマイクロ",
    "6645.T": "オムロン",
    "2413.T": "エムスリー",
}

# AIが自律的に取引対象を選ぶ候補銘柄プール
UNIVERSE = STOCK_NAMES


def get_stock_name(ticker):
    return STOCK_NAMES.get(ticker, ticker)


def _calc_rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(com=period - 1, adjust=True).mean()
    loss = -delta.clip(upper=0).ewm(com=period - 1, adjust=True).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def _calc_macd(prices, fast=12, slow=26, signal=9):
    ema_fast = prices.ewm(span=fast).mean()
    ema_slow = prices.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal).mean()
    histogram = macd - signal_line
    return macd, signal_line, histogram


def get_current_price(ticker):
    data = yf.Ticker(ticker).history(period="2d", interval="1d")
    if data.empty:
        raise ValueError(f"{ticker} の株価データを取得できませんでした")
    return float(data["Close"].iloc[-1])


def analyze(ticker, history=None, weights=None):
    if weights is None:
        weights = {"rsi": 1.0, "macd": 1.0, "ma": 1.0, "gc": 1.0}

    if history is None:
        history = yf.Ticker(ticker).history(period="6mo", interval="1d")

    if history.empty or len(history) < 30:
        raise ValueError(f"{ticker} のデータが不足しています（銘柄コードを確認してください）")

    closes = history["Close"]
    current_price = float(closes.iloc[-1])

    rsi_series = _calc_rsi(closes)
    current_rsi = float(rsi_series.iloc[-1])

    macd, signal_line, histogram = _calc_macd(closes)
    current_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2])

    ma5 = float(closes.rolling(5).mean().iloc[-1])
    ma25 = float(closes.rolling(25).mean().iloc[-1])
    ma75 = float(closes.rolling(75).mean().iloc[-1]) if len(closes) >= 75 else None

    reasons = []

    # RSI（生スコア）
    if current_rsi < 20:
        rsi_raw = 3
        reasons.append(f"📉 RSI {current_rsi:.1f}：極端な売られすぎ → 強い買いシグナル")
    elif current_rsi < 30:
        rsi_raw = 2
        reasons.append(f"📉 RSI {current_rsi:.1f}：売られすぎ → 買いシグナル")
    elif current_rsi < 40:
        rsi_raw = 1
        reasons.append(f"📉 RSI {current_rsi:.1f}：やや売られ気味")
    elif current_rsi > 80:
        rsi_raw = -3
        reasons.append(f"📈 RSI {current_rsi:.1f}：極端な買われすぎ → 強い売りシグナル")
    elif current_rsi > 70:
        rsi_raw = -2
        reasons.append(f"📈 RSI {current_rsi:.1f}：買われすぎ → 売りシグナル")
    elif current_rsi > 60:
        rsi_raw = -1
        reasons.append(f"📈 RSI {current_rsi:.1f}：やや買われ気味")
    else:
        rsi_raw = 0
        reasons.append(f"➡️ RSI {current_rsi:.1f}：中立ゾーン")

    # MACD（生スコア）
    if current_hist > 0 and prev_hist <= 0:
        macd_raw = 2
        reasons.append("✅ MACD がシグナル線を上抜け → 買いシグナル")
    elif current_hist < 0 and prev_hist >= 0:
        macd_raw = -2
        reasons.append("❌ MACD がシグナル線を下抜け → 売りシグナル")
    elif current_hist > 0:
        macd_raw = 1
        reasons.append("✅ MACD：上昇トレンド継続中")
    else:
        macd_raw = -1
        reasons.append("❌ MACD：下降トレンド継続中")

    # 移動平均（生スコア）
    if current_price > ma5 > ma25:
        ma_raw = 2
        reasons.append("✅ 株価 > 5日線 > 25日線：強い上昇トレンド")
    elif current_price > ma5:
        ma_raw = 1
        reasons.append("✅ 株価が5日移動平均を上回っています")
    elif current_price < ma5 < ma25:
        ma_raw = -2
        reasons.append("❌ 株価 < 5日線 < 25日線：強い下降トレンド")
    elif current_price < ma5:
        ma_raw = -1
        reasons.append("❌ 株価が5日移動平均を下回っています")
    else:
        ma_raw = 0

    # ゴールデン/デッドクロス（生スコア）
    gc_raw = 0
    if ma75 is not None:
        ma25_series = closes.rolling(25).mean()
        ma75_series = closes.rolling(75).mean()
        if (ma25_series.iloc[-1] > ma75_series.iloc[-1] and
                ma25_series.iloc[-2] <= ma75_series.iloc[-2]):
            gc_raw = 3
            reasons.append("🌟 ゴールデンクロス発生（25日線が75日線を上抜け）→ 強い買いシグナル")
        elif (ma25_series.iloc[-1] < ma75_series.iloc[-1] and
              ma25_series.iloc[-2] >= ma75_series.iloc[-2]):
            gc_raw = -3
            reasons.append("💀 デッドクロス発生（25日線が75日線を下抜け）→ 強い売りシグナル")

    # 重みを適用してトータルスコアを計算
    score = round(
        rsi_raw  * weights.get("rsi",  1.0) +
        macd_raw * weights.get("macd", 1.0) +
        ma_raw   * weights.get("ma",   1.0) +
        gc_raw   * weights.get("gc",   1.0)
    )

    if score >= 5:
        signal = "STRONG_BUY"
    elif score >= 2:
        signal = "BUY"
    elif score <= -5:
        signal = "STRONG_SELL"
    elif score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

    return {
        "ticker":        ticker,
        "name":          get_stock_name(ticker),
        "signal":        signal,
        "score":         score,
        "current_price": current_price,
        "rsi":           current_rsi,
        "ma5":           ma5,
        "ma25":          ma25,
        "ma75":          ma75,
        "reasons":       reasons,
        "history":       history,
        "component_scores": {
            "rsi": rsi_raw, "macd": macd_raw, "ma": ma_raw, "gc": gc_raw
        },
    }
