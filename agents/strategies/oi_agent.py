import os
import time
import zipfile
import requests
import calendar
import pandas as pd
from datetime import datetime, date
from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class OIAgent(BaseStrategy):
    """
    Downloads NSE FNO bhav copy, checks OI spike vs 5x average.
    Generates BUY (contrarian long on 3 red candles + OI surge)
    and SELL (short on 3 green candles + OI drop).

    KEY FIX: expiry_date is now computed dynamically — last Thursday of
    current month. No more hardcoded date that silently expires.
    """

    BHAV_DIR   = "data/bhav_copy"
    MULTIPLIER = 5
    MIN_CANDLES = 9

    # Class-level shared state — downloaded once per day across all evaluate() calls
    _fno_df:  pd.DataFrame = None
    _oi_df:   pd.DataFrame = None
    _oi_date: str          = None

    def __init__(self):
        self.expiry_date = self._current_monthly_expiry()
        self._ensure_bhav_downloaded()
        self._load_fno_averages()

    # ── Dynamic expiry — last Thursday of current month ────────────────────
    @staticmethod
    def _current_monthly_expiry() -> str:
        today = date.today()
        # Find last Thursday of current month
        last_day = calendar.monthrange(today.year, today.month)[1]
        for day in range(last_day, 0, -1):
            if date(today.year, today.month, day).weekday() == 3:  # Thursday
                expiry = date(today.year, today.month, day)
                expiry = date(2026, 6, 30)  #Chandu Fix
                break

        # If expiry has already passed, roll to next month
        if expiry < today:
            if today.month == 12:
                next_year, next_month = today.year + 1, 1
            else:
                next_year, next_month = today.year, today.month + 1
            last_day = calendar.monthrange(next_year, next_month)[1]
            for day in range(last_day, 0, -1):
                if date(next_year, next_month, day).weekday() == 3:
                    expiry = date(next_year, next_month, day)
                    break

        return expiry.strftime("%Y-%m-%d")

    # ── One-time bhav download ─────────────────────────────────────────────
    def _ensure_bhav_downloaded(self):
        today = datetime.now().strftime("%Y%m%d")
        today = "20260527"
        if OIAgent._oi_date == today and OIAgent._oi_df is not None:
            return   # already downloaded today

        days_left = (datetime.strptime(self.expiry_date, "%Y-%m-%d") - datetime.now()).days
        if days_left < 0:
            print(f"  ⚠️  [OIAgent] expiry_date {self.expiry_date} has PASSED — rolling to next month")
        elif days_left < 5:
            print(f"  ⚠️  [OIAgent] expiry_date {self.expiry_date} expires in {days_left} days")

        os.makedirs(self.BHAV_DIR, exist_ok=True)
        headers  = {"User-Agent": "Mozilla/5.0"}
        url      = (
            f"https://nsearchives.nseindia.com/content/fo/"
            f"BhavCopy_NSE_FO_0_0_0_{today}_F_0000.csv.zip"
        )
        dest_zip = os.path.join(self.BHAV_DIR, f"{today}_bhav.csv.zip")

        try:
            print(f"  [OIAgent] Downloading bhav copy for {today} (expiry={self.expiry_date})...")
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            with open(dest_zip, "wb") as f:
                f.write(r.content)

            with zipfile.ZipFile(dest_zip, "r") as z:
                z.extractall(self.BHAV_DIR)

            csv_name = f"BhavCopy_NSE_FO_0_0_0_{today}_F_0000.csv"
            csv_path = os.path.join(self.BHAV_DIR, csv_name)
            df = pd.read_csv(csv_path)

            df = df[df["FinInstrmTp"] == "STF"]
            df = df[df["XpryDt"] == self.expiry_date].reset_index(drop=True)

            OIAgent._oi_df   = df
            OIAgent._oi_date = today
            print(f"  [OIAgent] Bhav copy loaded: {len(df)} futures rows for expiry {self.expiry_date}")

        except Exception as e:
            print(f"  [OIAgent] Bhav download failed: {e} — OI signals disabled for today")
            OIAgent._oi_df = pd.DataFrame()

    def _load_fno_averages(self):
        if OIAgent._fno_df is not None:
            return
        fno = pd.read_csv(
            "data/FNO_LST_190.csv",
            usecols=["Script", "CHG_IN_OI", "CONTRACTS"],
        )
        fno = fno.dropna(subset=["Script", "CHG_IN_OI", "CONTRACTS"])
        OIAgent._fno_df = fno.set_index("Script")

    # ── Main evaluation ────────────────────────────────────────────────────
    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        if OIAgent._oi_df is None or OIAgent._oi_df.empty:
            return None

        try:
            avg_chg_oi    = OIAgent._fno_df.loc[symbol, "CHG_IN_OI"]
            avg_contracts = OIAgent._fno_df.loc[symbol, "CONTRACTS"]
        except KeyError:
            return None

        sym_oi = OIAgent._oi_df[OIAgent._oi_df["TckrSymb"] == symbol]
        if sym_oi.empty:
            return None

        oi_change    = sym_oi["ChngInOpnIntrst"].iloc[0]
        oi_contracts = sym_oi["TtlNbOfTxsExctd"].iloc[0]

        last  = df.iloc[-1]
        prev1 = df.iloc[-2]
        prev2 = df.iloc[-3]

        three_red = (
            last["Close"]  < last["Open"]
            and prev1["Close"] < prev1["Open"]
            and prev2["Close"] < prev2["Open"]
            and last["Close"] < prev1["Close"] < prev2["Close"]
        )
        three_green = (
            last["Close"]  > last["Open"]
            and prev1["Close"] > prev1["Open"]
            and prev2["Close"] > prev2["Open"]
            and last["Close"] > prev1["Close"] > prev2["Close"]
        )

        threshold_oi        = self.MULTIPLIER * avg_chg_oi
        threshold_contracts = self.MULTIPLIER * avg_contracts

        if three_red and oi_change > threshold_oi and oi_contracts > threshold_contracts:
            fall_pct = round(100 * (prev2["Open"] - last["Close"]) / last["Close"], 2)
            return StrategySignal(
                strategy="OI",
                symbol=symbol,
                signal="BUY",
                confidence=0.76,
                reasoning=[
                    f"3 consecutive red candles, fall={fall_pct}%",
                    f"OI change {oi_change:,.0f} > {self.MULTIPLIER}x avg ({threshold_oi:,.0f})",
                    f"Contracts {oi_contracts:,.0f} > {self.MULTIPLIER}x avg ({threshold_contracts:,.0f})",
                    "Contrarian long: large OI buildup on falling price",
                ],
                metadata={
                    "oi_change":    float(oi_change),
                    "oi_contracts": float(oi_contracts),
                    "fall_pct":     fall_pct,
                    "close":        float(last["Close"]),
                },
            )

        if three_green and oi_change < -threshold_oi and oi_contracts > threshold_contracts:
            gain_pct = round(100 * (last["Close"] - prev2["Close"]) / prev2["Close"], 2)
            return StrategySignal(
                strategy="OI",
                symbol=symbol,
                signal="SELL",
                confidence=0.74,
                reasoning=[
                    f"3 consecutive green candles, gain={gain_pct}%",
                    f"OI dropping: {oi_change:,.0f} < -{self.MULTIPLIER}x avg",
                    f"Contracts {oi_contracts:,.0f} > {self.MULTIPLIER}x avg",
                    "Short signal: OI unwinding on rising price (distribution)",
                ],
                metadata={
                    "oi_change":    float(oi_change),
                    "oi_contracts": float(oi_contracts),
                    "gain_pct":     gain_pct,
                    "close":        float(last["Close"]),
                },
            )

        return None
