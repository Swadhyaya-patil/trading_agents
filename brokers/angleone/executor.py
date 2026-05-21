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

    def __init__(self, client: hist_data):
        self.client  = client
        self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"

        if self.dry_run:
            print("  [executor] ⚠️  DRY RUN MODE — no real orders will be placed")

    # ── Main entry point ───────────────────────────────────────────────
    def execute(
        self,
        symbol:      str,
        signal:      str,         # "BUY" or "SELL"
        close_price: float,
        lot_size:    int   = 1,
        sl_pct:      float = 0.03,   # 3% stop loss
    ) -> dict:
        """
        Places a LIMIT order at close_price.
        Returns order result dict.
        """
        transaction = "BUY" if signal == "BUY" else "SELL"
        sl_price    = round(close_price * (1 - sl_pct), 2) if signal == "BUY" \
                      else round(close_price * (1 + sl_pct), 2)

        order_details = {
            "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "symbol":          symbol,
            "transaction":     transaction,
            "price":           close_price,
            "stop_loss":       sl_price,
            "quantity":        lot_size,
            "sl_pct":          sl_pct,
            "dry_run":         self.dry_run,
            "order_id":        None,
            "status":          None,
            "error":           None,
        }

        if self.dry_run:
            order_details["status"]   = "DRY_RUN"
            order_details["order_id"] = f"DRY-{symbol}-{datetime.now().strftime('%H%M%S')}"
            self._print_order(order_details)
            return order_details

        # ── Live order
        try:
            response = self.client.place_limit_order(
                ticker    = symbol,
                buy_sell  = transaction,
                price     = close_price,
                quantity  = lot_size,
            )
            order_details["order_id"] = response
            order_details["status"]   = "PLACED"
            self._print_order(order_details)

            # ── Place stop-loss order immediately after entry
            time.sleep(0.5)
            sl_transaction = "SELL" if signal == "BUY" else "BUY"
            sl_response = self.client.place_limit_order(
                ticker   = symbol,
                buy_sell = sl_transaction,
                price    = sl_price,
                quantity = lot_size,
            )
            order_details["sl_order_id"] = sl_response
            print(f"  [executor] SL order placed: {sl_response} @ {sl_price}")

        except Exception as e:
            order_details["status"] = "FAILED"
            order_details["error"]  = str(e)
            print(f"  [executor] ❌ Order failed for {symbol}: {e}")
            traceback.print_exc()

        return order_details

    def _print_order(self, o: dict):
        tag = "🧪 DRY RUN" if o["dry_run"] else "🟢 LIVE"
        print(f"\n  [{tag}] Order Details")
        print(f"    Symbol      : {o['symbol']}")
        print(f"    Transaction : {o['transaction']}")
        print(f"    Price       : ₹{o['price']:.2f}")
        print(f"    Stop Loss   : ₹{o['stop_loss']:.2f} ({o['sl_pct']*100:.1f}%)")
        print(f"    Quantity    : {o['quantity']}")
        print(f"    Order ID    : {o['order_id']}")
        print(f"    Status      : {o['status']}\n")