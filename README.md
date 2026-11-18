# Dipcatcher — BTC trading bot for make.fun

[![make.fun](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/chiidaichii1-boop/btc-race-20261118/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/chiidaichii1-boop/btc-race-20261118/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444)

Entry for [Race a BTC trading bot live for 24 hours](https://make.fun/bounty/btc-race-24h-20261118-1444)
(`btc-race-24h-20261118-1444`, $250, top 10 paid).

## What it does

Dipcatcher trades Coinbase Wrapped BTC (CBBTC) against USDC, one minute bar
at a time, with $1,000 of paper money. It is a dip-buyer with an oversold
guard:

- **Entry:** the latest close is at least **35 bps below the trailing
  20-bar high** (a short dip, not a crash) **and** Wilder **RSI(14) ≤ 35**
  (the dip is oversold). It buys with **25% of cash**.
- **Exit:** take profit at **+40 bps** from the entry fill price, cut at
  **−60 bps**, or give up after **120 bars** (~2 hours) with no move either
  way. After an exit it waits **5 bars** before the next entry.
- **Circuit breaker:** if equity ever falls **10%** below its peak, it
  flattens and stops trading for the rest of the window (the race guardrail
  is 25%).

## Why this shape

On the frozen 24-hour dev window the market bled slowly (−2.8% over the
day) with small, short-lived bounces. Trend-following and breakout rules all
lost money there — every kit baseline finished below $0 (momentum −$23.87,
ema-cross −$148.17, rsi-bollinger −$39.92, adx-momentum −$75.57,
buy-and-hold −$31.63). Only buying dips with an oversold guard made money,
and only in small size with tight exits, because a full-size round trip pays
~26–36 bps in fees, slippage and price impact.

The chosen parameters are the most consistent dip-buy variant found in an
offline scan of the dev data: positive in 3 of the 4 six-hour quarters,
14 round trips, max drawdown 0.40%.

## Dev-window result (local replay of the official harness)

| metric | value |
|---|---|
| net PnL | **+$2.18** (passes the dev gate: > $0) |
| filled orders | 27 (14 buys + 13 sells; the last position flattens at the close) |
| fees paid | $2.81 |
| max drawdown | 0.40% (guardrail: 25%) |
| determinism | two runs, byte-identical replies |

Verified with a local replay driver that re-implements the
`agent-fun/bars-replay/v1` harness in integer arithmetic (same hello,
batches, fills at next bar's open, 4 bps fee + 2 bps slippage +
constant-product impact vs open depth, flatten at the last close). The
driver reproduces the official `grade-local` buy-and-hold result to the
cent (−$31.633697 vs −$31.633698), so its numbers track the real grader.
(`grade-local --driver process` cannot run a Python bot on this machine —
it only runs Node; Python 3.13 runs only inside Docker, which is not
installed here — so the replay driver is the verification path.)

## How to run

Python 3.13, standard library only. The bot speaks
`agent-fun/bars-replay/v1` (newline-delimited JSON on stdin/stdout): the
harness sends one `hello`, then one `batch` per minute bar, then `done`;
the bot answers every batch with exactly one reply echoing its `seq`.

```sh
python3 -P -s main.py
```

(`-P` leaves the script's directory off `sys.path`, so `main.py` adds its
own `vendor/` directory for the vendored SDK.)

The bot is fully deterministic: no randomness, no wall clock, no
environment reads, no files outside the repository. Two runs on the same
data produce identical orders.

## Files

- `main.py` — the bot (entrypoint)
- `vendor/makefun_bars.py` — byte-identical copy of the kit's Python SDK shim
- `LICENSE` — MIT
