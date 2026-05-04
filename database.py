import psycopg2
from datetime import datetime

DEFAULT_INITIAL_CASH = 100000


def _get_db_url():
    import streamlit as st
    return st.secrets["DATABASE_URL"]


class Database:
    def _connect(self):
        return psycopg2.connect(_get_db_url(), sslmode="require")

    def __init__(self):
        self._init_db()

    def _init_db(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    id INTEGER PRIMARY KEY,
                    cash DOUBLE PRECISION NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS holdings (
                    ticker TEXT PRIMARY KEY,
                    name TEXT,
                    shares INTEGER NOT NULL DEFAULT 0,
                    avg_price DOUBLE PRECISION NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    date TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    action TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    price DOUBLE PRECISION NOT NULL,
                    amount DOUBLE PRECISION NOT NULL,
                    fee DOUBLE PRECISION DEFAULT 0,
                    profit_loss DOUBLE PRECISION DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_history (
                    id SERIAL PRIMARY KEY,
                    date TEXT NOT NULL,
                    total_value DOUBLE PRECISION NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS indicator_weights (
                    id          INTEGER PRIMARY KEY DEFAULT 1,
                    rsi_weight  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                    macd_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                    ma_weight   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                    gc_weight   DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                    updated_at  TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signal_log (
                    id         SERIAL PRIMARY KEY,
                    date       TEXT NOT NULL,
                    ticker     TEXT NOT NULL,
                    rsi_score  INTEGER DEFAULT 0,
                    macd_score INTEGER DEFAULT 0,
                    ma_score   INTEGER DEFAULT 0,
                    gc_score   INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reflection_log (
                    id          SERIAL PRIMARY KEY,
                    date        TEXT NOT NULL,
                    summary     TEXT,
                    rsi_weight  DOUBLE PRECISION,
                    macd_weight DOUBLE PRECISION,
                    ma_weight   DOUBLE PRECISION,
                    wins        INTEGER DEFAULT 0,
                    losses      INTEGER DEFAULT 0
                )
            """)

            defaults = {
                "initial_cash": str(DEFAULT_INITIAL_CASH),
                "fee_rate":     "0.001",
                "sl_pct":       "10",
                "tp_pct":       "20",
                "ai_level":     "普通",
            }
            for key, val in defaults.items():
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                    (key, val)
                )

            cur.execute("SELECT COUNT(*) FROM portfolio")
            if cur.fetchone()[0] == 0:
                cur.execute("SELECT value FROM settings WHERE key='initial_cash'")
                ic = float(cur.fetchone()[0])
                cur.execute("INSERT INTO portfolio (id, cash) VALUES (1, %s)", (ic,))

            cur.execute("SELECT COUNT(*) FROM indicator_weights")
            if cur.fetchone()[0] == 0:
                cur.execute(
                    "INSERT INTO indicator_weights (id, rsi_weight, macd_weight, ma_weight, gc_weight, updated_at) VALUES (1, 1.0, 1.0, 1.0, 1.0, %s)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ── 設定 ──────────────────────────────────────────────────

    def get_settings(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
            s = {r[0]: r[1] for r in rows}
            return {
                "initial_cash": float(s.get("initial_cash", DEFAULT_INITIAL_CASH)),
                "fee_rate":     float(s.get("fee_rate", 0.001)),
                "sl_pct":       float(s.get("sl_pct", 10)),
                "tp_pct":       float(s.get("tp_pct", 20)),
                "ai_level":     s.get("ai_level", "普通"),
            }
        finally:
            cur.close()
            conn.close()

    def save_settings(self, initial_cash, fee_rate, sl_pct, tp_pct, ai_level):
        conn = self._connect()
        cur = conn.cursor()
        try:
            for key, val in {
                "initial_cash": str(initial_cash),
                "fee_rate":     str(fee_rate),
                "sl_pct":       str(sl_pct),
                "tp_pct":       str(tp_pct),
                "ai_level":     ai_level,
            }.items():
                cur.execute(
                    "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                    (key, val)
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ── 残高・保有 ────────────────────────────────────────────

    def get_cash(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT cash FROM portfolio WHERE id=1")
            row = cur.fetchone()
            return row[0] if row else DEFAULT_INITIAL_CASH
        finally:
            cur.close()
            conn.close()

    def get_holding(self, ticker):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ticker, name, shares, avg_price FROM holdings WHERE ticker=%s", (ticker,)
            )
            row = cur.fetchone()
            return {"ticker": row[0], "name": row[1], "shares": row[2], "avg_price": row[3]} if row else None
        finally:
            cur.close()
            conn.close()

    def get_all_holdings(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ticker, name, shares, avg_price FROM holdings WHERE shares > 0"
            )
            rows = cur.fetchall()
            return [{"ticker": r[0], "name": r[1], "shares": r[2], "avg_price": r[3]} for r in rows]
        finally:
            cur.close()
            conn.close()

    # ── 売買 ──────────────────────────────────────────────────

    def execute_buy(self, ticker, name, shares, price, fee_rate=0.001):
        fee = round(shares * price * fee_rate)
        amount = shares * price + fee
        if amount > self.get_cash():
            return False, "現金が不足しています"
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE portfolio SET cash = cash - %s WHERE id=1", (amount,))
            ex = self.get_holding(ticker)
            if ex and ex["shares"] > 0:
                total = ex["shares"] + shares
                new_avg = (ex["shares"] * ex["avg_price"] + shares * price) / total
                cur.execute(
                    "UPDATE holdings SET shares=%s, avg_price=%s WHERE ticker=%s",
                    (total, new_avg, ticker),
                )
            else:
                cur.execute(
                    "INSERT INTO holdings (ticker, name, shares, avg_price) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (ticker) DO UPDATE SET name=EXCLUDED.name, shares=EXCLUDED.shares, avg_price=EXCLUDED.avg_price",
                    (ticker, name, shares, price),
                )
            cur.execute(
                "INSERT INTO trades (date, ticker, name, action, shares, price, amount, fee, profit_loss) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ticker, name, "買い", shares, price, amount, fee, 0),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return True, f"{name} {shares}株 ¥{price:,.0f}で購入（手数料 ¥{fee:,.0f}）"

    def execute_sell(self, ticker, shares, price, fee_rate=0.001):
        ex = self.get_holding(ticker)
        if not ex or ex["shares"] < shares:
            return False, "保有株数が不足しています"
        fee = round(shares * price * fee_rate)
        amount = shares * price - fee
        profit_loss = (price - ex["avg_price"]) * shares - fee
        name = ex["name"] or ticker
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("UPDATE portfolio SET cash = cash + %s WHERE id=1", (amount,))
            cur.execute(
                "UPDATE holdings SET shares=%s WHERE ticker=%s", (ex["shares"] - shares, ticker)
            )
            cur.execute(
                "INSERT INTO trades (date, ticker, name, action, shares, price, amount, fee, profit_loss) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ticker, name, "売り", shares, price, amount, fee, profit_loss),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return True, f"{name} {shares}株 ¥{price:,.0f}で売却（損益 ¥{profit_loss:+,.0f}）"

    # ── 取引履歴・統計 ────────────────────────────────────────

    def get_trades(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT date, name, action, shares, price, amount, fee, profit_loss FROM trades ORDER BY date DESC"
            )
            rows = cur.fetchall()
            return [
                {
                    "日時": r[0], "銘柄": r[1], "売買": r[2], "株数": r[3],
                    "価格": f"¥{r[4]:,.0f}", "金額": f"¥{r[5]:,.0f}",
                    "手数料": f"¥{r[6]:,.0f}",
                    "損益": f"¥{r[7]:+,.0f}" if r[2] == "売り" else "-",
                }
                for r in rows
            ]
        finally:
            cur.close()
            conn.close()

    def get_trade_stats(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT action, profit_loss, fee FROM trades")
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()
        total_fee = sum(r[2] for r in rows)
        sells = [r[1] for r in rows if r[0] == "売り"]
        if not sells:
            return {"total_trades": len(rows), "sell_trades": 0,
                    "win_rate": 0.0, "avg_pl": 0.0, "best": 0.0, "worst": 0.0,
                    "total_fee": total_fee}
        wins = [p for p in sells if p > 0]
        return {
            "total_trades": len(rows),
            "sell_trades":  len(sells),
            "win_rate":     len(wins) / len(sells) * 100,
            "avg_pl":       sum(sells) / len(sells),
            "best":         max(sells),
            "worst":        min(sells),
            "total_fee":    total_fee,
        }

    # ── スナップショット ───────────────────────────────────────

    def save_snapshot(self, total_value):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT date FROM portfolio_history ORDER BY date DESC LIMIT 1"
            )
            last = cur.fetchone()
            if not last or not last[0].startswith(today):
                cur.execute(
                    "INSERT INTO portfolio_history (date, total_value) VALUES (%s, %s)",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total_value),
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def save_snapshot_force(self, total_value):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO portfolio_history (date, total_value) VALUES (%s, %s)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), total_value),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def get_portfolio_history(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT date, total_value FROM portfolio_history ORDER BY date"
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    # ── 自己学習：指標重み ────────────────────────────────────

    def get_weights(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT rsi_weight, macd_weight, ma_weight, gc_weight FROM indicator_weights WHERE id=1"
            )
            row = cur.fetchone()
            return {"rsi": row[0], "macd": row[1], "ma": row[2], "gc": row[3]} if row else \
                   {"rsi": 1.0, "macd": 1.0, "ma": 1.0, "gc": 1.0}
        finally:
            cur.close()
            conn.close()

    def save_weights(self, rsi, macd, ma, gc):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE indicator_weights SET rsi_weight=%s, macd_weight=%s, ma_weight=%s, gc_weight=%s, updated_at=%s WHERE id=1",
                (rsi, macd, ma, gc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    # ── 自己学習：シグナル記録 ────────────────────────────────

    def log_signal(self, ticker, rsi_score, macd_score, ma_score, gc_score):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO signal_log (date, ticker, rsi_score, macd_score, ma_score, gc_score) VALUES (%s, %s, %s, %s, %s, %s)",
                (datetime.now().strftime("%Y-%m-%d"), ticker, rsi_score, macd_score, ma_score, gc_score),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def get_buy_signal_for_ticker(self, ticker):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT rsi_score, macd_score, ma_score, gc_score FROM signal_log WHERE ticker=%s ORDER BY id DESC LIMIT 1",
                (ticker,),
            )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    def get_todays_sells(self):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT ticker, profit_loss FROM trades WHERE action='売り' AND date LIKE %s",
                (f"{today}%",),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    # ── 自己学習：反省ログ ────────────────────────────────────

    def save_reflection(self, date, summary, rsi_w, macd_w, ma_w, wins, losses):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO reflection_log (date, summary, rsi_weight, macd_weight, ma_weight, wins, losses) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (date, summary, rsi_w, macd_w, ma_w, wins, losses),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    def get_last_reflection_date(self):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT date FROM reflection_log ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row[0][:10] if row else None
        finally:
            cur.close()
            conn.close()

    def get_reflection_log(self, limit=14):
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT date, summary, rsi_weight, macd_weight, ma_weight, wins, losses FROM reflection_log ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    # ── リセット ──────────────────────────────────────────────

    def reset(self):
        ic = self.get_settings()["initial_cash"]
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM portfolio")
            cur.execute("DELETE FROM holdings")
            cur.execute("DELETE FROM trades")
            cur.execute("DELETE FROM portfolio_history")
            cur.execute("DELETE FROM signal_log")
            cur.execute("DELETE FROM reflection_log")
            cur.execute(
                "UPDATE indicator_weights SET rsi_weight=1.0, macd_weight=1.0, ma_weight=1.0, gc_weight=1.0 WHERE id=1"
            )
            cur.execute("INSERT INTO portfolio (id, cash) VALUES (1, %s)", (ic,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
