from datetime import datetime, timezone, timedelta

import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_autorefresh import st_autorefresh

from database import Database
import ai_engine as ai
import reflector as ref

st.set_page_config(
    page_title="AI株式投資シミュレーター",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: #141929;
    border: 1px solid #1e293b;
    border-radius: 10px;
    padding: 0.9rem 1.1rem;
}
.main-title {
    background: linear-gradient(90deg, #00d4ff, #7b68ee);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.9rem;
    font-weight: 800;
    margin-bottom: 0;
}
.market-open   { color: #26c6da; font-weight: bold; }
.market-closed { color: #546e7a; }
.sel-bar { height:3px; background:#00d4ff; border-radius:2px; margin-top:2px; }
div.wl-btn > div > button {
    background: transparent !important;
    border: none !important;
    border-radius: 4px !important;
    color: #e2e8f0 !important;
    font-weight: bold !important;
    font-size: 0.82em !important;
    padding: 2px 4px !important;
    width: 100% !important;
    text-align: left !important;
    cursor: pointer !important;
}
div.wl-btn > div > button:hover {
    background: rgba(0,212,255,0.08) !important;
    color: #00d4ff !important;
}
div.wl-btn-sel > div > button {
    background: rgba(0,212,255,0.12) !important;
    border: none !important;
    border-left: 3px solid #00d4ff !important;
    border-radius: 0 4px 4px 0 !important;
    color: #00d4ff !important;
    font-weight: bold !important;
    font-size: 0.82em !important;
    padding: 2px 4px !important;
    width: 100% !important;
    text-align: left !important;
}
</style>
""", unsafe_allow_html=True)

JST          = timezone(timedelta(hours=9))
PERIOD_MAP   = {
    "1日":   ("1d",  "5m"),
    "1週間": ("5d",  "1h"),
    "1ヶ月": ("1mo", "1d"),
    "3ヶ月": ("3mo", "1d"),
    "6ヶ月": ("6mo", "1d"),
    "1年":   ("1y",  "1d"),
}
DEFAULT_WL   = ["7203.T", "6758.T", "7974.T", "9984.T", "6861.T"]

db = Database()


# ── ユーティリティ ──────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(JST)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 <= t <= 11 * 60 + 30) or (12 * 60 + 30 <= t <= 15 * 60 + 30)


@st.cache_data(ttl=300)
def fetch_history(ticker, period="3mo", interval="1d"):
    try:
        return yf.Ticker(ticker).history(period=period, interval=interval)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=290)
def _batch_prices(tickers_tuple):
    """複数銘柄の株価を一括取得（個別APIコールを1回にまとめる）"""
    tickers = list(tickers_tuple)
    try:
        raw = yf.download(tickers, period="5d", interval="1d",
                          auto_adjust=True, progress=False)
        if raw.empty:
            return {}
        result = {}
        for t in tickers:
            try:
                closes = (raw["Close"][t] if isinstance(raw.columns, pd.MultiIndex)
                          else raw["Close"]).dropna()
                if len(closes) >= 2:
                    cur, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                    result[t] = {"current": cur, "change": cur - prev,
                                 "pct": (cur - prev) / prev * 100}
            except Exception:
                pass
        return result
    except Exception:
        return {}


@st.cache_data(ttl=600)
def scan_universe(rsi_w=1.0, macd_w=1.0, ma_w=1.0, gc_w=1.0):
    """全銘柄を一括ダウンロードしてスキャン（1回のAPIリクエストで完了）"""
    weights = {"rsi": rsi_w, "macd": macd_w, "ma": ma_w, "gc": gc_w}
    tickers = list(ai.UNIVERSE.keys())
    try:
        raw = yf.download(
            tickers, period="6mo", interval="1d",
            auto_adjust=True, progress=False
        )
        if raw.empty:
            return []
    except Exception:
        return []

    results = []
    for ticker in tickers:
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                hist = pd.DataFrame({
                    col: raw[col][ticker]
                    for col in ["Open", "High", "Low", "Close", "Volume"]
                    if col in raw.columns.get_level_values(0)
                }).dropna()
            else:
                hist = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(hist) < 30:
                continue
            results.append(ai.analyze(ticker, history=hist, weights=weights))
        except Exception:
            pass

    return sorted(results, key=lambda x: x["score"], reverse=True)


# ── チャート ────────────────────────────────────────────────

def make_sparkline(ticker):
    """ウォッチリスト用ミニローソク足チャート（クリック検出用不可視Scatterつき）"""
    h = fetch_history(ticker, "1mo")
    if h.empty:
        return None
    fig = go.Figure()
    fig.add_trace(go.Candlestick(
        x=h.index,
        open=h["Open"], high=h["High"],
        low=h["Low"],   close=h["Close"],
        increasing_line_color="#ef5350",
        decreasing_line_color="#26a69a",
        increasing_fillcolor="#ef5350",
        decreasing_fillcolor="#26a69a",
        name="",
        showlegend=False,
        line=dict(width=1),
    ))
    # ローソク足はクリックイベントを返さないため、不可視のScatterでクリックを検出
    mid = (h["High"] + h["Low"]) / 2
    fig.add_trace(go.Scatter(
        x=h.index, y=mid,
        mode="markers",
        marker=dict(size=18, opacity=0.01, color="rgba(0,0,0,0)"),
        showlegend=False, name="",
        hoverinfo="skip",
    ))
    fig.update_layout(
        height=90,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False, rangeslider_visible=False),
        yaxis=dict(visible=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="#0f1521",
        showlegend=False,
        clickmode="event+select",
        dragmode=False,
    )
    return fig


def make_main_chart(ticker, period, interval="1d"):
    h = fetch_history(ticker, period, interval)
    if h.empty:
        return None

    intraday = interval in ("1m", "2m", "5m", "15m", "30m", "60m", "1h")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        row_heights=[0.75, 0.25], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(
        x=h.index, open=h["Open"], high=h["High"],
        low=h["Low"], close=h["Close"],
        name="株価",
        increasing_line_color="#ef5350", decreasing_line_color="#26a69a",
    ), row=1, col=1)

    closes = h["Close"]
    if intraday:
        # 短期足：5・25期移動平均
        ma_defs = [(5, "#00d4ff", "solid"), (25, "#f57c00", "solid")]
    else:
        ma_defs = [(5, "#00d4ff", "solid"), (25, "#f57c00", "solid"), (75, "#c62828", "dot")]

    for p, c, d in ma_defs:
        if len(closes) >= p:
            label = f"{p}期MA" if intraday else f"{p}日MA"
            fig.add_trace(go.Scatter(
                x=h.index, y=closes.rolling(p).mean(),
                name=label, line=dict(color=c, width=1.3, dash=d),
            ), row=1, col=1)

    bar_c = ["#ef5350" if c >= o else "#26a69a"
             for c, o in zip(h["Close"], h["Open"])]
    fig.add_trace(go.Bar(x=h.index, y=h["Volume"], name="出来高",
                         marker_color=bar_c, opacity=0.55), row=2, col=1)

    # 日中足は時刻表示、日足は日付表示
    xaxis_fmt = "%H:%M" if interval == "5m" else ("%m/%d %H:%M" if interval == "1h" else "%Y/%m/%d")
    fig.update_layout(
        height=500, xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", y=1.02, x=0, bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=10, r=10, t=40, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0f1521",
        xaxis=dict(tickformat=xaxis_fmt),
    )
    fig.update_yaxes(tickformat=",.0f", row=1, col=1, gridcolor="#1e293b")
    fig.update_yaxes(row=2, col=1, gridcolor="#1e293b")
    fig.update_xaxes(gridcolor="#1e293b")
    return fig


def make_pie_chart(cash, holdings, prices):
    labels = ["現金"] + [h["name"] or h["ticker"] for h in holdings]
    values = [cash] + [prices.get(h["ticker"], h["avg_price"]) * h["shares"]
                       for h in holdings]
    palette = ["#2d4a6e","#00d4ff","#7b68ee","#ef5350","#26a69a",
               "#f57c00","#9c27b0","#ff7043","#26c6da","#66bb6a"]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.55,
        marker=dict(colors=palette[:len(labels)], line=dict(color="#0a0e1a", width=2)),
        textinfo="label+percent", textfont=dict(size=12),
    ))
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=20, b=10),
        paper_bgcolor="rgba(0,0,0,0)", showlegend=False,
        annotations=[dict(text="資産配分", x=0.5, y=0.5, font_size=14, showarrow=False)],
    )
    return fig


def make_trend_chart(history_rows, initial_cash):
    if not history_rows:
        return None
    df = pd.DataFrame(history_rows, columns=["date", "total_value"])
    df["date"] = pd.to_datetime(df["date"])
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["total_value"], name="ポートフォリオ",
        line=dict(color="#00d4ff", width=2.5),
        fill="tozeroy", fillcolor="rgba(0,212,255,0.05)",
    ))
    fig.add_hline(y=initial_cash, line_dash="dash", line_color="#4a5568",
                  annotation_text=f"初期資金 ¥{initial_cash:,.0f}",
                  annotation_font_color="#94a3b8")
    try:
        start = df["date"].iloc[0].strftime("%Y-%m-%d")
        nk = yf.Ticker("^N225").history(start=start, interval="1d")
        if not nk.empty:
            nk_norm = nk["Close"] / nk["Close"].iloc[0] * initial_cash
            fig.add_trace(go.Scatter(
                x=nk.index, y=nk_norm, name="日経225（正規化）",
                line=dict(color="#f57c00", width=1.5, dash="dot"),
            ))
    except Exception:
        pass
    fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=20, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#0f1521",
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        yaxis=dict(tickformat=",.0f", gridcolor="#1e293b"),
        xaxis=dict(gridcolor="#1e293b"),
    )
    return fig


# ── AI自律取引 ──────────────────────────────────────────────

def run_ai_autonomous(settings):
    fee_rate = settings["fee_rate"]
    sl_pct   = settings["sl_pct"]
    tp_pct   = settings["tp_pct"]
    ai_level = settings["ai_level"]
    thresholds = {
        "慎重": {"buy": 5,  "sell": -5,  "ratio": 0.15, "max_pos": 3},
        "普通": {"buy": 3,  "sell": -3,  "ratio": 0.25, "max_pos": 5},
        "積極": {"buy": 2,  "sell": -2,  "ratio": 0.40, "max_pos": 8},
    }
    th = thresholds.get(ai_level, thresholds["普通"])

    w = db.get_weights()
    all_results = {r["ticker"]: r for r in scan_universe(w["rsi"], w["macd"], w["ma"], w["gc"])}
    trade_log   = []

    # 1. 損切り・利確
    for h in db.get_all_holdings():
        r = all_results.get(h["ticker"])
        if not r:
            continue
        price = r["current_price"]
        unr   = (price - h["avg_price"]) / h["avg_price"] * 100
        if unr <= -sl_pct:
            ok, msg = db.execute_sell(h["ticker"], h["shares"], price, fee_rate)
            if ok:
                trade_log.append(("stop_loss", r["name"], msg, r))
        elif unr >= tp_pct:
            ok, msg = db.execute_sell(h["ticker"], h["shares"], price, fee_rate)
            if ok:
                trade_log.append(("take_profit", r["name"], msg, r))

    # 2. 売りシグナルの保有株を売却
    for h in db.get_all_holdings():
        r = all_results.get(h["ticker"])
        if r and r["score"] <= th["sell"]:
            ok, msg = db.execute_sell(h["ticker"], h["shares"], r["current_price"], fee_rate)
            if ok:
                trade_log.append(("sell", r["name"], msg, r))

    # 3. 買いシグナル上位を購入
    slots = th["max_pos"] - len(db.get_all_holdings())
    buys  = [r for r in all_results.values()
             if r["score"] >= th["buy"]
             and not (db.get_holding(r["ticker"]) and
                      db.get_holding(r["ticker"])["shares"] > 0)]
    buys.sort(key=lambda x: x["score"], reverse=True)
    for r in buys[:slots]:
        cash = db.get_cash()
        if cash < 10000:
            break
        shares = int(cash * th["ratio"] / r["current_price"])
        if shares > 0:
            ok, msg = db.execute_buy(r["ticker"], r["name"], shares,
                                      r["current_price"], fee_rate)
            if ok:
                comp = r.get("component_scores", {})
                db.log_signal(r["ticker"],
                              comp.get("rsi", 0), comp.get("macd", 0),
                              comp.get("ma", 0),  comp.get("gc", 0))
                trade_log.append(("buy", r["name"], msg, r))

    return trade_log, all_results  # all_results はスナップショット計算に使用


# ── メイン ──────────────────────────────────────────────────

def main():
    # セッション初期化
    if "watchlist"   not in st.session_state:
        st.session_state["watchlist"]   = list(DEFAULT_WL)
    if "main_ticker" not in st.session_state:
        st.session_state["main_ticker"] = DEFAULT_WL[0]

    settings     = db.get_settings()
    initial_cash = settings["initial_cash"]
    market_open  = is_market_open()

    # ── 自動リフレッシュ（常時5分ごと） ─────────────────────
    refresh_count = st_autorefresh(interval=5 * 60 * 1000, key="auto_refresh")

    # ── 終業後自動反省（平日15:30以降、1日1回） ────────────────
    _now = datetime.now(JST)
    if (_now.weekday() < 5 and
            (_now.hour > 15 or (_now.hour == 15 and _now.minute >= 30))):
        if db.get_last_reflection_date() != _now.strftime("%Y-%m-%d"):
            new_w = ref.daily_reflect(db)
            if new_w:
                scan_universe.clear()
                st.toast("🧠 本日の取引を反省・学習しました")

    # ── 自動リフレッシュのたびに価格キャッシュをクリア ─────────
    last_ref_cnt = st.session_state.get("last_ref_cnt", -1)
    if refresh_count > last_ref_cnt:
        st.session_state["last_ref_cnt"] = refresh_count
        _batch_prices.clear()
        fetch_history.clear()

    # ── AI自動取引（取引時間中・リフレッシュ時のみ） ─────────
    if market_open:
        last_count = st.session_state.get("last_trade_count", -1)
        if refresh_count > last_count:
            st.session_state["last_trade_count"] = refresh_count
            with st.spinner("🤖 AI自動取引実行中（全銘柄一括スキャン）..."):
                trade_log, all_res = run_ai_autonomous(db.get_settings())
            st.session_state["last_auto_log"]  = trade_log
            st.session_state["last_auto_time"] = datetime.now(JST).strftime("%H:%M:%S")
            db.save_snapshot_force(
                db.get_cash() + sum(
                    all_res.get(h["ticker"], {}).get("current_price", h["avg_price"])
                    * h["shares"] for h in db.get_all_holdings()
                )
            )

    # ══ サイドバー ════════════════════════════════════════════
    with st.sidebar:
        st.markdown("## ⚙️ 設定")

        st.markdown("#### 💴 仮想資金")
        new_cash = st.number_input(
            "初期資金（円）", min_value=10000, max_value=100000000,
            value=int(initial_cash), step=10000, label_visibility="collapsed",
        )

        st.markdown("#### 💸 手数料率")
        fee_opt   = {"0%（無料）": 0.0, "0.05%": 0.0005,
                     "0.1%": 0.001, "0.2%": 0.002, "0.5%": 0.005}
        fee_label = st.select_slider(
            "手数料率", options=list(fee_opt.keys()),
            value={v: k for k, v in fee_opt.items()}.get(settings["fee_rate"], "0.1%"),
            label_visibility="collapsed",
        )

        st.markdown("#### 🤖 AI積極度")
        ai_level = st.select_slider(
            "AI積極度", options=["慎重", "普通", "積極"],
            value=settings["ai_level"], label_visibility="collapsed",
        )
        st.caption({"慎重": "スコア5↑・資金15%・最大3銘柄",
                    "普通": "スコア3↑・資金25%・最大5銘柄",
                    "積極": "スコア2↑・資金40%・最大8銘柄"}[ai_level])

        st.markdown("#### 🛡️ 損切り / 利確")
        sl_col, tp_col = st.columns(2)
        sl_pct = sl_col.number_input("損切り %", 1, 50, int(settings["sl_pct"]), 1)
        tp_pct = tp_col.number_input("利確 %",  1, 100, int(settings["tp_pct"]), 1)

        if st.button("💾 設定を保存", use_container_width=True):
            db.save_settings(new_cash, fee_opt[fee_label], sl_pct, tp_pct, ai_level)
            _batch_prices.clear()
            st.success("保存しました")
            st.rerun()

        st.divider()

        # リセット（確認あり）
        if not st.session_state.get("confirm_reset"):
            if st.button("🗑️ 全データをリセット", use_container_width=True):
                st.session_state["confirm_reset"] = True
                st.rerun()
        else:
            st.warning("⚠️ 本当にリセットしますか？\n取引履歴・保有株・資産記録がすべて消えます。")
            rc1, rc2 = st.columns(2)
            if rc1.button("✅ リセットする", use_container_width=True, type="primary"):
                db.reset()
                st.session_state["confirm_reset"] = False
                st.session_state.pop("last_auto_log", None)
                st.session_state.pop("last_auto_time", None)
                st.rerun()
            if rc2.button("❌ キャンセル", use_container_width=True):
                st.session_state["confirm_reset"] = False
                st.rerun()

    # ══ ヘッダー ══════════════════════════════════════════════
    h_col, m_col = st.columns([3, 1])
    h_col.markdown('<p class="main-title">📈 AI株式投資シミュレーター</p>',
                   unsafe_allow_html=True)
    with m_col:
        now_jst = datetime.now(JST).strftime("%H:%M")
        if market_open:
            st.markdown(
                f"<div class='market-open'>🟢 取引中 {now_jst}<br>"
                f"<span style='font-size:0.8em;font-weight:normal'>5分ごと自動取引</span></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"<div class='market-closed'>🔴 クローズ {now_jst}<br>"
                f"<span style='font-size:0.8em'>平日 9:00〜11:30 / 12:30〜15:30</span></div>",
                unsafe_allow_html=True,
            )

    # ── 株価一括取得（ウォッチリスト＋保有銘柄＋メイン銘柄） ───
    cash         = db.get_cash()
    holdings_all = db.get_all_holdings()
    main_ticker  = st.session_state["main_ticker"]
    _all_tickers = tuple(sorted(set(
        st.session_state.get("watchlist", list(DEFAULT_WL))
        + [h["ticker"] for h in holdings_all]
        + [main_ticker]
    )))
    price_info = _batch_prices(_all_tickers)   # 1回のAPIリクエストで全取得

    # ── KPI ──────────────────────────────────────────────────
    prices = {}
    hval   = 0.0
    unrealized = 0.0
    for h in holdings_all:
        info = price_info.get(h["ticker"])
        cur  = info["current"] if info else h["avg_price"]
        prices[h["ticker"]] = cur
        hval       += cur * h["shares"]
        unrealized += (cur - h["avg_price"]) * h["shares"]
    total  = cash + hval
    pl     = total - initial_cash
    pl_pct = pl / initial_cash * 100

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("💰 総資産",    f"¥{total:,.0f}",  f"¥{pl:+,.0f}")
    k2.metric("💵 現金残高",  f"¥{cash:,.0f}")
    k3.metric("📊 株式評価額", f"¥{hval:,.0f}")
    unr_sign = "+" if unrealized >= 0 else ""
    k4.metric("💹 含み利益",   f"¥{unrealized:,.0f}",
              f"{unr_sign}{unrealized / max(hval,1)*100:.1f}%" if hval > 0 else None)
    k5.metric("📈 損益率",    f"{pl_pct:+.2f}%")

    # AI取引ログ表示
    if st.session_state.get("last_auto_log") is not None:
        log_time = st.session_state.get("last_auto_time", "-")
        log      = st.session_state["last_auto_log"]
        label    = f"🤖 AI取引ログ（{log_time}）— {'取引あり ' + str(len(log)) + '件' if log else '取引なし（様子見）'}"
        with st.expander(label):
            if not log:
                st.info("全銘柄が様子見シグナルのため取引なし")
            else:
                for kind, name, msg, _ in log:
                    if kind == "stop_loss":
                        st.error(f"🔴 損切り: {msg}")
                    elif kind == "take_profit":
                        st.success(f"💰 利確: {msg}")
                    elif kind == "buy":
                        st.success(f"✅ {msg}")
                    elif kind == "sell":
                        st.warning(f"📤 {msg}")

    st.divider()

    # ══ メインレイアウト ══════════════════════════════════════
    col_main, col_watch = st.columns([3, 2], gap="large")

    # ── 右列：ウォッチリスト（ローソク足カード） ────────────
    with col_watch:
        st.markdown("#### 📋 ウォッチリスト")

        with st.expander("＋ 銘柄を追加"):
            new_t = st.text_input("銘柄コード（例: 9432.T）",
                                   key="add_input", label_visibility="collapsed")
            if st.button("追加", key="add_btn"):
                t = new_t.strip().upper()
                if t and t not in st.session_state["watchlist"]:
                    st.session_state["watchlist"].append(t)
                    st.rerun()

        wl_items = list(st.session_state["watchlist"])
        for i in range(0, len(wl_items), 2):
            card_cols = st.columns(2)
            for j in range(2):
                if i + j >= len(wl_items):
                    break
                t      = wl_items[i + j]
                info   = price_info.get(t)
                name   = ai.STOCK_NAMES.get(t, t)
                is_sel = (t == st.session_state["main_ticker"])

                with card_cols[j]:
                    with st.container(border=True):
                        # 銘柄名クリックで切替（CSSでボタンを非表示風に）
                        btn_class = "wl-btn-sel" if is_sel else "wl-btn"
                        price_str = f"  ¥{info['current']:,.0f}" if info else ""
                        st.markdown(f'<div class="{btn_class}">', unsafe_allow_html=True)
                        if st.button(f"{name[:10]}{price_str}", key=f"sel_{t}",
                                     use_container_width=True):
                            st.session_state["main_ticker"] = t
                            st.rerun()
                        st.markdown("</div>", unsafe_allow_html=True)

                        # 株価変動
                        if info:
                            up    = info["pct"] >= 0
                            pc    = "#ef5350" if up else "#26a69a"
                            arrow = "▲" if up else "▼"
                            st.markdown(
                                f"<span style='color:{pc};font-size:0.78em'>"
                                f"{arrow}{info['pct']:+.1f}%</span>",
                                unsafe_allow_html=True,
                            )

                        # ローソク足（表示のみ）
                        spark = make_sparkline(t)
                        if spark:
                            st.plotly_chart(
                                spark, use_container_width=True,
                                config={"displayModeBar": False},
                                key=f"sp_{t}",
                            )

    # ── 左列：メインチャート ─────────────────────────────────
    with col_main:
        info  = price_info.get(main_ticker)
        name  = ai.STOCK_NAMES.get(main_ticker, main_ticker)

        hc1, hc2, hc3 = st.columns([3, 1, 1])
        hc1.markdown(
            f"### {name} "
            f"<span style='color:#4a5568;font-size:0.65em'>{main_ticker}</span>",
            unsafe_allow_html=True,
        )
        if info:
            hc2.metric("現在値", f"¥{info['current']:,.0f}")
            dc = "normal" if info["pct"] >= 0 else "inverse"
            hc3.metric("前日比", f"{info['pct']:+.2f}%",
                       f"¥{info['change']:+,.0f}", delta_color=dc)

        period_label = st.radio(
            "期間", list(PERIOD_MAP.keys()), horizontal=True, index=2,
            label_visibility="collapsed", key="period_radio",
        )
        period, interval = PERIOD_MAP[period_label]
        fig = make_main_chart(main_ticker, period, interval)
        if fig:
            st.plotly_chart(fig, use_container_width=True,
                            config={"displayModeBar": False}, key="main_chart")

        # ── タブ ────────────────────────────────────────────
        st.divider()
        t_port, t_hist, t_stat, t_trend, t_learn = st.tabs(
            ["💼 ポートフォリオ", "📜 取引履歴", "📊 統計", "📈 資産推移", "🧠 学習ログ"]
        )

        with t_port:
            holdings = db.get_all_holdings()
            if not holdings:
                st.info("まだ保有銘柄はありません（市場オープン時にAIが自動取引します）")
            else:
                pc1, pc2 = st.columns([1, 1])
                with pc1:
                    st.plotly_chart(make_pie_chart(cash, holdings, prices),
                                    use_container_width=True,
                                    config={"displayModeBar": False}, key="pie")
                with pc2:
                    rows = []
                    for h in holdings:
                        cur   = prices.get(h["ticker"], h["avg_price"])
                        val   = cur * h["shares"]
                        pl_h  = val - h["avg_price"] * h["shares"]
                        pct_h = pl_h / (h["avg_price"] * h["shares"]) * 100
                        icon  = "🔴" if pl_h >= 0 else "🔵"
                        rows.append({
                            "銘柄":     h["name"] or h["ticker"],
                            "株数":     h["shares"],
                            "取得単価": f"¥{h['avg_price']:,.0f}",
                            "現在値":   f"¥{cur:,.0f}",
                            "含み利益": f"{icon} ¥{pl_h:+,.0f} ({pct_h:+.1f}%)",
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        with t_hist:
            trades = db.get_trades()
            if not trades:
                st.info("まだ取引履歴はありません")
            else:
                df_t = pd.DataFrame(trades)
                st.dataframe(df_t, use_container_width=True, hide_index=True)
                csv = df_t.to_csv(index=False, encoding="utf-8-sig")
                st.download_button("📥 CSVでダウンロード", csv, "取引履歴.csv", "text/csv")

        with t_stat:
            stats = db.get_trade_stats()
            s1, s2, s3 = st.columns(3)
            s1.metric("総取引回数", stats["total_trades"])
            s2.metric("売却回数",   stats["sell_trades"])
            s3.metric("勝率",       f"{stats['win_rate']:.1f}%")
            s4, s5, s6 = st.columns(3)
            s4.metric("平均損益",   f"¥{stats['avg_pl']:+,.0f}")
            s5.metric("最高益",     f"¥{stats['best']:+,.0f}")
            s6.metric("最大損",     f"¥{stats['worst']:+,.0f}")
            st.metric("手数料合計", f"¥{stats['total_fee']:,.0f}")

        with t_trend:
            history_rows = db.get_portfolio_history()
            if not history_rows:
                st.info("まだ資産履歴がありません")
            else:
                fig_trend = make_trend_chart(history_rows, initial_cash)
                if fig_trend:
                    st.plotly_chart(fig_trend, use_container_width=True,
                                    config={"displayModeBar": False}, key="trend")

        with t_learn:
            st.markdown("#### 🧠 AI自己学習ログ")
            st.caption("毎日15:30に当日の取引結果を分析して指標の重みを自動調整します。")

            # 現在の重み
            weights = db.get_weights()
            st.markdown("**現在の指標重み**")
            wc1, wc2, wc3 = st.columns(3)
            for col, name, key in [(wc1, "RSI", "rsi"), (wc2, "MACD", "macd"), (wc3, "移動平均", "ma")]:
                w = weights[key]
                diff = round(w - 1.0, 3)
                dc = "normal" if diff >= 0 else "inverse"
                col.metric(
                    f"{name}重み",
                    f"{w:.2f}",
                    f"{diff:+.2f}（基準1.00）",
                    delta_color=dc,
                )

            st.caption("重み > 1.00：この指標を重視 ／ 重み < 1.00：この指標を軽視")
            st.divider()

            # 反省ログ
            st.markdown("**📝 反省ログ（直近14日）**")
            logs = db.get_reflection_log()
            if not logs:
                st.info("まだ学習データがありません。\n平日の取引後15:30以降に自動的に記録されます。")
            else:
                for row in logs:
                    date, summary, rsi_w, macd_w, ma_w, wins, losses = row
                    total = wins + losses
                    if total == 0:
                        icon = "💤"
                    elif wins / total >= 0.6:
                        icon = "✅"
                    elif wins / total >= 0.4:
                        icon = "➡️"
                    else:
                        icon = "⚠️"
                    st.markdown(
                        f"{icon} **{date}**　{summary}",
                        help=f"RSI重み:{rsi_w:.2f} MACD重み:{macd_w:.2f} MA重み:{ma_w:.2f}",
                    )


if __name__ == "__main__":
    main()
