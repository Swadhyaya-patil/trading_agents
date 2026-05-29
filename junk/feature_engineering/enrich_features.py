import pandas as pd
import numpy as np


def enrich_features(df):

    eps = 1e-9

    """
    Extended indicator generator. Adds the full set of features used by
    CNN_features, Conv1D_features and MLP_features including Vol_Ratio and Momentum_10.
    (Function body preserved from original script.)
    """
    eps = 1e-9
    df = df.copy()

    # Ensure Date is datetime if present
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')

    # Basic numeric check
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        if col not in df.columns:
            raise ValueError(f"Required column missing: {col}")
        df[col] = pd.to_numeric(df[col], errors='coerce')

    # -------------------------
    # Moving averages & MACD
    # -------------------------
    df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
    df['EMA_51'] = df['Close'].ewm(span=51, adjust=False).mean()
    df['SMA_50'] = df['Close'].rolling(window=50, min_periods=1).mean()
    df['SMA_200'] = df['Close'].rolling(window=200, min_periods=1).mean()

    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Histogram'] = df['MACD'] - df['MACD_signal']
    df['MACD_Cross_Flag'] = np.sign(df['MACD'] - df['MACD_signal'])

    # -------------------------
    # Returns & volatility
    # -------------------------
    df['log_return_1'] = np.log(df['Close'] / df['Close'].shift(1)).replace([np.inf, -np.inf], np.nan)
    df['pct_return_1'] = df['Close'].pct_change(1)
    df['pct_return_3'] = df['Close'].pct_change(3)
    df['Return_1'] = df['pct_return_1']
    df['Return_3'] = df['pct_return_3']
    df['Return_7'] = df['Close'].pct_change(7)
    df['Volatility_21'] = df['pct_return_1'].rolling(window=21, min_periods=1).std()

    # -------------------------
    # Stochastics (long & short)
    # -------------------------
    k_long, d_long, smooth_long = 34, 8, 3
    k_short, d_short, smooth_short = 5, 3, 3

    # long
    low_min = df['Low'].rolling(window=k_long, min_periods=1).min()
    high_max = df['High'].rolling(window=k_long, min_periods=1).max()
    denom = (high_max - low_min).replace(0, np.nan)
    df['%K_L'] = (df['Close'] - low_min) / (denom + eps) * 100
    df['%D_L'] = df['%K_L'].rolling(window=d_long, min_periods=1).mean()
    df['%K_L'] = df['%K_L'].rolling(window=smooth_long, min_periods=1).mean()
    df['%D_L'] = df['%D_L'].rolling(window=smooth_long, min_periods=1).mean()

    # short
    low_min_s = df['Low'].rolling(window=k_short, min_periods=1).min()
    high_max_s = df['High'].rolling(window=k_short, min_periods=1).max()
    denom_s = (high_max_s - low_min_s).replace(0, np.nan)
    df['%K_S'] = (df['Close'] - low_min_s) / (denom_s + eps) * 100
    df['%D_S'] = df['%K_S'].rolling(window=d_short, min_periods=1).mean()
    df['%K_S'] = df['%K_S'].rolling(window=smooth_short, min_periods=1).mean()
    df['%D_S'] = df['%D_S'].rolling(window=smooth_short, min_periods=1).mean()

    # -------------------------
    # RSI
    # -------------------------
    delta = df['Close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ema_up = up.ewm(com=10, adjust=False).mean()
    ema_down = down.ewm(com=10, adjust=False).mean()
    rs = ema_up / (ema_down + eps)
    df['RSI'] = 100 - (100 / (1 + rs))

    # -------------------------
    # True Range & ATR
    # -------------------------
    df['prev_close'] = df['Close'].shift(1)
    tr1 = df['High'] - df['Low']
    tr2 = (df['High'] - df['prev_close']).abs()
    tr3 = (df['Low'] - df['prev_close']).abs()
    df['TR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = df['TR'].rolling(window=14, min_periods=1).mean()
    df['ATR_pct'] = df['ATR'] / (df['Close'] + eps)

    # -------------------------
    # Bollinger & Keltner
    # -------------------------
    bb_mid = df['Close'].rolling(window=21, min_periods=1).mean()
    bb_std = df['Close'].rolling(window=21, min_periods=1).std()
    df['BB_MID'] = bb_mid
    df['BB_STD'] = bb_std
    df['BB_UPPER'] = bb_mid + 2 * bb_std
    df['BB_LOWER'] = bb_mid - 2 * bb_std
    df['BB_WIDTH'] = df['BB_UPPER'] - df['BB_LOWER']
    df['BB_POSITION'] = (df['Close'] - df['BB_LOWER']) / (df['BB_UPPER'] - df['BB_LOWER'] + eps)

    df['Keltner_upper'] = bb_mid + 2 * df['ATR']
    df['Keltner_lower'] = bb_mid - 2 * df['ATR']

    # -------------------------
    # Volume, OBV, CMF, VWAP and Volume-derived features
    # -------------------------
    df['Volume_mean_21'] = df['Volume'].rolling(window=21, min_periods=1).mean()
    df['Vol_Ratio'] = df['Volume'] / (df['Volume_mean_21'] + eps)

    df['Volume_Bars'] = df['Volume']

    close_diff = df['Close'].diff().fillna(0)
    sign = np.sign(close_diff)
    obv = (sign * df['Volume']).fillna(0).cumsum()
    df['OBV'] = obv
    df['OBV_EMA_21'] = df['OBV'].ewm(span=21, adjust=False).mean()
    df['OBV_pct_change_3'] = df['OBV'].pct_change(3)

    # Chaikin Money Flow (CMF)
    n = 14
    mfv = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / (df['High'] - df['Low'] + eps) * df['Volume']
    df['CMF'] = mfv.rolling(window=n, min_periods=1).sum() / (df['Volume'].rolling(window=n, min_periods=1).sum() + eps)

    # VWAP (cumulative)
    typical_price_v = (df['High'] + df['Low'] + df['Close']) / 3.0
    cum_vp = (typical_price_v * df['Volume']).cumsum()
    cum_vol = df['Volume'].cumsum()
    df['VWAP'] = cum_vp / (cum_vol + eps)

    # -------------------------
    # Momentum_10 (explicitly added)
    # -------------------------
    df['Momentum_10'] = df['Close'] - df['Close'].shift(10)

    # -------------------------
    # Williams %R, CCI, MFI
    # -------------------------
    n_w = 14
    hh = df['High'].rolling(window=n_w, min_periods=1).max()
    ll = df['Low'].rolling(window=n_w, min_periods=1).min()
    df['Williams_%R'] = -100 * (hh - df['Close']) / (hh - ll + eps)

    tp = (df['High'] + df['Low'] + df['Close']) / 3.0
    tp_ma = tp.rolling(window=n_w, min_periods=1).mean()
    tp_std = tp.rolling(window=n_w, min_periods=1).std()
    df['CCI'] = (tp - tp_ma) / (0.015 * (tp_std + eps))

    typical_price = tp
    money_flow = typical_price * df['Volume']
    pos_flow = np.where(typical_price > typical_price.shift(1), money_flow, 0.0)
    neg_flow = np.where(typical_price < typical_price.shift(1), money_flow, 0.0)
    pos_mf_sum = pd.Series(pos_flow).rolling(window=n_w, min_periods=1).sum()
    neg_mf_sum = pd.Series(neg_flow).rolling(window=n_w, min_periods=1).sum()
    mfr = pos_mf_sum / (neg_mf_sum + eps)
    df['MFI'] = 100.0 - (100.0 / (1.0 + mfr))

    # -------------------------
    # Parabolic SAR
    # -------------------------
    df['Parabolic_SAR'] = df['Low']  # init
    af = 0.02
    max_af = 0.2
    ep = df['High'].iloc[0]
    trend = 1
    for i in range(1, len(df)):
        prev_sar = df['Parabolic_SAR'].iloc[i-1]
        sar = prev_sar + af * (ep - prev_sar)
        if trend == 1:
            if df['Low'].iloc[i] < sar:
                trend = -1
                sar = ep
                ep = df['Low'].iloc[i]
                af = 0.02
            else:
                ep = max(ep, df['High'].iloc[i])
                af = min(max_af, af + 0.02)
        else:
            if df['High'].iloc[i] > sar:
                trend = 1
                sar = ep
                ep = df['High'].iloc[i]
                af = 0.02
            else:
                ep = min(ep, df['Low'].iloc[i])
                af = min(max_af, af + 0.02)
        df.at[df.index[i], 'Parabolic_SAR'] = sar

    # -------------------------
    # ADX (+DI, -DI), Aroon
    # -------------------------
    up_move = df['High'].diff()
    down_move = -df['Low'].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = df['TR'].ewm(alpha=1.0/14, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm, index=df.index).ewm(alpha=1.0/14, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm, index=df.index).ewm(alpha=1.0/14, adjust=False).mean()

    plus_di = 100.0 * (plus_dm_smooth / (tr_smooth + eps))
    minus_di = 100.0 * (minus_dm_smooth / (tr_smooth + eps))
    dx = 100.0 * (np.abs(plus_di - minus_di) / (plus_di + minus_di + eps))
    df['+DI'] = plus_di
    df['-DI'] = minus_di
    df['DX'] = dx
    df['ADX'] = dx.ewm(alpha=1.0/14, adjust=False).mean()

    # Aroon oscillator (period 25)
    period = 25
    df['Aroon_Oscillator'] = df['High'].rolling(window=period, min_periods=1).apply(
        lambda x: (period - np.argmax(x[::-1])) / period * 100.0
    ) - df['Low'].rolling(window=period, min_periods=1).apply(
        lambda x: (period - np.argmax(x[::-1])) / period * 100.0
    )

    # -------------------------
    # Snapshot / relative features
    # -------------------------
    df['EMA_21_minus_EMA_51'] = df['EMA_21'] - df['EMA_51']
    df['Price_EMA_21_Ratio'] = (df['Close'] - df['EMA_21']) / (df['EMA_21'] + eps)
    df['Price_EMA_51_Ratio'] = (df['Close'] - df['EMA_51']) / (df['EMA_51'] + eps)
    df['SMA_50_dist'] = (df['Close'] - df['SMA_50']) / (df['SMA_50'] + eps)
    df['SMA_200_dist'] = (df['Close'] - df['SMA_200']) / (df['SMA_200'] + eps)

    df['Body'] = (df['Close'] - df['Open']).abs()
    df['Wick'] = (df['High'] - df['Low']) - df['Body']
    df['Body_Wick_Ratio'] = df['Body'] / (df['Wick'].abs() + eps)
    df['Day_Trend'] = np.where(df['Close'] > df['Open'], 1, 0)

    # Donchian & Close position
    df['Rolling_Max_20'] = df['Close'].rolling(window=20, min_periods=1).max()
    df['Rolling_Min_20'] = df['Close'].rolling(window=20, min_periods=1).min()
    df['Close_Range_Position'] = (df['Close'] - df['Rolling_Min_20']) / (df['Rolling_Max_20'] - df['Rolling_Min_20'] + eps)
    df['Donchian_Width'] = df['Rolling_Max_20'] - df['Rolling_Min_20']

    # Cyclic features
    if 'Date' in df.columns:
        df['day_of_week'] = df['Date'].dt.dayofweek
        df['dow_sin'] = np.sin(2 * np.pi * df['day_of_week'] / 7.0)
        df['dow_cos'] = np.cos(2 * np.pi * df['day_of_week'] / 7.0)
        df['month'] = df['Date'].dt.month
        df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12.0)
        df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12.0)

    # Cleanup intermediate columns that aren't needed
    df.drop(columns=['prev_close', 'TR'], inplace=True, errors='ignore')

    # Final: drop rows with NaNs from rolling computations
    df = df.dropna().reset_index(drop=True)
    return df