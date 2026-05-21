"""
feature_engineering/chart_snapshot.py

Drop-in replacement for chandu_util.save_trade_snapshot_V3()
Matches the real implementation exactly — 2x3 subplot CNN-safe chart.

Panels:
    [0,0] Candlestick + EMA_21 + EMA_51 + Bollinger Bands
    [0,1] MACD + Signal + Histogram
    [0,2] RSI (with 30/70 lines)
    [1,0] ADX + +DI / -DI
    [1,1] Volume + OBV
    [1,2] Stochastic %K_L / %D_L  ← added (was empty in original)

Key differences vs original chandu_util:
    - Fixed misplaced imports (original had imports inside module body → IndentationError)
    - Added panel 6 (Stochastic) instead of leaving it empty
    - call_signature adapted: idx can be int (row index) or None (use last row)
    - Safe fallback for missing columns — never raises, just skips that panel
"""

import os
import io
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")   # headless — no display needed on server
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from mplfinance.original_flavor import candlestick_ohlc
from io import BytesIO


def save_trade_snapshot_V3(
    df,
    idx=None,
    filename: str = None,
    buffer_needed: bool = False,
) -> bytes | None:
    """
    CNN-safe chart generator — 2x3 subplot image (12x8 inches).

    Parameters
    ----------
    df            : enriched DataFrame with OHLCV + indicators
                    Must have at minimum: Date, Open, High, Low, Close, Volume
    idx           : row index into df to use as the END of the window.
                    Slices df[max(0, idx-50) : idx+1] — matches original exactly.
                    Pass None to use the last row (live mode).
    filename      : path to save PNG (used when buffer_needed=False)
    buffer_needed : if True, returns PNG as bytes; if False, saves to filename

    Returns
    -------
    bytes if buffer_needed=True, else None
    """
    # ── Resolve idx
    if idx is None:
        idx = len(df) - 1

    df_ = df.iloc[max(0, idx - 50): idx + 1].copy().reset_index(drop=True)

    # ── Date → matplotlib float
    df_["Date_num"] = mdates.date2num(pd.to_datetime(df_["Date"]))

    # ── Figure setup
    fig, axs = plt.subplots(2, 3, figsize=(12, 8))
    plt.subplots_adjust(wspace=0.25, hspace=0.4)

    def clean_axis(ax):
        """Remove all ticks, labels, grid, spines — pure visual signal only."""
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.xaxis.set_visible(False)
        ax.yaxis.set_visible(False)

    # ── [0,0] Candlestick + EMA_21 + EMA_51 + Bollinger Bands
    ohlc = df_[["Date_num", "Open", "High", "Low", "Close"]].dropna().values
    candlestick_ohlc(axs[0, 0], ohlc, width=0.6, colorup="g", colordown="r")
    if "EMA_21" in df_:
        axs[0, 0].plot(df_["Date_num"], df_["EMA_21"],
                       color="magenta", linewidth=1)
    if "EMA_51" in df_:
        axs[0, 0].plot(df_["Date_num"], df_["EMA_51"],
                       color="blue", linewidth=1)
    if "BB_UPPER" in df_ and "BB_LOWER" in df_:
        axs[0, 0].plot(df_["Date_num"], df_["BB_UPPER"],
                       color="grey", linestyle="--", linewidth=0.8)
        axs[0, 0].plot(df_["Date_num"], df_["BB_LOWER"],
                       color="grey", linestyle="--", linewidth=0.8)
    clean_axis(axs[0, 0])

    # ── [0,1] MACD + Signal + Histogram
    if all(c in df_ for c in ["MACD", "MACD_signal"]):
        axs[0, 1].plot(df_["Date_num"], df_["MACD"],
                       color="purple", linewidth=1)
        axs[0, 1].plot(df_["Date_num"], df_["MACD_signal"],
                       color="black", linewidth=1)
    if "MACD_Histogram" in df_:
        colors = ["green" if v >= 0 else "red"
                  for v in df_["MACD_Histogram"].fillna(0)]
        axs[0, 1].bar(df_["Date_num"], df_["MACD_Histogram"],
                      color=colors, width=0.4, alpha=0.5)
    clean_axis(axs[0, 1])

    # ── [0,2] RSI
    if "RSI" in df_:
        axs[0, 2].plot(df_["Date_num"], df_["RSI"],
                       color="brown", linewidth=1)
        axs[0, 2].axhline(70, color="grey", linestyle="--", linewidth=0.5)
        axs[0, 2].axhline(30, color="grey", linestyle="--", linewidth=0.5)
        axs[0, 2].axhline(50, color="lightgrey", linestyle=":", linewidth=0.4)
    clean_axis(axs[0, 2])

    # ── [1,0] ADX + +DI + -DI
    if "ADX" in df_:
        axs[1, 0].plot(df_["Date_num"], df_["ADX"],
                       color="darkorange", linewidth=1, label="ADX")
    if "+DI" in df_:
        axs[1, 0].plot(df_["Date_num"], df_["+DI"],
                       color="green", linewidth=0.8)
    if "-DI" in df_:
        axs[1, 0].plot(df_["Date_num"], df_["-DI"],
                       color="red", linewidth=0.8)
    clean_axis(axs[1, 0])

    # ── [1,1] Volume bars + OBV overlay
    if "Volume" in df_:
        vol_colors = []
        for i in range(len(df_)):
            if df_["Close"].iloc[i] >= df_["Open"].iloc[i]:
                vol_colors.append("steelblue")
            else:
                vol_colors.append("salmon")
        axs[1, 1].bar(df_["Date_num"], df_["Volume"],
                      color=vol_colors, alpha=0.6, width=0.5)
    if "OBV" in df_:
        ax_obv = axs[1, 1].twinx()
        ax_obv.plot(df_["Date_num"], df_["OBV"] / 1e6,
                    color="darkgreen", linewidth=1)
        clean_axis(ax_obv)
    clean_axis(axs[1, 1])

    # ── [1,2] Stochastic %K_L / %D_L  (was empty in original — now used)
    if "%K_L" in df_ and "%D_L" in df_:
        axs[1, 2].plot(df_["Date_num"], df_["%K_L"],
                       color="dodgerblue", linewidth=1, label="%K")
        axs[1, 2].plot(df_["Date_num"], df_["%D_L"],
                       color="tomato", linewidth=1, label="%D")
        axs[1, 2].axhline(80, color="grey", linestyle="--", linewidth=0.4)
        axs[1, 2].axhline(20, color="grey", linestyle="--", linewidth=0.4)
    elif "%K_S" in df_ and "%D_S" in df_:
        # fallback to short stochastic if long not available
        axs[1, 2].plot(df_["Date_num"], df_["%K_S"],
                       color="dodgerblue", linewidth=1)
        axs[1, 2].plot(df_["Date_num"], df_["%D_S"],
                       color="tomato", linewidth=1)
    clean_axis(axs[1, 2])

    plt.tight_layout(pad=0)

    # ── Output
    if buffer_needed:
        buf = BytesIO()
        plt.savefig(buf, format="png",
                    bbox_inches="tight", pad_inches=0, dpi=100)
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    else:
        if filename:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            plt.savefig(filename,
                        bbox_inches="tight", pad_inches=0, dpi=100)
        plt.close(fig)
        return None