# import numpy as np
# import tensorflow as tf
# import joblib
# from PIL import Image
# import io
# from agents.strategies.base_strategy import BaseStrategy
# from shared.models import StrategySignal


# class MLModelAgent(BaseStrategy):
#     """
#     Hybrid CNN + MLP + Conv1D model agent.
#     Requires pre-trained model and scalers in models/ directory.
#     """

#     SEQ_LEN   = 51
#     THRESHOLD = 0.9995

#     MLP_FEATURES = [
#         'Price_EMA_21_Ratio', 'Price_EMA_51_Ratio', 'EMA_21_minus_EMA_51',
#         'MACD', 'MACD_signal', 'MACD_Histogram', 'MACD_Cross_Flag',
#         'RSI', 'Williams_%R', 'ADX', 'Aroon_Oscillator',
#         'CCI', 'MFI', 'CMF', 'ATR_pct', 'BB_WIDTH', 'Donchian_Width',
#         'Close_Range_Position', 'OBV_pct_change_3', 'Vol_Ratio',
#         'SMA_50_dist', 'SMA_200_dist', 'Return_1', 'Return_3', 'Return_7',
#         'Volatility_21', 'Day_Trend', 'dow_sin', 'dow_cos',
#         'month_sin', 'month_cos'
#     ]

#     CONV1D_FEATURES = [
#         'Open', 'High', 'Low', 'Close',
#         'log_return_1', 'pct_return_1', 'pct_return_3',
#         'EMA_21', 'EMA_51', 'SMA_50', 'SMA_200',
#         'MACD', 'MACD_signal', 'MACD_Histogram',
#         '%K_L', '%D_L', '%K_S', '%D_S',
#         'RSI', 'ADX', 'CCI', 'MFI',
#         'ATR', 'ATR_pct', 'BB_WIDTH', 'BB_POSITION',
#         'Keltner_upper', 'Keltner_lower',
#         'VWAP', 'Volume', 'OBV', 'OBV_EMA_21', 'Vol_Ratio',
#         'Momentum_10', 'Williams_%R', 'Parabolic_SAR',
#         'Body', 'Wick', 'Body_Wick_Ratio',
#         'dow_sin', 'dow_cos', 'month_sin', 'month_cos'
#     ]

#     # Class-level — loaded once, shared across all evaluate() calls
#     _model        = None
#     _mlp_scaler   = None
#     _conv1d_scaler = None

#     def __init__(self):
#         self._load_model()

#     def _load_model(self):
#         if MLModelAgent._model is not None:
#             return
#         try:
#             import chandu_util as _cu
#             self._chandu_util = _cu
#             MLModelAgent._model         = tf.keras.models.load_model("models/best_hybrid_model.h5")
#             MLModelAgent._mlp_scaler    = joblib.load("models/mlp_scaler.pkl")
#             MLModelAgent._conv1d_scaler = joblib.load("models/conv1d_scaler.pkl")
#             print("  [MLModelAgent] Model + scalers loaded ✓")
#         except Exception as e:
#             print(f"  [MLModelAgent] ⚠️  Could not load model: {e} — agent disabled")
#             MLModelAgent._model = None

#     def evaluate(self, df, symbol: str) -> StrategySignal | None:
#         if MLModelAgent._model is None:
#             return None    # gracefully disabled if model not found
#         if df is None or len(df) < self.SEQ_LEN:
#             return None

#         df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

#         try:
#             # ── CNN image input
#             df_recent = df.iloc[-self.SEQ_LEN:].copy()
#             img_bytes  = self._chandu_util.save_trade_snapshot_V3(
#                 df_recent, self.SEQ_LEN, "", True
#             )
#             img       = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((224, 224))
#             cnn_input = tf.convert_to_tensor(
#                 np.expand_dims(np.array(img, dtype=np.float32) / 255.0, axis=0),
#                 dtype=tf.float32
#             )

#             # ── MLP input
#             last_row  = df.iloc[-1]
#             mlp_vals  = np.array(
#                 [float(last_row.get(f, 0)) for f in self.MLP_FEATURES],
#                 dtype=np.float32
#             ).reshape(1, -1)
#             mlp_input = tf.convert_to_tensor(
#                 MLModelAgent._mlp_scaler.transform(mlp_vals), dtype=tf.float32
#             )

#             # ── Conv1D input
#             conv_seq  = df[self.CONV1D_FEATURES].iloc[-self.SEQ_LEN:].values.astype(np.float32)
#             conv_seq  = MLModelAgent._conv1d_scaler.transform(conv_seq)
#             conv_input = tf.convert_to_tensor(
#                 np.expand_dims(conv_seq, axis=0), dtype=tf.float32
#             )

#             # ── Predict
#             pred = MLModelAgent._model.predict(
#                 {"cnn_input": cnn_input,
#                  "mlp_input": mlp_input,
#                  "conv1d_input": conv_input},
#                 verbose=0
#             )
#             prob = float(pred.squeeze())

#         except Exception as e:
#             print(f"  [MLModelAgent] Inference failed for {symbol}: {e}")
#             return None

#         if prob < self.THRESHOLD:
#             return None

#         return StrategySignal(
#             strategy="MLModel",
#             symbol=symbol,
#             signal="BUY",
#             confidence=round(prob, 4),
#             reasoning=[
#                 f"Hybrid CNN+MLP+Conv1D model probability: {prob:.4f}",
#                 f"Threshold: {self.THRESHOLD}",
#                 f"Features: {len(self.MLP_FEATURES)} MLP + {len(self.CONV1D_FEATURES)} Conv1D + CNN chart image",
#             ],
#             metadata={
#                 "probability": prob,
#                 "threshold":   self.THRESHOLD,
#                 "seq_len":     self.SEQ_LEN,
#             },
#         )















"""
agents/strategies/ml_model_agent.py

Hybrid CNN + MLP + Conv1D model agent.
Requires pre-trained model files in models/:
    - best_hybrid_model.h5
    - mlp_scaler.pkl
    - conv1d_scaler.pkl
"""

import io
import numpy as np
from agents.strategies.base_strategy import BaseStrategy
from shared.models import StrategySignal
from feature_engineering.chart_snapshot import save_trade_snapshot_V3


class MLModelAgent(BaseStrategy):

    SEQ_LEN   = 51
    THRESHOLD = 0.9995

    MLP_FEATURES = [
        'Price_EMA_21_Ratio', 'Price_EMA_51_Ratio', 'EMA_21_minus_EMA_51',
        'MACD', 'MACD_signal', 'MACD_Histogram', 'MACD_Cross_Flag',
        'RSI', 'Williams_%R', 'ADX', 'Aroon_Oscillator',
        'CCI', 'MFI', 'CMF', 'ATR_pct', 'BB_WIDTH', 'Donchian_Width',
        'Close_Range_Position', 'OBV_pct_change_3', 'Vol_Ratio',
        'SMA_50_dist', 'SMA_200_dist', 'Return_1', 'Return_3', 'Return_7',
        'Volatility_21', 'Day_Trend', 'dow_sin', 'dow_cos',
        'month_sin', 'month_cos',
    ]

    CONV1D_FEATURES = [
        'Open', 'High', 'Low', 'Close',
        'log_return_1', 'pct_return_1', 'pct_return_3',
        'EMA_21', 'EMA_51', 'SMA_50', 'SMA_200',
        'MACD', 'MACD_signal', 'MACD_Histogram',
        '%K_L', '%D_L', '%K_S', '%D_S',
        'RSI', 'ADX', 'CCI', 'MFI',
        'ATR', 'ATR_pct', 'BB_WIDTH', 'BB_POSITION',
        'Keltner_upper', 'Keltner_lower',
        'VWAP', 'Volume', 'OBV', 'OBV_EMA_21', 'Vol_Ratio',
        'Momentum_10', 'Williams_%R', 'Parabolic_SAR',
        'Body', 'Wick', 'Body_Wick_Ratio',
        'dow_sin', 'dow_cos', 'month_sin', 'month_cos',
    ]

    # Class-level — loaded once, shared across all evaluate() calls
    _model         = None
    _mlp_scaler    = None
    _conv1d_scaler = None

    def __init__(self):
        self._load_model()

    def _load_model(self):
        if MLModelAgent._model is not None:
            return
        try:
            import tensorflow as tf
            import joblib
            MLModelAgent._tf            = tf
            MLModelAgent._model         = tf.keras.models.load_model(
                                              "models/best_hybrid_model.h5")
            MLModelAgent._mlp_scaler    = joblib.load("models/mlp_scaler.pkl")
            MLModelAgent._conv1d_scaler = joblib.load("models/conv1d_scaler.pkl")
            print("  [MLModelAgent] Model + scalers loaded ✓")
        except FileNotFoundError as e:
            print(f"  [MLModelAgent] ⚠️  Model files not found: {e}")
            print(f"  [MLModelAgent]     Copy to models/: best_hybrid_model.h5, "
                  f"mlp_scaler.pkl, conv1d_scaler.pkl")
            MLModelAgent._model = None
        except Exception as e:
            print(f"  [MLModelAgent] ⚠️  Could not load model: {e} — agent disabled")
            MLModelAgent._model = None

    def evaluate(self, df, symbol: str) -> StrategySignal | None:
        if MLModelAgent._model is None:
            return None
        if df is None or len(df) < self.SEQ_LEN:
            return None

        tf = MLModelAgent._tf
        df = df.replace([float("inf"), float("-inf")], float("nan")).fillna(0)

        try:
            # ── CNN input
            # idx = last row index; V3 slices df[idx-50 : idx+1] internally
            idx       = len(df) - 1
            img_bytes = save_trade_snapshot_V3(
                df,
                idx=idx,
                filename=None,
                buffer_needed=True,        # ← returns PNG bytes
            )
            from PIL import Image
            img       = (Image.open(io.BytesIO(img_bytes))
                              .convert("RGB")
                              .resize((224, 224)))
            cnn_input = tf.convert_to_tensor(
                np.expand_dims(np.array(img, dtype=np.float32) / 255.0, axis=0),
                dtype=tf.float32,
            )

            # ── MLP input — single last row, 31 features
            last_row  = df.iloc[-1]
            mlp_vals  = np.array(
                [float(last_row.get(f, 0)) for f in self.MLP_FEATURES],
                dtype=np.float32,
            ).reshape(1, -1)
            mlp_input = tf.convert_to_tensor(
                MLModelAgent._mlp_scaler.transform(mlp_vals),
                dtype=tf.float32,
            )

            # ── Conv1D input — last SEQ_LEN rows, available features only
            available  = [c for c in self.CONV1D_FEATURES if c in df.columns]
            conv_seq   = df[available].iloc[-self.SEQ_LEN:].values.astype(np.float32)
            conv_seq   = MLModelAgent._conv1d_scaler.transform(conv_seq)
            conv_input = tf.convert_to_tensor(
                np.expand_dims(conv_seq, axis=0), dtype=tf.float32,
            )

            # ── Inference
            pred = MLModelAgent._model.predict(
                {
                    "cnn_input":    cnn_input,
                    "mlp_input":    mlp_input,
                    "conv1d_input": conv_input,
                },
                verbose=0,
            )
            prob = float(pred.squeeze())

        except Exception as e:
            print(f"  [MLModelAgent] Inference error for {symbol}: {e}")
            return None

        if prob < self.THRESHOLD:
            return None

        return StrategySignal(
            strategy="MLModel",
            symbol=symbol,
            signal="BUY",
            confidence=round(prob, 4),
            reasoning=[
                f"Hybrid CNN+MLP+Conv1D probability: {prob:.4f} "
                f"(threshold {self.THRESHOLD})",
                f"CNN: 2×3 chart — candles, MACD, RSI, ADX, Volume, Stoch",
                f"MLP: {len(self.MLP_FEATURES)} indicator features",
                f"Conv1D: {len(available)}/{len(self.CONV1D_FEATURES)} "
                f"sequence features × {self.SEQ_LEN} bars",
            ],
            metadata={
                "probability": prob,
                "threshold":   self.THRESHOLD,
                "seq_len":     self.SEQ_LEN,
            },
        )