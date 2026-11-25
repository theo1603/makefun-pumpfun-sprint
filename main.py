"""Pump.fun replay paper-trading bot (agent-fun/replay/v2, Python).

Strategy, written from scratch for this bounty (v2: velocity entry with a
take-profit exit, tuned on the kit's dev window with a batch-level
replay of the harness rules):

  * Velocity entry: a coin becomes a candidate when its bonding-curve
    price is up at least 8% over the last 150 slots AND at least 2% over
    its creation price, with at least 2 SOL of real quote reserves (so
    one order can meet the 0.01 SOL round-trip minimum) and buy-side SOL
    flow at least twice sell-side flow. One fixed 0.05 SOL stake per
    coin, at most 5 open positions. Buying a coin that is accelerating
    NOW beats buying one that already ran to a level: level breakouts
    enter after the move and bleed.
  * Exits, checked every batch in this order: take profit at +10% over
    the entry price (sell all: the bounce is banked before the coin
    rolls over), a 15% trailing stop below the peak since entry, a 10%
    stop below the entry price, and a staleness exit when the coin makes
    no new high for ~2000 slots while sitting under +10%. A completed
    curve is closed by the replay itself at completion price.
  * Orders carry a 500 bps slippage cap: a fill worse than that is
    rejected for free, so a 2-slot adverse jump never chases the price.
  * All arithmetic is integer (lamports and scaled price ratios) and all
    iteration follows insertion order or sorted order, so the two graded
    executions produce byte-identical action logs. No clock, no network,
    no unseeded randomness. Diagnostics go to stderr only.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script directory off sys.path)

SOL = makefun_bot.LAMPORTS_PER_SOL
SCALE = 1_000_000  # price ratios are scaled by this; all math stays integer

# Entry.
VELOCITY_MULT = 108         # price must be >= 1.08x its price 150 slots ago
VELOCITY_LAG = 150          # slots back for the velocity comparison
ENTRY_MULT = 102            # ... and >= 1.02x its creation price
MIN_REAL_QUOTE = 2 * SOL    # need 2 SOL real reserves so one buy can be >= 0.01 SOL
MIN_TRADES = 4              # a little tape before trusting the move
MIN_BUY_DOMINANCE = 2       # buy SOL must be >= 2x sell SOL over the coin's life
STAKE = SOL // 20           # 0.05 SOL per coin
MIN_BUY = SOL // 100        # 0.01 SOL: the guardrail's round-trip minimum
MAX_BUY_ATTEMPTS = 3        # retries when an order fails (2% of orders fail) or is rejected
CASH_RESERVE = SOL // 2     # never deploy the last 0.5 SOL (exit tx fees live there)

# Exits.
TP_MULT = 110               # take profit: sell all at +10% over entry
TRAIL_BPS = 1500            # sell when 15% below the peak since entry
STOP_BPS = 1000             # sell when 10% below the entry price
STALE_SLOTS = 2000          # no new high for this long ...
STALE_MAX_MULT = 110        # ... while under +10%: give the slot to a fresher coin
SLIPPAGE_BPS = 500          # free protection: a worse fill is rejected, not chased


def price_of(curve):
    """Scaled price: virtual quote per virtual token (0 when unknown)."""
    if curve is None or curve.virtual_token_reserves <= 0:
        return 0
    return curve.virtual_quote_reserves * SCALE // curve.virtual_token_reserves


class Coin:
    """Per-mint tape state, kept in first-seen order."""

    __slots__ = (
        "create_price", "trades", "buy_sol", "sell_sol", "attempts",
        "entry_price", "peak", "peak_slot", "done", "history",
    )

    def __init__(self, create_price):
        self.create_price = create_price
        self.trades = 0
        self.buy_sol = 0
        self.sell_sol = 0
        self.attempts = 0
        self.entry_price = 0   # set when a buy fills (decision-time price)
        self.peak = 0
        self.peak_slot = 0
        self.done = False      # traded and exited: never re-enter the same mint
        self.history = []      # (slot, scaled price) samples, one per batch seen

    def price_ago(self, slot, lag):
        """Most recent sampled price at least `lag` slots old, else 0."""
        target = slot - lag
        for sample_slot, sample_price in reversed(self.history):
            if sample_slot <= target:
                return sample_price
        return 0


coins = {}        # mint -> Coin, insertion ordered
buy_orders = {}   # client_id -> (mint, decision_price)
sell_orders = {}  # client_id -> mint
_next_id = [1]    # deterministic client ids: b1, b2, ... / s1, s2, ...


def fresh_id(prefix):
    cid = "%s%d" % (prefix, _next_id[0])
    _next_id[0] += 1
    return cid


def on_batch(state, batch):
    slot = batch["slot"]
    orders = []

    # 1. Learn from this batch's fills first.
    for fill in batch["fills"]:
        cid = fill["client_id"]
        if cid in buy_orders:
            mint, decision_price = buy_orders.pop(cid)
            coin = coins.get(mint)
            if coin is None:
                continue
            if fill["status"] == "filled":
                coin.entry_price = decision_price
                coin.peak = decision_price
                coin.peak_slot = slot
            else:  # failed or rejected: the coin stays a candidate
                coin.attempts += 1
        elif cid in sell_orders:
            mint = sell_orders.pop(cid)
            coin = coins.get(mint)
            if coin is not None and fill["status"] == "filled":
                coin.done = True

    # 2. Fold this batch's events into the per-mint tape, and sample each
    # mint seen in this batch once at its end-of-batch price.
    seen = []
    for event in batch["events"]:
        mint = event["mint"]
        kind = event["event"]
        if kind == "CreateEvent":
            if mint not in coins:
                p = int(event["virtual_quote_reserves"]) * SCALE // int(event["virtual_token_reserves"])
                coins[mint] = Coin(p)
            seen.append(mint)
        elif kind == "TradeEvent":
            coin = coins.get(mint)
            if coin is None:
                p = int(event["virtual_sol_reserves"]) * SCALE // int(event["virtual_token_reserves"])
                coin = coins[mint] = Coin(p)
            coin.trades += 1
            if event["is_buy"]:
                coin.buy_sol += int(event["sol_amount"])
            else:
                coin.sell_sol += int(event["sol_amount"])
            seen.append(mint)
        else:  # CompleteEvent
            coin = coins.get(mint)
            if coin is not None:
                coin.done = True
    for mint in seen:
        coin = coins.get(mint)
        curve = state.curves.get(mint)
        if coin is not None and curve is not None:
            price = price_of(curve)
            if price > 0:
                coin.history.append((slot, price))

    # 3. Exits first: free the 5 position slots before looking for entries.
    tx = state.tx_fee_lamports()
    pending_sell = set()
    pending_buy = set()
    for pending in state.pending.values():
        if pending.order["side"] == "sell":
            pending_sell.add(pending.order["mint"])
        else:
            pending_buy.add(pending.order["mint"])
    for mint in sorted(state.positions):
        if mint in pending_sell:
            continue
        coin = coins.get(mint)
        curve = state.curves.get(mint)
        if coin is None or curve is None or curve.complete or coin.entry_price <= 0:
            continue
        price = price_of(curve)
        if price <= 0:
            continue
        if price > coin.peak:
            coin.peak = price
            coin.peak_slot = slot
        exit_now = False
        if price * 100 >= coin.entry_price * TP_MULT:
            exit_now = True
        elif price * 10000 <= coin.peak * (10000 - TRAIL_BPS):
            exit_now = True
        elif price * 10000 <= coin.entry_price * (10000 - STOP_BPS):
            exit_now = True
        elif (
            slot - coin.peak_slot > STALE_SLOTS
            and price * 100 <= coin.entry_price * STALE_MAX_MULT
        ):
            exit_now = True
        if exit_now:
            holding = state.positions[mint]
            if holding.token_amount > 0 and state.cash >= tx:
                cid = fresh_id("s")
                orders.append({
                    "side": "sell", "mint": mint,
                    "token_amount": holding.token_amount,
                    "max_slippage_bps": SLIPPAGE_BPS,
                    "client_id": cid,
                })
                sell_orders[cid] = mint
                pending_sell.add(mint)

    # 4. Entries, in the batch's event order.
    caps = state.hello["caps"]
    free = state.free_cash_lamports()
    slots = caps["max_open_positions"] - state.committed_position_count()
    for event in batch["events"]:
        if len(orders) >= caps["max_orders_per_batch"] or slots <= 0:
            break
        mint = event["mint"]
        coin = coins.get(mint)
        if coin is None or coin.done or coin.entry_price > 0:
            continue
        if mint in state.positions or mint in pending_buy:
            continue
        if coin.attempts >= MAX_BUY_ATTEMPTS:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        if coin.trades < MIN_TRADES:
            continue
        price = price_of(curve)
        if price <= 0 or price * 100 < coin.create_price * ENTRY_MULT:
            continue
        if coin.buy_sol < MIN_BUY_DOMINANCE * coin.sell_sol:
            continue
        past = coin.price_ago(slot, VELOCITY_LAG)
        if past <= 0 or price * 100 < past * VELOCITY_MULT:
            continue
        if curve.real_quote_reserves < MIN_REAL_QUOTE:
            continue
        cap = state.max_buy_lamports(mint)
        amount = min(cap, STAKE)
        if amount < MIN_BUY or free < amount + tx + CASH_RESERVE:
            continue
        cid = fresh_id("b")
        orders.append({
            "side": "buy", "mint": mint,
            "quote_lamports": amount,
            "max_slippage_bps": SLIPPAGE_BPS,
            "client_id": cid,
        })
        buy_orders[cid] = (mint, price)
        pending_buy.add(mint)
        free -= amount + tx
        slots -= 1

    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
