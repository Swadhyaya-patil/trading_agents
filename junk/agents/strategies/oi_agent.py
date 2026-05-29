import os
import time
import zipfile
import requests
import pandas as pd
from datetime import datetime
from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal


class OIAgent(BaseStrategy):
    """
    Ported from live_OI_predictor.check_for_long_short()
    Downloads NSE FNO bhav copy, checks OI spike vs 5x average
    for symbols in the FNO list.
    Generates BUY (contrarian long on 3 red candles + OI surge)
    and SELL (short on 3 green candles + OI drop).
    """

    BHAV_DIR        = "data/bhav_copy"
    MULTIPLIER      = 5
    MIN_CANDLES     = 9
    EXPIRY_DATE     = None    # set in __init__ — update monthly

    # ── These come from FNO_LST_190.csv columns CHG_IN_OI, CONTRACTS
    _fno_df: pd.DataFrame = None
    _oi_df:  pd.DataFrame = None   # shared across all evaluate() calls
    _oi_date: str         = None   # track which date was downloaded

    def __init__(self):
        # Next monthly expiry — update this monthly or automate
        self.expiry_date = "2026-05-29"
        self._ensure_bhav_downloaded()
        self._load_fno_averages()

    # ── One-time setup ─────────────────────────────────────────────────
    def _ensure_bhav_downloaded(self):
        today = datetime.now().strftime("%Y%m%d")
        if OIAgent._oi_date == today and OIAgent._oi_df is not None:
            return   # already downloaded today

        # add inside _ensure_bhav_downloaded(), after setting self.expiry_date
        expiry_dt = datetime.strptime(self.expiry_date, "%Y-%m-%d")
        days_left  = (expiry_dt - datetime.now()).days
        if days_left < 0:
            print(f"  ⚠️  [OIAgent] expiry_date {self.expiry_date} has PASSED — update it in oi_agent.py")
        elif days_left < 5:
            print(f"  ⚠️  [OIAgent] expiry_date expires in {days_left} days — update soon")
            
        os.makedirs(self.BHAV_DIR, exist_ok=True)
        headers = {"User-Agent": "Mozilla/5.0"}
        url = (
            f"https://nsearchives.nseindia.com/content/fo/"
            f"BhavCopy_NSE_FO_0_0_0_{today}_F_0000.csv.zip"
        )
        dest_zip = os.path.join(self.BHAV_DIR, f"{today}_bhav.csv.zip")

        try:
            print(f"  [OIAgent] Downloading bhav copy for {today}...")
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            with open(dest_zip, "wb") as f:
                f.write(r.content)

            # Extract
            with zipfile.ZipFile(dest_zip, "r") as z:
                z.extractall(self.BHAV_DIR)

            csv_name = f"BhavCopy_NSE_FO_0_0_0_{today}_F_0000.csv"
            csv_path = os.path.join(self.BHAV_DIR, csv_name)
            df = pd.read_csv(csv_path)

            # Filter futures only + current expiry
            df = df[df["FinInstrmTp"] == "STF"]
            df = df[df["XpryDt"] == self.expiry_date]
            df = df.reset_index(drop=True)

            OIAgent._oi_df   = df
            OIAgent._oi_date = today
            print(f"  [OIAgent] Bhav copy loaded: {len(df)} futures rows")

        except Exception as e:
            print(f"  [OIAgent] Bhav download failed: {e} — OI signals disabled")
            OIAgent._oi_df = pd.DataFrame()   # empty — evaluate() will return None

    def _load_fno_averages(self):
        if OIAgent._fno_df is not None:
            return
        fno = pd.read_csv("data/FNO_LST_190.csv",
                          usecols=["Script", "CHG_IN_OI", "CONTRACTS"])
        fno = fno.dropna(subset=["Script", "CHG_IN_OI", "CONTRACTS"])
        OIAgent._fno_df = fno.set_index("Script")

    # ── Main evaluation ────────────────────────────────────────────────
    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if df is None or len(df) < self.MIN_CANDLES:
            return None
        if OIAgent._oi_df is None or OIAgent._oi_df.empty:
            return None

        # ── Get average OI thresholds from FNO list
        try:
            avg_chg_oi   = OIAgent._fno_df.loc[symbol, "CHG_IN_OI"]
            avg_contracts = OIAgent._fno_df.loc[symbol, "CONTRACTS"]
        except KeyError:
            return None   # symbol not in FNO list

        # ── Get today's OI from bhav copy
        sym_oi = OIAgent._oi_df[OIAgent._oi_df["TckrSymb"] == symbol]
        if sym_oi.empty:
            return None

        oi_change   = sym_oi["ChngInOpnIntrst"].iloc[0]
        oi_contracts = sym_oi["TtlNbOfTxsExctd"].iloc[0]

        # ── Check last 3 candles (most recent = last row)
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

        # ── LONG signal: 3 red candles + OI spike (buildup on down move)
        if three_red and oi_change > threshold_oi and oi_contracts > threshold_contracts:
            fall_pct = round(
                100 * (prev2["Open"] - last["Close"]) / last["Close"], 2
            )
            reasons = [
                f"3 consecutive red candles, fall={fall_pct}%",
                f"OI change {oi_change:,.0f} > {self.MULTIPLIER}x avg ({threshold_oi:,.0f})",
                f"Contracts {oi_contracts:,.0f} > {self.MULTIPLIER}x avg ({threshold_contracts:,.0f})",
                "Contrarian long: large OI buildup on falling price",
            ]
            return StrategySignal(
                strategy="OI",
                symbol=symbol,
                signal="BUY",
                confidence=0.76,
                reasoning=reasons,
                metadata={
                    "oi_change":    float(oi_change),
                    "oi_contracts": float(oi_contracts),
                    "fall_pct":     fall_pct,
                    "close":        float(last["Close"]),
                },
            )

        # ── SHORT/SELL signal: 3 green candles + OI drop (unwinding on up move)
        if three_green and oi_change < -threshold_oi and oi_contracts > threshold_contracts:
            gain_pct = round(
                100 * (last["Close"] - prev2["Close"]) / prev2["Close"], 2
            )
            reasons = [
                f"3 consecutive green candles, gain={gain_pct}%",
                f"OI dropping: {oi_change:,.0f} < -{self.MULTIPLIER}x avg",
                f"Contracts {oi_contracts:,.0f} > {self.MULTIPLIER}x avg",
                "Short signal: OI unwinding on rising price (distribution)",
            ]
            return StrategySignal(
                strategy="OI",
                symbol=symbol,
                signal="SELL",
                confidence=0.74,
                reasoning=reasons,
                metadata={
                    "oi_change":    float(oi_change),
                    "oi_contracts": float(oi_contracts),
                    "gain_pct":     gain_pct,
                    "close":        float(last["Close"]),
                },
            )

        return None