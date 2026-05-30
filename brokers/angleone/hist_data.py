from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from pyotp import TOTP
from datetime import datetime
import pandas as pd
import traceback
import os


class hist_data:

    INSTRUMENT_URL   = (
        "https://margincalculator.angelbroking.com"
        "/OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    INSTRUMENT_CACHE = "data/instrument_master.json"

    def __init__(self):
        self.angel_obj           = None
        self.angle_script_master = None
        self.angel_WS_Obj        = None

    # ── Login ──────────────────────────────────────────────────────────────
    def log_in(self):
        angel_secret = open("keys/angleonekeys", "r").read().split()

        self.angel_obj = SmartConnect(api_key=angel_secret[0])
        data = self.angel_obj.generateSession(
            angel_secret[2],
            angel_secret[3],
            TOTP(angel_secret[4]).now(),
        )

        angel_WS_token = self.angel_obj.getfeedToken()
        self.angel_WS_Obj = SmartWebSocketV2(
            data["data"]["jwtToken"],
            angel_secret[0],
            angel_secret[2],
            angel_WS_token,
        )

        # Cache instrument master — re-download only if > 1 day old
        if (
            not os.path.exists(self.INSTRUMENT_CACHE)
            or (datetime.now().timestamp()
                - os.path.getmtime(self.INSTRUMENT_CACHE)) > 86400
        ):
            print("  [hist_data] Downloading instrument master...")
            self.angle_script_master = pd.read_json(self.INSTRUMENT_URL)
            os.makedirs("data", exist_ok=True)
            self.angle_script_master.to_json(self.INSTRUMENT_CACHE)
        else:
            print("  [hist_data] Loading cached instrument master")
            self.angle_script_master = pd.read_json(self.INSTRUMENT_CACHE)

    # ── Equity historical data ─────────────────────────────────────────────
    def get_eq_data(self, script_name, script_code, from_date, to_date, interval):
        params = {
            "exchange":    "NSE",
            "symboltoken": script_code,
            "interval":    interval,
            "fromdate":    from_date,
            "todate":      to_date,
        }

        try:
            response = self.angel_obj.getCandleData(params)
        except Exception as e:
            print(f"  [hist_data] API call failed for {script_name}: {e}")
            traceback.print_exc()
            return None

        if not response or not response.get("status"):
            msg = response.get("message", "unknown error") if response else "no response"
            print(f"  [hist_data] AngelOne error for {script_name}: {msg}")
            return None

        data = response.get("data")
        if not data:
            print(f"  [hist_data] No candle data returned for {script_name}")
            return None

        return self.angle_parsedata(data)

    # ── FNO historical data ────────────────────────────────────────────────
    def get_FNO_data(self, script_name, script_code, from_date, to_date, interval):
        params = {
            "exchange":    "NFO",
            "symboltoken": script_code,
            "interval":    interval,
            "fromdate":    from_date,
            "todate":      to_date,
        }

        try:
            response = self.angel_obj.getCandleData(params)
        except Exception as e:
            print(f"  [hist_data] FNO API call failed for {script_name}: {e}")
            return None

        if not response or not response.get("status"):
            msg = response.get("message", "unknown") if response else "no response"
            print(f"  [hist_data] FNO error for {script_name}: {msg}")
            return None

        data = response.get("data")
        if not data:
            return None

        return self.angle_parsedata(data)

    # ── Parse candle list into DataFrame ──────────────────────────────────
    def angle_parsedata(self, data):
        if not data:
            return None

        rows = []
        for candle in data:
            rows.append({
                "Date":   candle[0].split("+")[0].replace("T", " "),
                "Open":   candle[1],
                "High":   candle[2],
                "Low":    candle[3],
                "Close":  candle[4],
                "Volume": candle[5],
            })

        df = pd.DataFrame(rows)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    # ── Token lookup ───────────────────────────────────────────────────────
    def token_lookup(self, ticker: str) -> str:
        eq_ticker = f"{ticker}-EQ"
        result = self.angle_script_master.loc[
            (self.angle_script_master["name"]     == ticker)
            & (self.angle_script_master["exch_seg"] == "NSE")
            & (self.angle_script_master["symbol"]   == eq_ticker),
            "token",
        ]
        if result.empty:
            raise ValueError(f"Token not found for {ticker}")
        return result.iloc[0]

    def token_lookup_options(self, ticker: str) -> str:
        result = self.angle_script_master.loc[
            self.angle_script_master["symbol"] == ticker, "token"
        ]
        if result.empty:
            raise ValueError(f"Options token not found for {ticker}")
        return result.iloc[0]

    # ── Market data (LTP / OI) ─────────────────────────────────────────────
    def get_ltp_data(self, script_code, exchange="NFO"):
        response = self.angel_obj.getMarketData("FULL", {exchange: [script_code]})
        return response["data"]["fetched"][0]["ltp"]

    def get_market_data(self, script_code, exchange="NFO"):
        response = self.angel_obj.getMarketData("FULL", {exchange: [script_code]})
        return response["data"]["fetched"][0]["opnInterest"]

    # ── Order placement ────────────────────────────────────────────────────
    def place_limit_order(self, ticker, buy_sell, price,
                          quantity, exchange="NSE",
                          product_type="DELIVERY"):
        params = {
            "variety":         "NORMAL",
            "tradingsymbol":   f"{ticker}-EQ",
            "symboltoken":     self.token_lookup(ticker),
            "transactiontype": buy_sell,
            "exchange":        exchange,
            "ordertype":       "LIMIT",
            "producttype":     product_type,
            "duration":        "DAY",
            "price":           price,
            "quantity":        quantity,
        }
        return self.angel_obj.placeOrder(params)

    def place_stoploss_order(
            self,
            ticker,
            buy_sell,
            trigger_price,
            quantity,
            exchange="NSE",
            product_type="DELIVERY"
    ):
        params = {
            "variety": "STOPLOSS",
            "tradingsymbol": f"{ticker}-EQ",
            "symboltoken": self.token_lookup(ticker),
            "transactiontype": buy_sell,
            "exchange": exchange,
            "ordertype": "STOPLOSS_LIMIT",
            "producttype": product_type,
            "duration": "DAY",
            "price": trigger_price,
            "triggerprice": trigger_price,
            "quantity": quantity,
        }

        return self.angel_obj.placeOrder(params)