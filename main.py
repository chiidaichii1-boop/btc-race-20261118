"""Dipcatcher: a dip-buying BTC trading bot for make.fun btc-race-24h-20261118-1444.

Strategy (original; fitted and checked on the frozen 24h dev window only):
  - One asset, CBBTC, long only, one open position at a time.
  - Entry: the latest close sits at least 35 bps below the trailing 20-bar
    high (a short dip, not a crash) AND Wilder RSI(14) is at most 35
    (the dip is oversold, not the start of a slide). Buy with 25% of cash.
  - Exit: take profit at +40 bps from the entry fill price, cut at -60 bps,
    or give up after 120 bars (about 2 hours) with no move either way.
  - After an exit, wait 5 bars before the next entry (cooldown).
  - Circuit breaker: if equity ever falls 10% below its peak, flatten and
    stop trading for the rest of the window (the race guardrail is 25%).

Why this shape: on the dev window the market bled slowly (-2.8% over the
day) with small, short-lived bounces. Trend-following and breakout rules all
lost money there (every kit baseline finished below $0); only buying dips
with an oversold guard made money, and only in small size with tight exits,
because a full-size round trip pays ~26-36 bps in fees, slippage and price
impact. The chosen parameters are the most consistent dip-buy variant found
in an offline scan of the dev data: positive in 3 of the 4 six-hour quarters,
14 round trips, max drawdown 0.40%.

Protocol: agent-fun/bars-replay/v1. The harness sends one hello, then one
batch per minute bar, then done; the bot answers every batch with exactly
one reply echoing its seq. Orders decided on a batch fill at the next bar's
open, so the entry decision uses the bar that just closed and the exit
decision uses the latest close against the recorded entry fill price. The
bot is fully deterministic: no randomness, no clock, no environment reads,
no files -- every number is a pure function of the bars received.
"""

import os
import sys
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars  # noqa: E402  (vendored kit SDK, byte-identical copy)

SYMBOL = "CBBTC"

# --- strategy parameters (chosen on the dev window; see README) ---
LOOKBACK_BARS = 20      # rolling-high window for the dip
DIP_BPS = 35            # enter when close is this far below the 20-bar high
RSI_PERIOD = 14         # Wilder RSI length
RSI_MAX = 35            # enter only when RSI is at most this (oversold)
SIZE_FRACTION_PCT = 25  # spend this % of cash per entry
TAKE_PROFIT_BPS = 40    # exit when the close is this far above the entry fill
STOP_LOSS_BPS = 60      # exit when the close is this far below the entry fill
MAX_HOLD_BARS = 120     # exit after this many bars with no TP/SL hit
COOLDOWN_BARS = 5       # bars to wait after an exit before the next entry
MAX_SLIPPAGE_BPS = 100  # order-level slippage bound (1%; 1-min moves stay well under it)
DRAWDOWN_HALT_PCT = 10  # circuit breaker: flatten and stop at this peak drawdown

BPS = 10_000


class Dipcatcher:
    """State for one run: closes, RSI, the open position, cooldown, peak."""

    def __init__(self):
        self.closes = deque(maxlen=LOOKBACK_BARS)  # closes in USD (floats)
        self.prev_close = None
        self.avg_gain = None  # Wilder RSI(14) running averages
        self.avg_loss = None
        self.gain_window = []  # first RSI_PERIOD changes, for the seed average
        self.loss_window = []
        self.entry_price = None  # USD fill price of the open position
        self.entry_bar = -1      # bar index (fill_bar) the position opened on
        self.cool_until = -1     # no entry on bars <= this
        self.peak_equity = None  # quote units
        self.halted = False

    def rsi(self):
        """Wilder RSI(14) of the closes seen so far, or None before bar 14."""
        if self.avg_gain is None:
            return None
        if self.avg_loss == 0:
            return 100.0
        rs = self.avg_gain / self.avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    def on_close(self, close_usd):
        """Fold one new close into the indicators (call once per batch)."""
        if self.prev_close is not None:
            change = close_usd - self.prev_close
            gain = change if change > 0 else 0.0
            loss = -change if change < 0 else 0.0
            if self.avg_gain is None:
                self.gain_window.append(gain)
                self.loss_window.append(loss)
                if len(self.gain_window) == RSI_PERIOD:
                    self.avg_gain = sum(self.gain_window) / RSI_PERIOD
                    self.avg_loss = sum(self.loss_window) / RSI_PERIOD
            else:
                self.avg_gain = (self.avg_gain * (RSI_PERIOD - 1) + gain) / RSI_PERIOD
                self.avg_loss = (self.avg_loss * (RSI_PERIOD - 1) + loss) / RSI_PERIOD
        self.closes.append(close_usd)
        self.prev_close = close_usd

    def on_batch(self, state, batch):
        # The batch carries the bar that just closed, the fills of the orders
        # decided on the previous batch, and the current account.
        bar = batch["bars"][0]
        close_usd = int(bar["c"]) / 1e5
        self.on_close(close_usd)

        # Record any fill: a buy fill opens the position (entry price/bar).
        for fill in batch.get("fills", []):
            if fill.get("status") == "filled" and fill.get("side") == "buy":
                self.entry_price = int(fill["price"]) / 1e5
                self.entry_bar = fill["fill_bar"]

        # Circuit breaker: peak equity so far, halt at a 10% drawdown.
        equity = state.equity
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if not self.halted and self.peak_equity > 0:
            drawdown_pct = (self.peak_equity - equity) * 100.0 / self.peak_equity
            if drawdown_pct >= DRAWDOWN_HALT_PCT:
                self.halted = True

        holding = state.positions.get(SYMBOL)

        if self.halted:
            # Flatten if still holding, then stay flat for the rest of the run.
            if holding is not None and holding.qty > 0:
                return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        if holding is not None and holding.qty > 0:
            # Exit check: TP / SL / max hold, judged on this bar's close
            # against the entry fill price (the order fills at next open).
            gain_bps = (close_usd / self.entry_price - 1.0) * BPS
            held_bars = state.seq - self.entry_bar
            if (gain_bps >= TAKE_PROFIT_BPS or gain_bps <= -STOP_LOSS_BPS
                    or held_bars >= MAX_HOLD_BARS):
                self.cool_until = state.seq + COOLDOWN_BARS
                return [{"side": "sell", "symbol": SYMBOL, "qty": holding.qty,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
            return []

        # Flat: entry check. Needs a full 20-bar high, the cooldown to have
        # passed, and no warm-up (orders on warm-up batches are rejected).
        # The first entry is on seq 20, not 19: the offline scan that chose
        # these parameters required i >= lookback, i.e. 20 closes before the
        # first decision, and the one-bar-earlier variant loses $2.51 on the
        # dev window (its first dip-buy catches a worse price and the whole
        # trade sequence desynchronises). Keep the bot byte-faithful to the
        # scanned configuration.
        if state.warming_up:
            return []
        if state.seq < LOOKBACK_BARS or len(self.closes) < LOOKBACK_BARS:
            return []
        if state.seq <= self.cool_until:
            return []
        high = max(self.closes)
        dip_bps = (close_usd / high - 1.0) * BPS
        rsi = self.rsi()
        if dip_bps <= -DIP_BPS and (rsi is None or rsi <= RSI_MAX):
            notional = state.cash * SIZE_FRACTION_PCT // 100
            if notional >= 1_000_000:  # min notional $1, in quote units
                return [{"side": "buy", "symbol": SYMBOL,
                         "notional_quote_units": notional,
                         "max_slippage_bps": MAX_SLIPPAGE_BPS}]
        return []


def main():
    bot = Dipcatcher()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
