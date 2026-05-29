import time
import traceback
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

from brokers.angleone.hist_data import hist_data
from feature_engineering.enrich_features import enrich_features


class HistoricalAgent:
    """
    Instantiate ONCE at module level in nodes.py and reuse for the entire run.
    Never instantiate per-symbol — each instantiation triggers a full
    AngelOne login + new WebSocket thread.
    """

    CSV_PATH         = "data/FNO_LST_190.csv"
    NUM_DAYS         = 201
    DEFAULT_INTERVAL = "ONE_DAY"

    # Rate-limit guard — AngelOne free tier: ~1 req/sec. 0.6s keeps safely under.
    API_SLEEP    = 0.6

    # Retry config for transient 429 / network errors
    MAX_RETRIES  = 3
    RETRY_BACKOFF = 2.0   # seconds, doubles each attempt

    def __init__(self):
        self.client = hist_data()
        self.client.log_in()
        self._symbol_df = None

        # Run-level counters
        self._fetch_ok    = 0
        self._fetch_fail  = 0
        self._fetch_empty = 0

    # ── Symbol list ────────────────────────────────────────────────────────
    def get_symbols(self) -> list[dict]:
        """Returns [{"Script": "RELIANCE", "Code": "2885"}, ...]"""
        df = pd.read_csv(self.CSV_PATH, usecols=["Script", "Code"])
        df = df.dropna(subset=["Script", "Code"])
        df["Code"]   = df["Code"].astype(str).str.strip()
        df["Script"] = df["Script"].astype(str).str.strip()
        self._symbol_df = df
        return df.to_dict("records")

    # ── Market data ────────────────────────────────────────────────────────
    def get_market_data(
        self,
        symbol: str,
        code: str,
        interval: str = None,
        from_date: str = None,
        to_date: str = None,
    ):
        """
        Fetches OHLCV from AngelOne and enriches with all indicators.
        Includes rate-limit sleep + exponential-backoff retry.
        """
        interval = interval or self.DEFAULT_INTERVAL

        to_dt = to_date or datetime.now().strftime("%Y-%m-%d %H:%M")
        if from_date is None:
            tmp     = datetime.strptime(to_dt.split(" ")[0], "%Y-%m-%d")
            from_dt = (tmp + relativedelta(days=-self.NUM_DAYS)).strftime(
                "%Y-%m-%d %H:%M"
            )
        else:
            from_dt = from_date

        # Sleep BEFORE the call — prevents burst at startup
        time.sleep(self.API_SLEEP)

        df = self._fetch_with_retry(symbol, code, from_dt, to_dt, interval)

        if df is None:
            self._fetch_fail += 1
            return None

        if len(df) == 0:
            print(f"  [historical_agent] Empty data for {symbol}")
            self._fetch_empty += 1
            return None

        try:
            df = df.reset_index(drop=True)
            df["Date"] = pd.to_datetime(df["Date"])
            df = enrich_features(df)
            self._fetch_ok += 1
            return df
        except Exception as e:
            print(f"  [historical_agent] enrich_features failed for {symbol}: {e}")
            traceback.print_exc()
            self._fetch_fail += 1
            return None

    def _fetch_with_retry(self, symbol, code, from_dt, to_dt, interval):
        """Calls get_eq_data with exponential backoff on failure."""
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                return self.client.get_eq_data(symbol, code, from_dt, to_dt, interval)
            except Exception as e:
                err = str(e)
                is_rate_limit = any(
                    kw in err.lower()
                    for kw in ["429", "rate limit", "too many", "throttl"]
                )
                if attempt < self.MAX_RETRIES:
                    wait = self.RETRY_BACKOFF * (2 ** (attempt - 1))
                    tag  = "rate-limit" if is_rate_limit else "error"
                    print(
                        f"  [historical_agent] {symbol} {tag} "
                        f"attempt {attempt}/{self.MAX_RETRIES} — "
                        f"retry in {wait:.1f}s: {err[:80]}"
                    )
                    time.sleep(wait)
                else:
                    print(
                        f"  [historical_agent] {symbol} failed after "
                        f"{self.MAX_RETRIES} attempts: {err[:120]}"
                    )
                    return None

    # ── Run summary ────────────────────────────────────────────────────────
    def print_summary(self):
        total = self._fetch_ok + self._fetch_fail + self._fetch_empty
        print(
            f"\n[historical_agent] Run summary: "
            f"{total} symbols | "
            f"{self._fetch_ok} OK | "
            f"{self._fetch_empty} empty | "
            f"{self._fetch_fail} failed"
        )
