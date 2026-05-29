import os
import time
import traceback
from datetime import datetime
from brokers.angleone.hist_data import hist_data


class AngelOneExecutor:
    """
    Wraps AngelOne order placement.
    DRY_RUN=True  → logs order details, never calls the API
    DRY_RUN=False → places real orders via AngelOne SmartAPI
    """

    # AngelOne error codes we handle explicitly
    ERROR_CAUTIONARY   = "AB4036"
    ERROR_UNREGISTERED = "AG7002"

    def __init__(self, client: hist_data):
        self.client  = client
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        # DELIVERY = CNC, holds overnight — correct for swing/positional
        # INTRADAY = MIS, auto-squares at 3:20 PM — wrong for our strategies
        self.product_type = os.getenv("PRODUCT_TYPE", "DELIVERY")

        if self.dry_run:
            print("  [executor] DRY RUN MODE — no real orders will be placed")
        else:
            print(f"  [executor] LIVE MODE — product type: {self.product_type}")

    # ── Main entry point ───────────────────────────────────────────────
    def execute(
        self,
        symbol:      str,
        signal:      str,        # "BUY" or "SELL"
        close_price: float,
        lot_size:    int   = 1,
        sl_pct:      float = 0.03,
    ) -> dict:
        """
        Places a LIMIT order at close_price.
        Returns order result dict with status:
            DRY_RUN    → dry run, no API call
            PLACED     → entry order confirmed by broker
            CAUTIONARY → rejected by exchange (AB4036) — symbol flagged
            FAILED     → other API error
        """
        transaction = "BUY" if signal == "BUY" else "SELL"
        sl_price    = round(close_price * (1 - sl_pct), 2) if signal == "BUY" \
                      else round(close_price * (1 + sl_pct), 2)

        order_details = {
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":       symbol,
            "transaction":  transaction,
            "price":        close_price,
            "stop_loss":    sl_price,
            "quantity":     lot_size,
            "sl_pct":       sl_pct,
            "product_type": self.product_type,
            "dry_run":      self.dry_run,
            "order_id":     None,
            "sl_order_id":  None,
            "status":       None,
            "error":        None,
        }

        # ── Dry run — no broker call
        if self.dry_run:
            order_details["status"]   = "DRY_RUN"
            order_details["order_id"] = f"DRY-{symbol}-{datetime.now().strftime('%H%M%S')}"
            self._print_order(order_details)
            return order_details

        # ── Live: place entry order
        try:
            response = self.client.place_limit_order(
                ticker       = symbol,
                buy_sell     = transaction,
                price        = close_price,
                quantity     = lot_size,
                product_type = self.product_type,   # DELIVERY not INTRADAY
            )

            # ── Check entry order response
            status, error_code, error_msg = self._parse_response(response)
            if not status:
                return self._handle_error(order_details, error_code, error_msg)

            order_details["order_id"] = response
            order_details["status"]   = "PLACED"
            self._print_order(order_details)

        except Exception as e:
            order_details["status"] = "FAILED"
            order_details["error"]  = str(e)
            print(f"  [executor] Order failed for {symbol}: {e}")
            traceback.print_exc()
            return order_details

        # ── Live: place SL order immediately after entry
        time.sleep(0.5)
        sl_transaction = "SELL" if signal == "BUY" else "BUY"
        try:
            sl_response = self.client.place_limit_order(
                ticker       = symbol,
                buy_sell     = sl_transaction,
                price        = sl_price,
                quantity     = lot_size,
                product_type = self.product_type,
            )

            sl_status, sl_err_code, sl_err_msg = self._parse_response(sl_response)
            if not sl_status:
                # Entry placed but SL failed — critical, log clearly
                print(f"  [executor] ! SL order FAILED for {symbol}: {sl_err_msg}")
                print(f"  [executor] ! Entry is LIVE but NO SL — monitor manually!")
                order_details["sl_order_id"] = None
                order_details["error"]       = f"SL failed: {sl_err_msg}"
            else:
                order_details["sl_order_id"] = sl_response
                print(f"  [executor] SL order placed: {sl_response} @ {sl_price}")

        except Exception as e:
            print(f"  [executor] ! SL order exception for {symbol}: {e}")
            order_details["error"] = f"SL exception: {e}"

        return order_details

    # ── Response parsing ───────────────────────────────────────────────
    def _parse_response(self, response) -> tuple[bool, str, str]:
        """
        Returns (success, error_code, error_message).
        AngelOne returns:
            - A string (order ID) on success
            - A dict with status=False on failure
            - None on network error
        """
        if response is None:
            return False, "", "Empty response from AngelOne"

        if isinstance(response, str) and len(response) > 0:
            return True, "", ""  # order ID string = success

        if isinstance(response, dict):
            success    = response.get("success", response.get("status", False))
            error_code = response.get("errorCode", response.get("errorcode", ""))
            error_msg  = response.get("message", "Unknown error")
            return bool(success), str(error_code), error_msg

        return False, "", f"Unexpected response type: {type(response)}"

    def _handle_error(self, order_details: dict,
                      error_code: str, error_msg: str) -> dict:
        symbol = order_details["symbol"]

        if error_code == self.ERROR_CAUTIONARY:
            order_details["status"] = "CAUTIONARY"
            order_details["error"]  = error_msg
            print(f"  [executor] {symbol} on cautionary list (AB4036) — flagging")
            self._flag_cautionary(symbol, error_msg)

        elif error_code == self.ERROR_UNREGISTERED:
            order_details["status"] = "FAILED"
            order_details["error"]  = error_msg
            print(f"  [executor] IP not whitelisted (AG7002) — register IP in AngelOne dashboard")

        else:
            order_details["status"] = "FAILED"
            order_details["error"]  = f"[{error_code}] {error_msg}"
            print(f"  [executor] Order failed ({error_code}): {error_msg}")

        return order_details

    def _flag_cautionary(self, symbol: str, reason: str):
        """Persist cautionary flag to DB so future runs skip this symbol."""
        try:
            import sqlite3
            db_path = os.getenv("DB_PATH", "data/signals.db")
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS cautionary_symbols (
                        symbol     TEXT PRIMARY KEY,
                        reason     TEXT,
                        flagged_at TEXT
                    )
                """)
                conn.execute("""
                    INSERT OR IGNORE INTO cautionary_symbols
                        (symbol, reason, flagged_at)
                    VALUES (?, ?, ?)
                """, [symbol, reason,
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        except Exception as e:
            print(f"  [executor] Could not flag cautionary {symbol}: {e}")

    # ── Print ──────────────────────────────────────────────────────────
    def _print_order(self, o: dict):
        tag = "DRY RUN" if o["dry_run"] else "LIVE"
        print(f"\n  [{tag}] Order Details")
        print(f"    Symbol      : {o['symbol']}")
        print(f"    Transaction : {o['transaction']}")
        print(f"    Product     : {o['product_type']}")
        print(f"    Price       : Rs{o['price']:.2f}")
        print(f"    Stop Loss   : Rs{o['stop_loss']:.2f} ({o['sl_pct']*100:.1f}%)")
        print(f"    Quantity    : {o['quantity']}")
        print(f"    Order ID    : {o['order_id']}")
        print(f"    Status      : {o['status']}\n")