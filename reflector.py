from datetime import datetime, timezone, timedelta

JST  = timezone(timedelta(hours=9))
STEP = 0.05   # 1回あたりの重み変化量
MIN_W = 0.5   # 重みの下限
MAX_W = 2.0   # 重みの上限


def daily_reflect(db):
    """
    市場クローズ後に当日の取引を振り返り、指標の重みを自動調整する。
    戻り値: 更新後の重み dict、または今日分を既に実行済みなら None
    """
    today = datetime.now(JST).strftime("%Y-%m-%d")

    if db.get_last_reflection_date() == today:
        return None

    weights = db.get_weights()
    sells   = db.get_todays_sells()   # [(ticker, profit_loss), ...]

    if not sells:
        db.save_reflection(
            today, "本日は売却なし（学習データなし）",
            weights["rsi"], weights["macd"], weights["ma"], 0, 0,
        )
        return weights

    rsi_right = rsi_wrong = 0
    macd_right = macd_wrong = 0
    ma_right   = ma_wrong   = 0
    wins = losses = 0

    for ticker, pl in sells:
        sig = db.get_buy_signal_for_ticker(ticker)
        if sig is None:
            continue
        rsi_sc, macd_sc, ma_sc, _ = sig
        profitable = pl > 0

        if profitable:
            wins += 1
            # 買いシグナルを出していた指標は「正解」
            if rsi_sc  > 0: rsi_right  += 1
            elif rsi_sc  < 0: rsi_wrong  += 1
            if macd_sc > 0: macd_right += 1
            elif macd_sc < 0: macd_wrong += 1
            if ma_sc   > 0: ma_right   += 1
            elif ma_sc   < 0: ma_wrong   += 1
        else:
            losses += 1
            # 買いシグナルを出していた指標は「外れ」
            if rsi_sc  > 0: rsi_wrong  += 1
            elif rsi_sc  < 0: rsi_right  += 1
            if macd_sc > 0: macd_wrong += 1
            elif macd_sc < 0: macd_right += 1
            if ma_sc   > 0: ma_wrong   += 1
            elif ma_sc   < 0: ma_right   += 1

    def adj(w, right, wrong):
        return max(MIN_W, min(MAX_W, round(w + (right - wrong) * STEP, 3)))

    new_rsi  = adj(weights["rsi"],  rsi_right,  rsi_wrong)
    new_macd = adj(weights["macd"], macd_right, macd_wrong)
    new_ma   = adj(weights["ma"],   ma_right,   ma_wrong)
    new_gc   = weights["gc"]   # GCはレア事象のため固定

    db.save_weights(new_rsi, new_macd, new_ma, new_gc)

    # 反省コメントを生成
    parts = [f"勝ち{wins}件 / 負け{losses}件"]
    for name, old, new in [
        ("RSI", weights["rsi"], new_rsi),
        ("MACD", weights["macd"], new_macd),
        ("MA", weights["ma"], new_ma),
    ]:
        diff = round(new - old, 3)
        if abs(diff) >= STEP:
            arrow = "↑" if diff > 0 else "↓"
            parts.append(f"{name}重み{arrow}{new:.2f}")

    if len(parts) == 1:
        parts.append("重みの変化なし")

    summary = " / ".join(parts)
    db.save_reflection(today, summary, new_rsi, new_macd, new_ma, wins, losses)

    return {"rsi": new_rsi, "macd": new_macd, "ma": new_ma, "gc": new_gc}
