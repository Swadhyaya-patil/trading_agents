# import pandas as pd

# from brokers.angleone.hist_data import hist_data
# from feature_engineering.enrich_features import enrich_features


# class HistoricalAgent:

#     def __init__(self):

#         self.client = hist_data()
#         self.client.log_in()

#     def get_symbols(self):

#         df = pd.read_csv("data/nifty500.csv")

#         return df['Symbol'].dropna().unique().tolist()

#     def get_market_data(
#         self,
#         symbol,
#         interval,
#         from_date,
#         to_date
#     ):

#         try:

#             df = self.client.get_eq_data(
#                 symbol,
#                 symbol,
#                 from_date,
#                 to_date,
#                 interval
#             )

#             if df is None or len(df) == 0:
#                 return None

#             df = enrich_features(df)

#             return df

#         except Exception as e:

#             print(f"ERROR fetching {symbol}: {e}")

#             return None










import os
import time
import traceback
import pandas as pd
from datetime import datetime
from dateutil.relativedelta import relativedelta

from brokers.angleone.hist_data import hist_data
from feature_engineering.enrich_features import enrich_features


class HistoricalAgent:

    CSV_PATH        = "data/FNO_LST_190.csv"
    NUM_DAYS        = 201          # match original get_daily_data()
    DEFAULT_INTERVAL = "ONE_DAY"
    API_SLEEP       = 0.4          # rate-limit guard, same as original

    def __init__(self):
        self.client = hist_data()
        self.client.log_in()
        self._symbol_df = None     # cache so CSV is only read once

    # ── Symbol list ────────────────────────────────────────────────────
    def get_symbols(self) -> list[dict]:
        """
        Returns list of dicts: [{"script": "RELIANCE", "code": "2885"}, ...]
        Handles the mixed CSV (some rows have all 6 cols, tail rows only 2).
        """
        df = pd.read_csv(self.CSV_PATH, usecols=["Script", "Code"])
        df = df.dropna(subset=["Script", "Code"])
        df["Code"] = df["Code"].astype(str).str.strip()
        df["Script"] = df["Script"].astype(str).str.strip()
        self._symbol_df = df
        return df.to_dict("records")          # [{"Script": ..., "Code": ...}]

    # ── Market data ────────────────────────────────────────────────────
    def get_market_data(
        self,
        symbol: str,
        code: str,
        interval: str = None,
        from_date: str = None,
        to_date: str = None,
    ):
        """
        Fetches OHLCV from AngelOne and enriches with indicators.
        symbol = Script name (e.g. "RELIANCE")
        code   = AngelOne token code (e.g. "2885")   ← was the bug
        """
        interval = interval or self.DEFAULT_INTERVAL

        # ── Date range — match original num_days_in_past=201
        to_dt   = to_date   or datetime.now().strftime("%Y-%m-%d %H:%M")
        if from_date is None:
            tmp     = datetime.strptime(to_dt.split(" ")[0], "%Y-%m-%d")
            from_dt = (tmp + relativedelta(days=-self.NUM_DAYS)).strftime("%Y-%m-%d %H:%M")
        else:
            from_dt = from_date

        try:
            time.sleep(self.API_SLEEP)               # rate-limit guard
            df = self.client.get_eq_data(
                symbol,
                code,                                # ← correct: pass Code not Symbol
                from_dt,
                to_dt,
                interval,
            )
            if df is None or len(df) == 0:
                print(f"  [historical_agent] No data returned for {symbol}")
                return None

            df = df.reset_index(drop=True)
            df["Date"] = pd.to_datetime(df["Date"])
            df = enrich_features(df)                 # ATR, BB_WIDTH etc for risk manager
            return df

        except Exception as e:
            print(f"  [historical_agent] ERROR fetching {symbol}: {e}")
            traceback.print_exc()
            return None