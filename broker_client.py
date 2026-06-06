# """
# broker_client.py  —  v8

# All AngelOne API interaction:
#   - FillResult     : immutable fill confirmation result
#   - TokenCache     : cached token lookup with TTL refresh (#9)
#   - BrokerState    : positions + orderbook with pending-SL guard (#1 #3 #4)
#   - SLPlacer       : STOPLOSS_LIMIT with correct trigger logic (#10)
#   - get_freeze_qty : NSE freeze quantity lookup (#6)

# v8 fixes applied here:
#   #3  get_active_sl_qty_total — also handles "complete" status to avoid
#       recreating SL after execution
#   #4  track all known SL order IDs per symbol to detect accumulation
#   #9  persistent per-instance SQLite-style connection NOT needed here
#       (DB is in order_manager.py); but TokenCache reuses objects
#   #10 get_freeze_qty signature fixed to take symbol string
# """

# import time
# from om_config import (
#     ORDERBOOK_INTERVAL_MINS, TOKEN_CACHE_TTL_MINS,
#     SL_TRIGGER_PCT, PRODUCT_TYPE, DRY_RUN,
#     FILL_CONFIRM_SECS, FILL_CONFIRM_RETRIES,
#     MAX_RISK_PCT,
# )
# from om_utils import log, safe_float, safe_int, round_to_tick, strip_eq, now


# # =========================================================
# # FILL RESULT
# # =========================================================

# class FillResult:
#     def __init__(self, filled: bool, qty: int = 0,
#                  avg_price: float = 0.0, order_id: str = ""):
#         self.filled    = filled
#         self.qty       = qty
#         self.avg_price = avg_price
#         self.order_id  = order_id

#     @classmethod
#     def unfilled(cls):
#         return cls(filled=False)

#     def __bool__(self):
#         return self.filled and self.qty > 0


# # =========================================================
# # TOKEN CACHE  (#9: one object, never recreated)
# # =========================================================

# class TokenCache:
#     """
#     Caches token_lookup() per symbol with TTL.
#     Instance is created ONCE in OrderManagerApp and shared.
#     """
#     def __init__(self, client):
#         self.client = client
#         self._cache = {}   # bare_symbol -> (token_str, cached_at)

#     def _is_stale(self, cached_at) -> bool:
#         return (now() - cached_at).total_seconds() / 60 > TOKEN_CACHE_TTL_MINS

#     def get(self, symbol: str) -> str:
#         bare  = strip_eq(symbol)
#         entry = self._cache.get(bare)
#         if entry is None or self._is_stale(entry[1]):
#             token = self.client.token_lookup(bare)
#             self._cache[bare] = (token, now())
#             return token
#         return entry[0]

#     def ltp(self, symbol: str, exchange="NSE") -> float:
#         return self.client.get_ltp_data(self.get(symbol), exchange=exchange)

#     def invalidate(self, symbol: str):
#         self._cache.pop(strip_eq(symbol), None)


# # =========================================================
# # BROKER STATE
# # =========================================================

# class BrokerState:

#     def __init__(self, client):
#         self.client          = client
#         self.positions_cache = {}   # bare_symbol -> dict
#         self.orderbook_cache = []
#         self._last_ob_time   = None
#         # FIX #1/#4: track symbols mid-SL-replace and all known SL order IDs
#         self._sl_replace_pending = set()
#         self._known_sl_order_ids = {}  # bare_symbol -> set of order_id strings

#     # ── Refresh ──────────────────────────────────────────────

#     def refresh(self):
#         self._refresh_positions()
#         self._refresh_orderbook_if_due()

#     def _refresh_positions(self):
#         try:
#             data = (self.client.angel_obj.position() or {}).get("data") or []
#             new_cache = {}
#             for p in data:
#                 qty = abs(int(safe_float(p.get("netqty", 0))))
#                 if qty <= 0:
#                     continue
#                 symbol = strip_eq(p.get("tradingsymbol", ""))
#                 new_cache[symbol] = {
#                     "symbol":    symbol,
#                     "qty":       qty,
#                     "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
#                     "avg_price": safe_float(p.get("averageprice")),
#                     "ltp":       safe_float(p.get("ltp")),
#                     "pnl":       safe_float(p.get("pnl")),
#                 }
#             # FIX #2: detect exits — log symbols that disappeared
#             closed = set(self.positions_cache.keys()) - set(new_cache.keys())
#             for sym in closed:
#                 old = self.positions_cache[sym]
#                 log(f"  [broker] Position CLOSED: {sym} "
#                     f"side={old['side']} qty={old['qty']} "
#                     f"last_ltp=Rs{old['ltp']:.2f}")
#                 # Clean up known SL order ID tracking
#                 self._known_sl_order_ids.pop(sym, None)

#             self.positions_cache = new_cache
#         except Exception as e:
#             log(f"Position refresh failed: {e}")

#     def _refresh_orderbook_if_due(self):
#         if self._last_ob_time:
#             if (now() - self._last_ob_time).total_seconds() / 60 \
#                     < ORDERBOOK_INTERVAL_MINS:
#                 return
#         self._do_orderbook_refresh()

#     def _do_orderbook_refresh(self):
#         try:
#             ob = self.client.angel_obj.orderBook()
#             self.orderbook_cache = (ob or {}).get("data") or []
#             self._last_ob_time   = now()
#         except Exception as e:
#             log(f"Orderbook refresh failed: {e}")

#     def force_orderbook_refresh(self):
#         self._last_ob_time = None
#         self._do_orderbook_refresh()

#     # ── SL replace pending guard  (#1 #6) ──────────────────────

#     def mark_sl_replace_pending(self, symbol: str):
#         self._sl_replace_pending.add(strip_eq(symbol))

#     def clear_sl_replace_pending(self, symbol: str):
#         self._sl_replace_pending.discard(strip_eq(symbol))

#     def is_sl_replace_pending(self, symbol: str) -> bool:
#         return strip_eq(symbol) in self._sl_replace_pending

#     # ── Known SL order ID tracking  (#4) ───────────────────────

#     def register_sl_order(self, symbol: str, sl_order_id: str):
#         """FIX #4: Track all SL order IDs placed for a symbol."""
#         bare = strip_eq(symbol)
#         if bare not in self._known_sl_order_ids:
#             self._known_sl_order_ids[bare] = set()
#         self._known_sl_order_ids[bare].add(sl_order_id)

#     def get_known_sl_count(self, symbol: str) -> int:
#         """FIX #4: How many SL orders have we placed for this symbol."""
#         return len(self._known_sl_order_ids.get(strip_eq(symbol), set()))

#     def cancel_all_known_sls(self, symbol: str, sl_placer) -> int:
#         """
#         FIX #4: Cancel ALL known SL orders for a symbol.
#         Returns count of cancelled orders.
#         Used when accumulation is detected.
#         """
#         bare      = strip_eq(symbol)
#         oids      = list(self._known_sl_order_ids.get(bare, set()))
#         cancelled = 0
#         for oid in oids:
#             if sl_placer.cancel(oid):
#                 cancelled += 1
#         self._known_sl_order_ids.pop(bare, None)
#         return cancelled

#     # ── Position helpers ─────────────────────────────────────

#     def inject_position(self, symbol, side, avg_price, qty):
#         """Called only after confirmed fill — actual price+qty."""
#         bare = strip_eq(symbol)
#         self.positions_cache[bare] = {
#             "symbol":    bare,
#             "qty":       qty,
#             "side":      side,
#             "avg_price": avg_price,
#             "ltp":       avg_price,
#             "pnl":       0.0,
#         }

#     def sync_position_from_broker(self, symbol: str):
#         """FIX #5: Correct injected position with live broker data."""
#         try:
#             data = (self.client.angel_obj.position() or {}).get("data") or []
#             bare = strip_eq(symbol)
#             for p in data:
#                 if strip_eq(p.get("tradingsymbol", "")) != bare:
#                     continue
#                 qty = abs(int(safe_float(p.get("netqty", 0))))
#                 if qty > 0:
#                     self.positions_cache[bare] = {
#                         "symbol":    bare,
#                         "qty":       qty,
#                         "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
#                         "avg_price": safe_float(p.get("averageprice")),
#                         "ltp":       safe_float(p.get("ltp")),
#                         "pnl":       safe_float(p.get("pnl")),
#                     }
#                     log(f"  [broker] Synced {bare}: "
#                         f"qty={qty} avg=Rs{self.positions_cache[bare]['avg_price']:.2f}")
#                 else:
#                     self.positions_cache.pop(bare, None)
#                 return
#         except Exception as e:
#             log(f"  [broker] Single-position sync failed {symbol}: {e}")

#     def remove_position(self, symbol):
#         self.positions_cache.pop(strip_eq(symbol), None)

#     def has_position(self, symbol) -> bool:
#         return strip_eq(symbol) in self.positions_cache

#     def get_positions(self) -> dict:
#         return self.positions_cache

#     def position_count(self) -> int:
#         return len(self.positions_cache)

#     # ── Orderbook helpers ─────────────────────────────────────

#     def get_order_from_book(self, order_id) -> dict | None:
#         for o in self.orderbook_cache:
#             if o.get("orderid") == order_id:
#                 return o
#         return None

#     def get_fill_result(self, order_id) -> FillResult:
#         o = self.get_order_from_book(order_id)
#         if o is None:
#             return FillResult.unfilled()
#         filled_qty = safe_int(o.get("filledshares", 0))
#         avg_price  = safe_float(o.get("averageprice", 0.0))
#         if filled_qty > 0 and avg_price > 0:
#             return FillResult(True, filled_qty, avg_price, order_id)
#         return FillResult.unfilled()

#     def get_active_sl_qty_total(self, symbol, sl_side) -> int:
#         """
#         FIX #3: Sum ALL active SL orders for symbol+side.
#         Excludes "complete" orders — an executed SL means the position
#         is closing, not that SL coverage is missing.
#         """
#         sl_side_upper = sl_side.upper()
#         ACTIVE_STATUSES = {
#             "open", "trigger pending",
#             "amo req received", "modified",
#             "put order req received",
#         }
#         # FIX #3: statuses that mean the SL already fired
#         EXECUTED_STATUSES = {"complete", "filled"}

#         total_qty      = 0
#         sl_just_fired  = False

#         for o in self.orderbook_cache:
#             if strip_eq(o.get("tradingsymbol", "")) != strip_eq(symbol):
#                 continue
#             if o.get("transactiontype", "").upper() != sl_side_upper:
#                 continue
#             if o.get("variety") not in ("STOPLOSS", "NORMAL"):
#                 continue
#             status = o.get("status", "").lower()
#             if status in ACTIVE_STATUSES:
#                 total_qty += safe_int(o.get("quantity", 0))
#             elif status in EXECUTED_STATUSES:
#                 sl_just_fired = True

#         if sl_just_fired and total_qty == 0:
#             # SL executed — position is in process of closing
#             # Return broker_qty equivalent so reconcile doesn't re-place SL
#             pos = self.positions_cache.get(strip_eq(symbol), {})
#             return pos.get("qty", 0)   # signals "fully covered, SL fired"

#         return total_qty

#     def has_active_sl_for(self, symbol, sl_side) -> bool:
#         return self.get_active_sl_qty_total(symbol, sl_side) > 0


# # =========================================================
# # NSE FREEZE QUANTITY  (#10: takes symbol string)
# # =========================================================

# _FREEZE_QTY: dict[str, int] = {
#     # Add per-symbol overrides here if needed
#     # "RELIANCE": 250,
#     "DEFAULT": 5000,
# }

# def get_freeze_qty(symbol: str) -> int:
#     """
#     FIX #10: Takes bare symbol string, not price.
#     Returns NSE freeze quantity for the symbol.
#     Update _FREEZE_QTY dict or maintain a CSV for accuracy.
#     """
#     return _FREEZE_QTY.get(strip_eq(symbol), _FREEZE_QTY["DEFAULT"])


# # =========================================================
# # SL ORDER PLACER
# # =========================================================

# class SLPlacer:
#     """
#     SELL SL (protecting BUY):  trigger ABOVE limit
#     BUY  SL (protecting SELL): trigger BELOW limit
#     """

#     def __init__(self, client, tokens):
#         self.client = client
#         self.tokens = tokens

#     def place(self, symbol, side, sl_price, qty,
#               broker_state=None) -> str | None:
#         """
#         FIX #10: symbol is always a string.
#         FIX #6: qty capped to freeze quantity.
#         FIX #4: registers placed order ID in broker_state.
#         """
#         # FIX #6 + #10: freeze qty uses symbol string
#         freeze_qty = get_freeze_qty(symbol)
#         if qty > freeze_qty:
#             log(f"  ! {symbol}: qty={qty} capped to freeze_qty={freeze_qty}")
#             qty = freeze_qty

#         sl_side = "SELL" if side == "BUY" else "BUY"
#         trigger_price = round_to_tick(
#             sl_price * (1 + SL_TRIGGER_PCT) if sl_side == "SELL"
#             else sl_price * (1 - SL_TRIGGER_PCT)
#         )

#         if DRY_RUN:
#             oid = f"DRYSL_{symbol}_{now().strftime('%H%M%S')}"
#             log(f"  [DRY] SL: {sl_side} {symbol} qty={qty} "
#                 f"trigger=Rs{trigger_price:.2f} limit=Rs{sl_price:.2f}")
#             if broker_state:
#                 broker_state.register_sl_order(symbol, oid)
#             return oid

#         params = {
#             "variety":         "STOPLOSS",
#             "tradingsymbol":   f"{symbol}-EQ",
#             "symboltoken":     self.tokens.get(symbol),
#             "transactiontype": sl_side,
#             "exchange":        "NSE",
#             "ordertype":       "STOPLOSS_LIMIT",
#             "producttype":     PRODUCT_TYPE,
#             "duration":        "DAY",
#             "price":           sl_price,
#             "triggerprice":    trigger_price,
#             "quantity":        qty,
#         }
#         try:
#             response = self.client.angel_obj.placeOrder(params)
#             if isinstance(response, dict):
#                 success = response.get("success", response.get("status", False))
#                 if not success:
#                     log(f"  ! SL rejected {symbol}: {response.get('message','?')}")
#                     return None
#                 oid = response.get("data", {}).get("orderid")
#             elif isinstance(response, str) and response:
#                 oid = response
#             else:
#                 return None

#             # FIX #4: register in broker_state
#             if oid and broker_state:
#                 broker_state.register_sl_order(symbol, oid)
#             return oid

#         except Exception as e:
#             log(f"  ! SL exception {symbol}: {e}")
#             return None

#     def cancel(self, sl_order_id) -> bool:
#         if DRY_RUN or not sl_order_id:
#             return True
#         try:
#             self.client.angel_obj.cancelOrder(sl_order_id, variety="STOPLOSS")
#             return True
#         except Exception as e:
#             log(f"  ! Cancel failed {sl_order_id}: {e}")
#             return False

#     def wait_for_fill(self, broker_state, symbol, order_id) -> FillResult:
#         for attempt in range(FILL_CONFIRM_RETRIES):
#             time.sleep(FILL_CONFIRM_SECS)
#             broker_state.force_orderbook_refresh()
#             fill = broker_state.get_fill_result(order_id)
#             if fill:
#                 log(f"  {symbol}: fill qty={fill.qty} @ Rs{fill.avg_price:.2f} "
#                     f"(attempt {attempt+1}/{FILL_CONFIRM_RETRIES})")
#                 return fill
#             log(f"  {symbol}: fill pending ({attempt+1}/{FILL_CONFIRM_RETRIES})")
#         return FillResult.unfilled()









# """
# broker_client.py  —  v8

# All AngelOne API interaction:
#   - FillResult     : immutable fill confirmation result
#   - TokenCache     : cached token lookup with TTL refresh (#9)
#   - BrokerState    : positions + orderbook with pending-SL guard (#1 #3 #4)
#   - SLPlacer       : STOPLOSS_LIMIT with correct trigger logic (#10)
#   - get_freeze_qty : NSE freeze quantity lookup (#6)

# v8 fixes applied here:
#   #3  get_active_sl_qty_total — also handles "complete" status to avoid
#       recreating SL after execution
#   #4  track all known SL order IDs per symbol to detect accumulation
#   #9  persistent per-instance SQLite-style connection NOT needed here
#       (DB is in order_manager.py); but TokenCache reuses objects
#   #10 get_freeze_qty signature fixed to take symbol string
# """

# import time
# from om_config import (
#     ORDERBOOK_INTERVAL_MINS, TOKEN_CACHE_TTL_MINS,
#     SL_TRIGGER_PCT, PRODUCT_TYPE, DRY_RUN,
#     FILL_CONFIRM_SECS, FILL_CONFIRM_RETRIES,
#     MAX_RISK_PCT,
# )
# from om_utils import log, safe_float, safe_int, round_to_tick, strip_eq, now


# # =========================================================
# # FILL RESULT
# # =========================================================

# class FillResult:
#     def __init__(self, filled: bool, qty: int = 0,
#                  avg_price: float = 0.0, order_id: str = ""):
#         self.filled    = filled
#         self.qty       = qty
#         self.avg_price = avg_price
#         self.order_id  = order_id

#     @classmethod
#     def unfilled(cls):
#         return cls(filled=False)

#     def __bool__(self):
#         return self.filled and self.qty > 0


# # =========================================================
# # TOKEN CACHE  (#9: one object, never recreated)
# # =========================================================

# class TokenCache:
#     """
#     Caches token_lookup() per symbol with TTL.
#     Instance is created ONCE in OrderManagerApp and shared.
#     """
#     def __init__(self, client):
#         self.client = client
#         self._cache = {}   # bare_symbol -> (token_str, cached_at)

#     def _is_stale(self, cached_at) -> bool:
#         return (now() - cached_at).total_seconds() / 60 > TOKEN_CACHE_TTL_MINS

#     def get(self, symbol: str) -> str:
#         bare  = strip_eq(symbol)
#         entry = self._cache.get(bare)
#         if entry is None or self._is_stale(entry[1]):
#             token = self.client.token_lookup(bare)
#             self._cache[bare] = (token, now())
#             return token
#         return entry[0]

#     def ltp(self, symbol: str, exchange="NSE") -> float:
#         return self.client.get_ltp_data(self.get(symbol), exchange=exchange)

#     def invalidate(self, symbol: str):
#         self._cache.pop(strip_eq(symbol), None)


# # =========================================================
# # BROKER STATE
# # =========================================================

# class BrokerState:

#     def __init__(self, client):
#         self.client          = client
#         self.positions_cache = {}   # bare_symbol -> dict
#         self.orderbook_cache = []
#         self._last_ob_time   = None
#         # FIX #1/#4: track symbols mid-SL-replace and all known SL order IDs
#         self._sl_replace_pending = set()
#         self._known_sl_order_ids = {}  # bare_symbol -> set of order_id strings

#     # ── Refresh ──────────────────────────────────────────────

#     def refresh(self):
#         self._refresh_positions()
#         self._refresh_orderbook_if_due()

#     def _refresh_positions(self):
#         try:
#             data = (self.client.angel_obj.position() or {}).get("data") or []
#             new_cache = {}
#             for p in data:
#                 qty = abs(int(safe_float(p.get("netqty", 0))))
#                 if qty <= 0:
#                     continue
#                 symbol = strip_eq(p.get("tradingsymbol", ""))
#                 new_cache[symbol] = {
#                     "symbol":    symbol,
#                     "qty":       qty,
#                     "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
#                     "avg_price": safe_float(p.get("averageprice")),
#                     "ltp":       safe_float(p.get("ltp")),
#                     "pnl":       safe_float(p.get("pnl")),
#                 }
#             # FIX #2: detect exits — log symbols that disappeared
#             closed = set(self.positions_cache.keys()) - set(new_cache.keys())
#             for sym in closed:
#                 old = self.positions_cache[sym]
#                 log(f"  [broker] Position CLOSED: {sym} "
#                     f"side={old['side']} qty={old['qty']} "
#                     f"last_ltp=Rs{old['ltp']:.2f}")
#                 # Clean up known SL order ID tracking
#                 self._known_sl_order_ids.pop(sym, None)

#             self.positions_cache = new_cache
#         except Exception as e:
#             log(f"Position refresh failed: {e}")

#     def _refresh_orderbook_if_due(self):
#         if self._last_ob_time:
#             if (now() - self._last_ob_time).total_seconds() / 60 \
#                     < ORDERBOOK_INTERVAL_MINS:
#                 return
#         self._do_orderbook_refresh()

#     def _do_orderbook_refresh(self):
#         try:
#             ob = self.client.angel_obj.orderBook()
#             self.orderbook_cache = (ob or {}).get("data") or []
#             self._last_ob_time   = now()
#         except Exception as e:
#             log(f"Orderbook refresh failed: {e}")

#     def force_orderbook_refresh(self):
#         self._last_ob_time = None
#         self._do_orderbook_refresh()

#     # ── SL replace pending guard  (#1 #6) ──────────────────────

#     def mark_sl_replace_pending(self, symbol: str):
#         self._sl_replace_pending.add(strip_eq(symbol))

#     def clear_sl_replace_pending(self, symbol: str):
#         self._sl_replace_pending.discard(strip_eq(symbol))

#     def is_sl_replace_pending(self, symbol: str) -> bool:
#         return strip_eq(symbol) in self._sl_replace_pending

#     # ── Known SL order ID tracking  (#4) ───────────────────────

#     def register_sl_order(self, symbol: str, sl_order_id: str):
#         """FIX #4: Track all SL order IDs placed for a symbol."""
#         bare = strip_eq(symbol)
#         if bare not in self._known_sl_order_ids:
#             self._known_sl_order_ids[bare] = set()
#         self._known_sl_order_ids[bare].add(sl_order_id)

#     def get_known_sl_count(self, symbol: str) -> int:
#         """FIX #4: How many SL orders have we placed for this symbol."""
#         return len(self._known_sl_order_ids.get(strip_eq(symbol), set()))

#     def cancel_all_known_sls(self, symbol: str, sl_placer) -> int:
#         """
#         FIX #4: Cancel ALL known SL orders for a symbol.
#         Returns count of cancelled orders.
#         Used when accumulation is detected.
#         """
#         bare      = strip_eq(symbol)
#         oids      = list(self._known_sl_order_ids.get(bare, set()))
#         cancelled = 0
#         for oid in oids:
#             if sl_placer.cancel(oid):
#                 cancelled += 1
#         self._known_sl_order_ids.pop(bare, None)
#         return cancelled

#     # ── Position helpers ─────────────────────────────────────

#     def inject_position(self, symbol, side, avg_price, qty):
#         """Called only after confirmed fill — actual price+qty."""
#         bare = strip_eq(symbol)
#         self.positions_cache[bare] = {
#             "symbol":    bare,
#             "qty":       qty,
#             "side":      side,
#             "avg_price": avg_price,
#             "ltp":       avg_price,
#             "pnl":       0.0,
#         }

#     def sync_position_from_broker(self, symbol: str):
#         """FIX #5: Correct injected position with live broker data."""
#         try:
#             data = (self.client.angel_obj.position() or {}).get("data") or []
#             bare = strip_eq(symbol)
#             for p in data:
#                 if strip_eq(p.get("tradingsymbol", "")) != bare:
#                     continue
#                 qty = abs(int(safe_float(p.get("netqty", 0))))
#                 if qty > 0:
#                     self.positions_cache[bare] = {
#                         "symbol":    bare,
#                         "qty":       qty,
#                         "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
#                         "avg_price": safe_float(p.get("averageprice")),
#                         "ltp":       safe_float(p.get("ltp")),
#                         "pnl":       safe_float(p.get("pnl")),
#                     }
#                     log(f"  [broker] Synced {bare}: "
#                         f"qty={qty} avg=Rs{self.positions_cache[bare]['avg_price']:.2f}")
#                 else:
#                     self.positions_cache.pop(bare, None)
#                 return
#         except Exception as e:
#             log(f"  [broker] Single-position sync failed {symbol}: {e}")

#     def remove_position(self, symbol):
#         self.positions_cache.pop(strip_eq(symbol), None)

#     def has_position(self, symbol) -> bool:
#         return strip_eq(symbol) in self.positions_cache

#     def get_positions(self) -> dict:
#         return self.positions_cache

#     def position_count(self) -> int:
#         return len(self.positions_cache)

#     # ── Orderbook helpers ─────────────────────────────────────

#     def get_order_from_book(self, order_id) -> dict | None:
#         for o in self.orderbook_cache:
#             if o.get("orderid") == order_id:
#                 return o
#         return None

#     def get_fill_result(self, order_id) -> FillResult:
#         o = self.get_order_from_book(order_id)
#         if o is None:
#             return FillResult.unfilled()
#         filled_qty = safe_int(o.get("filledshares", 0))
#         avg_price  = safe_float(o.get("averageprice", 0.0))
#         if filled_qty > 0 and avg_price > 0:
#             return FillResult(True, filled_qty, avg_price, order_id)
#         return FillResult.unfilled()

#     def get_active_sl_qty_total(self, symbol, sl_side) -> int:
#         """
#         FIX #3: Sum ALL active SL orders for symbol+side.
#         Excludes "complete" orders — an executed SL means the position
#         is closing, not that SL coverage is missing.
#         """
#         sl_side_upper = sl_side.upper()
#         ACTIVE_STATUSES = {
#             "open", "trigger pending",
#             "amo req received", "modified",
#             "put order req received",
#         }
#         # FIX #3: statuses that mean the SL already fired
#         EXECUTED_STATUSES = {"complete", "filled"}

#         total_qty      = 0
#         sl_just_fired  = False

#         for o in self.orderbook_cache:
#             if strip_eq(o.get("tradingsymbol", "")) != strip_eq(symbol):
#                 continue
#             if o.get("transactiontype", "").upper() != sl_side_upper:
#                 continue
#             if o.get("variety") not in ("STOPLOSS", "NORMAL"):
#                 continue
#             status = o.get("status", "").lower()
#             if status in ACTIVE_STATUSES:
#                 total_qty += safe_int(o.get("quantity", 0))
#             elif status in EXECUTED_STATUSES:
#                 sl_just_fired = True

#         if sl_just_fired and total_qty == 0:
#             # SL executed — position is in process of closing
#             # Return broker_qty equivalent so reconcile doesn't re-place SL
#             pos = self.positions_cache.get(strip_eq(symbol), {})
#             return pos.get("qty", 0)   # signals "fully covered, SL fired"

#         return total_qty

#     def has_active_sl_for(self, symbol, sl_side) -> bool:
#         return self.get_active_sl_qty_total(symbol, sl_side) > 0


# # =========================================================
# # NSE FREEZE QUANTITY  (#10: takes symbol string)
# # =========================================================

# _FREEZE_QTY: dict[str, int] = {
#     # Add per-symbol overrides here if needed
#     # "RELIANCE": 250,
#     "DEFAULT": 5000,
# }

# def get_freeze_qty(symbol: str) -> int:
#     """
#     FIX #10: Takes bare symbol string, not price.
#     Returns NSE freeze quantity for the symbol.
#     Update _FREEZE_QTY dict or maintain a CSV for accuracy.
#     """
#     return _FREEZE_QTY.get(strip_eq(symbol), _FREEZE_QTY["DEFAULT"])


# # =========================================================
# # SL ORDER PLACER
# # =========================================================

# class SLPlacer:
#     """
#     SELL SL (protecting BUY):  trigger ABOVE limit
#     BUY  SL (protecting SELL): trigger BELOW limit
#     """

#     def __init__(self, client, tokens):
#         self.client = client
#         self.tokens = tokens

#     def place(self, symbol, side, sl_price, qty,
#               broker_state=None) -> str | None:
#         """
#         FIX #10: symbol is always a string.
#         FIX #6: qty capped to freeze quantity.
#         FIX #4: registers placed order ID in broker_state.
#         """
#         # FIX #6 + #10: freeze qty uses symbol string
#         freeze_qty = get_freeze_qty(symbol)
#         if qty > freeze_qty:
#             log(f"  ! {symbol}: qty={qty} capped to freeze_qty={freeze_qty}")
#             qty = freeze_qty

#         sl_side = "SELL" if side == "BUY" else "BUY"
#         trigger_price = round_to_tick(
#             sl_price * (1 + SL_TRIGGER_PCT) if sl_side == "SELL"
#             else sl_price * (1 - SL_TRIGGER_PCT)
#         )

#         if DRY_RUN:
#             oid = f"DRYSL_{symbol}_{now().strftime('%H%M%S')}"
#             log(f"  [DRY] SL: {sl_side} {symbol} qty={qty} "
#                 f"trigger=Rs{trigger_price:.2f} limit=Rs{sl_price:.2f}")
#             if broker_state:
#                 broker_state.register_sl_order(symbol, oid)
#             return oid

#         params = {
#             "variety":         "STOPLOSS",
#             "tradingsymbol":   f"{symbol}-EQ",
#             "symboltoken":     self.tokens.get(symbol),
#             "transactiontype": sl_side,
#             "exchange":        "NSE",
#             "ordertype":       "STOPLOSS_LIMIT",
#             "producttype":     PRODUCT_TYPE,
#             "duration":        "DAY",
#             "price":           sl_price,
#             "triggerprice":    trigger_price,
#             "quantity":        qty,
#         }
#         try:
#             response = self.client.angel_obj.placeOrder(params)
#             if isinstance(response, dict):
#                 success = response.get("success", response.get("status", False))
#                 if not success:
#                     log(f"  ! SL rejected {symbol}: {response.get('message','?')}")
#                     return None
#                 oid = response.get("data", {}).get("orderid")
#             elif isinstance(response, str) and response:
#                 oid = response
#             else:
#                 return None

#             # FIX #4: register in broker_state
#             if oid and broker_state:
#                 broker_state.register_sl_order(symbol, oid)
#             return oid

#         except Exception as e:
#             log(f"  ! SL exception {symbol}: {e}")
#             return None

#     def cancel(self, sl_order_id) -> bool:
#         if DRY_RUN or not sl_order_id:
#             return True
#         try:
#             self.client.angel_obj.cancelOrder(sl_order_id, variety="STOPLOSS")
#             return True
#         except Exception as e:
#             log(f"  ! Cancel failed {sl_order_id}: {e}")
#             return False

#     def wait_for_fill(self, broker_state, symbol, order_id) -> FillResult:
#         for attempt in range(FILL_CONFIRM_RETRIES):
#             time.sleep(FILL_CONFIRM_SECS)
#             broker_state.force_orderbook_refresh()
#             fill = broker_state.get_fill_result(order_id)
#             if fill:
#                 log(f"  {symbol}: fill qty={fill.qty} @ Rs{fill.avg_price:.2f} "
#                     f"(attempt {attempt+1}/{FILL_CONFIRM_RETRIES})")
#                 return fill
#             log(f"  {symbol}: fill pending ({attempt+1}/{FILL_CONFIRM_RETRIES})")
#         return FillResult.unfilled()


"""
broker_client.py  —  v9

KEY CHANGE: BrokerState now merges BOTH position() and holding() APIs.
  position() = intraday / same-day positions
  holding()  = overnight DELIVERY holdings (the critical missing piece)

This means TSL, reconcile, and exit detection now work for ALL stocks
regardless of when they were bought.

PORTFOLIO SOURCE CLASSIFICATION:
  "source": "position"  — bought today (intraday or delivery same day)
  "source": "holding"   — carried overnight, delivery stock from prior days

Both are merged into positions_cache with identical structure so all
downstream engines (TSL, reconcile, exit detection) work unchanged.
"""

import time
from om_config import (
    ORDERBOOK_INTERVAL_MINS, TOKEN_CACHE_TTL_MINS,
    SL_TRIGGER_PCT, PRODUCT_TYPE, DRY_RUN,
    FILL_CONFIRM_SECS, FILL_CONFIRM_RETRIES,
    MAX_RISK_PCT,
)
from om_utils import log, safe_float, safe_int, round_to_tick, strip_eq, now


# =========================================================
# FILL RESULT
# =========================================================

class FillResult:
    def __init__(self, filled: bool, qty: int = 0,
                 avg_price: float = 0.0, order_id: str = ""):
        self.filled    = filled
        self.qty       = qty
        self.avg_price = avg_price
        self.order_id  = order_id

    @classmethod
    def unfilled(cls):
        return cls(filled=False)

    def __bool__(self):
        return self.filled and self.qty > 0


# =========================================================
# TOKEN CACHE
# =========================================================

class TokenCache:
    """One object, created once in OrderManagerApp, shared everywhere."""

    def __init__(self, client):
        self.client = client
        self._cache = {}   # bare_symbol -> (token_str, cached_at)

    def _is_stale(self, cached_at) -> bool:
        return (now() - cached_at).total_seconds() / 60 > TOKEN_CACHE_TTL_MINS

    def get(self, symbol: str) -> str:
        bare  = strip_eq(symbol)
        entry = self._cache.get(bare)
        if entry is None or self._is_stale(entry[1]):
            token = self.client.token_lookup(bare)
            self._cache[bare] = (token, now())
            return token
        return entry[0]

    def ltp(self, symbol: str, exchange="NSE") -> float:
        return self.client.get_ltp_data(self.get(symbol), exchange=exchange)

    def invalidate(self, symbol: str):
        self._cache.pop(strip_eq(symbol), None)


# =========================================================
# BROKER STATE  (v9: merges position() + holding())
# =========================================================

class BrokerState:

    def __init__(self, client):
        self.client           = client
        self.positions_cache  = {}   # bare_symbol -> dict (ALL holdings)
        self.orderbook_cache  = []
        self._last_ob_time    = None
        self._sl_replace_pending  = set()
        self._known_sl_order_ids  = {}   # bare_symbol -> set of order_id strings

    # ── Refresh ──────────────────────────────────────────────

    def refresh(self):
        self._refresh_positions()          # merges position() + holding()
        self._refresh_orderbook_if_due()

    def _refresh_positions(self):
        """
        v9 FIX: Merge intraday position() and overnight holding() APIs.

        position() returns same-day trades (all products).
        holding()  returns settled delivery stocks (T+2 settled).

        We merge both so TSL/reconcile/exit-detection work for ALL
        stocks this system manages — not just today's trades.
        """
        new_cache = {}

        # ── Step 1: intraday positions (today's trades, any product)
        try:
            data = (self.client.angel_obj.position() or {}).get("data") or []
            for p in data:
                qty = abs(int(safe_float(p.get("netqty", 0))))
                if qty <= 0:
                    continue
                symbol = strip_eq(p.get("tradingsymbol", ""))
                new_cache[symbol] = {
                    "symbol":    symbol,
                    "qty":       qty,
                    "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
                    "avg_price": safe_float(p.get("averageprice")),
                    "ltp":       safe_float(p.get("ltp")),
                    "pnl":       safe_float(p.get("pnl")),
                    "source":    "position",   # same-day
                }
        except Exception as e:
            log(f"position() refresh failed: {e}")

        # ── Step 2: overnight delivery holdings (prior-day purchases)
        # holding() returns stocks that have settled in demat.
        # These NEVER appear in position() after market close.
        try:
            h_data = (self.client.angel_obj.holding() or {}).get("data") or []
            for h in h_data:
                qty = safe_int(h.get("quantity", 0))
                if qty <= 0:
                    continue
                symbol = strip_eq(h.get("tradingsymbol", ""))

                # If already in new_cache from position(), don't overwrite
                # (same-day re-buy on existing holding — position() is more current)
                if symbol in new_cache:
                    continue

                avg_price = safe_float(h.get("averageprice",
                                        h.get("average_price", 0)))
                ltp       = safe_float(h.get("ltp", avg_price))
                pnl       = (ltp - avg_price) * qty

                new_cache[symbol] = {
                    "symbol":    symbol,
                    "qty":       qty,
                    "side":      "BUY",   # holdings are always long
                    "avg_price": avg_price,
                    "ltp":       ltp,
                    "pnl":       pnl,
                    "source":    "holding",   # overnight / prior days
                }
        except Exception as e:
            log(f"holding() refresh failed: {e}")

        # ── Detect exits (symbols in old cache but not in new)
        closed = set(self.positions_cache.keys()) - set(new_cache.keys())
        for sym in closed:
            old = self.positions_cache[sym]
            log(f"  [broker] Position CLOSED: {sym} "
                f"source={old.get('source','?')} "
                f"side={old['side']} qty={old['qty']} "
                f"last_ltp=Rs{old['ltp']:.2f}")
            self._known_sl_order_ids.pop(sym, None)

        self.positions_cache = new_cache

    def _refresh_orderbook_if_due(self):
        if self._last_ob_time:
            if (now() - self._last_ob_time).total_seconds() / 60 \
                    < ORDERBOOK_INTERVAL_MINS:
                return
        self._do_orderbook_refresh()

    def _do_orderbook_refresh(self):
        try:
            ob = self.client.angel_obj.orderBook()
            self.orderbook_cache = (ob or {}).get("data") or []
            self._last_ob_time   = now()
        except Exception as e:
            log(f"Orderbook refresh failed: {e}")

    def force_orderbook_refresh(self):
        self._last_ob_time = None
        self._do_orderbook_refresh()

    # ── SL replace pending guard ─────────────────────────────

    def mark_sl_replace_pending(self, symbol: str):
        self._sl_replace_pending.add(strip_eq(symbol))

    def clear_sl_replace_pending(self, symbol: str):
        self._sl_replace_pending.discard(strip_eq(symbol))

    def is_sl_replace_pending(self, symbol: str) -> bool:
        return strip_eq(symbol) in self._sl_replace_pending

    # ── Known SL order ID tracking ───────────────────────────

    def register_sl_order(self, symbol: str, sl_order_id: str):
        bare = strip_eq(symbol)
        if bare not in self._known_sl_order_ids:
            self._known_sl_order_ids[bare] = set()
        self._known_sl_order_ids[bare].add(sl_order_id)

    def get_known_sl_count(self, symbol: str) -> int:
        return len(self._known_sl_order_ids.get(strip_eq(symbol), set()))

    def cancel_all_known_sls(self, symbol: str, sl_placer) -> int:
        bare      = strip_eq(symbol)
        oids      = list(self._known_sl_order_ids.get(bare, set()))
        cancelled = 0
        for oid in oids:
            if sl_placer.cancel(oid):
                cancelled += 1
        self._known_sl_order_ids.pop(bare, None)
        return cancelled

    # ── Position helpers ─────────────────────────────────────

    def inject_position(self, symbol, side, avg_price, qty):
        """Immediate cache update after confirmed fill (before next refresh)."""
        bare = strip_eq(symbol)
        self.positions_cache[bare] = {
            "symbol":    bare,
            "qty":       qty,
            "side":      side,
            "avg_price": avg_price,
            "ltp":       avg_price,
            "pnl":       0.0,
            "source":    "position",
        }

    def sync_position_from_broker(self, symbol: str):
        """Correct injected position with live broker data post-fill."""
        try:
            bare = strip_eq(symbol)
            # Check position() first (same-day fill)
            data = (self.client.angel_obj.position() or {}).get("data") or []
            for p in data:
                if strip_eq(p.get("tradingsymbol", "")) != bare:
                    continue
                qty = abs(int(safe_float(p.get("netqty", 0))))
                if qty > 0:
                    self.positions_cache[bare] = {
                        "symbol":    bare,
                        "qty":       qty,
                        "side":      "BUY" if safe_float(p.get("netqty")) > 0 else "SELL",
                        "avg_price": safe_float(p.get("averageprice")),
                        "ltp":       safe_float(p.get("ltp")),
                        "pnl":       safe_float(p.get("pnl")),
                        "source":    "position",
                    }
                    log(f"  [broker] Synced {bare}: "
                        f"qty={qty} avg=Rs{self.positions_cache[bare]['avg_price']:.2f}")
                else:
                    self.positions_cache.pop(bare, None)
                return
        except Exception as e:
            log(f"  [broker] Single-position sync failed {symbol}: {e}")

    def remove_position(self, symbol):
        self.positions_cache.pop(strip_eq(symbol), None)

    def has_position(self, symbol) -> bool:
        return strip_eq(symbol) in self.positions_cache

    def get_positions(self) -> dict:
        return self.positions_cache

    def position_count(self) -> int:
        return len(self.positions_cache)

    def get_system_positions(self) -> dict:
        """
        v9: Return ONLY positions that came from this system's recommendations
        (source == "position") OR have tsl_state in DB.
        Used by TSL to decide which holdings to trail.
        Note: caller (TSLEngine) cross-checks against DB.
        """
        return {s: p for s, p in self.positions_cache.items()
                if p.get("source") == "position"}

    def get_all_holdings(self) -> dict:
        """Return ALL positions including overnight holdings."""
        return self.positions_cache

    # ── Orderbook helpers ─────────────────────────────────────

    def get_order_from_book(self, order_id) -> dict | None:
        for o in self.orderbook_cache:
            if o.get("orderid") == order_id:
                return o
        return None

    def get_fill_result(self, order_id) -> FillResult:
        o = self.get_order_from_book(order_id)
        if o is None:
            return FillResult.unfilled()
        filled_qty = safe_int(o.get("filledshares", 0))
        avg_price  = safe_float(o.get("averageprice", 0.0))
        if filled_qty > 0 and avg_price > 0:
            return FillResult(True, filled_qty, avg_price, order_id)
        return FillResult.unfilled()

    def get_active_sl_qty_total(self, symbol, sl_side) -> int:
        """
        Sum ALL active SL orders for symbol+side.
        Returns broker_qty if SL already fired (prevents re-place on closing position).
        """
        sl_side_upper = sl_side.upper()
        ACTIVE_STATUSES = {
            "open", "trigger pending",
            "amo req received", "modified",
            "put order req received",
        }
        EXECUTED_STATUSES = {"complete", "filled"}

        total_qty     = 0
        sl_just_fired = False

        for o in self.orderbook_cache:
            if strip_eq(o.get("tradingsymbol", "")) != strip_eq(symbol):
                continue
            if o.get("transactiontype", "").upper() != sl_side_upper:
                continue
            if o.get("variety") not in ("STOPLOSS", "NORMAL"):
                continue
            status = o.get("status", "").lower()
            if status in ACTIVE_STATUSES:
                total_qty += safe_int(o.get("quantity", 0))
            elif status in EXECUTED_STATUSES:
                sl_just_fired = True

        if sl_just_fired and total_qty == 0:
            pos = self.positions_cache.get(strip_eq(symbol), {})
            return pos.get("qty", 0)

        return total_qty

    def has_active_sl_for(self, symbol, sl_side) -> bool:
        return self.get_active_sl_qty_total(symbol, sl_side) > 0


# =========================================================
# NSE FREEZE QUANTITY
# =========================================================

_FREEZE_QTY: dict[str, int] = {
    # Override per-symbol if needed: "RELIANCE": 250,
    "DEFAULT": 5000,
}

def get_freeze_qty(symbol: str) -> int:
    return _FREEZE_QTY.get(strip_eq(symbol), _FREEZE_QTY["DEFAULT"])


# =========================================================
# SL ORDER PLACER
# =========================================================

class SLPlacer:
    """
    SELL SL (protecting BUY):  trigger ABOVE limit (activates as price drops)
    BUY  SL (protecting SELL): trigger BELOW limit (activates as price rises)
    """

    def __init__(self, client, tokens):
        self.client = client
        self.tokens = tokens

    def place(self, symbol, side, sl_price, qty,
              broker_state=None) -> str | None:
        freeze_qty = get_freeze_qty(symbol)
        if qty > freeze_qty:
            log(f"  ! {symbol}: qty={qty} capped to freeze_qty={freeze_qty}")
            qty = freeze_qty

        sl_side       = "SELL" if side == "BUY" else "BUY"
        trigger_price = round_to_tick(
            sl_price * (1 + SL_TRIGGER_PCT) if sl_side == "SELL"
            else sl_price * (1 - SL_TRIGGER_PCT)
        )

        if DRY_RUN:
            oid = f"DRYSL_{symbol}_{now().strftime('%H%M%S')}"
            log(f"  [DRY] SL: {sl_side} {symbol} qty={qty} "
                f"trigger=Rs{trigger_price:.2f} limit=Rs{sl_price:.2f}")
            if broker_state:
                broker_state.register_sl_order(symbol, oid)
            return oid

        params = {
            "variety":         "STOPLOSS",
            "tradingsymbol":   f"{symbol}-EQ",
            "symboltoken":     self.tokens.get(symbol),
            "transactiontype": sl_side,
            "exchange":        "NSE",
            "ordertype":       "STOPLOSS_LIMIT",
            "producttype":     PRODUCT_TYPE,
            "duration":        "DAY",
            "price":           sl_price,
            "triggerprice":    trigger_price,
            "quantity":        qty,
        }
        try:
            response = self.client.angel_obj.placeOrder(params)
            if isinstance(response, dict):
                success = response.get("success", response.get("status", False))
                if not success:
                    log(f"  ! SL rejected {symbol}: {response.get('message','?')}")
                    return None
                oid = response.get("data", {}).get("orderid")
            elif isinstance(response, str) and response:
                oid = response
            else:
                return None

            if oid and broker_state:
                broker_state.register_sl_order(symbol, oid)
            return oid

        except Exception as e:
            log(f"  ! SL exception {symbol}: {e}")
            return None

    def cancel(self, sl_order_id) -> bool:
        if DRY_RUN or not sl_order_id:
            return True
        try:
            self.client.angel_obj.cancelOrder(sl_order_id, variety="STOPLOSS")
            return True
        except Exception as e:
            log(f"  ! Cancel failed {sl_order_id}: {e}")
            return False

    def wait_for_fill(self, broker_state, symbol, order_id) -> FillResult:
        for attempt in range(FILL_CONFIRM_RETRIES):
            time.sleep(FILL_CONFIRM_SECS)
            broker_state.force_orderbook_refresh()
            fill = broker_state.get_fill_result(order_id)
            if fill:
                log(f"  {symbol}: fill qty={fill.qty} @ Rs{fill.avg_price:.2f} "
                    f"(attempt {attempt+1}/{FILL_CONFIRM_RETRIES})")
                return fill
            log(f"  {symbol}: fill pending ({attempt+1}/{FILL_CONFIRM_RETRIES})")
        return FillResult.unfilled()


