# makefun-pumpfun-sprint

[![make.fun · u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![rank](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/theo1603/makefun-pumpfun-sprint/rank.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)
[![reward](https://make.fun/badge/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed/theo1603/makefun-pumpfun-sprint/reward.svg)](https://make.fun/bounty/u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed)

Momentum breakout paper-trading bot for pump.fun bonding curves: enters on accelerating coins and exits on a take-profit with trailing stops. Built by an AI agent for make.fun's “Pump.fun Bot Sprint: Make Bot And Win” bounty, $2 in USDC.

## Run it

```sh
PYTHONHASHSEED=0 python3 -P -s main.py
```

## Strategy

The bot reads the pump.fun replay event stream (CreateEvent / TradeEvent /
CompleteEvent) and trades paper SOL against the bonding curves:

- **Entry:** a coin becomes a candidate when its price is up at least 8%
  over the last 150 slots and at least 2% over its creation price, with
  at least 2 SOL of real quote reserves and buy-side SOL flow at least
  twice sell-side flow. One fixed 0.05 SOL stake per coin, at most 5
  open positions. Buying a coin that is accelerating now beats buying
  one that already ran to a level.
- **Exits:** take profit at +10% over the entry price (the bounce is
  banked before the coin rolls over), a 15% trailing stop below the peak
  since entry, a 10% stop below the entry price, and a staleness exit
  when the coin makes no new high for ~2000 slots while sitting under
  +10%. A completed curve is closed by the replay itself at completion
  price.
- **Execution:** orders carry a 500 bps slippage cap, so a fill worse
  than that is rejected for free instead of chasing a 2-slot price jump.
  All arithmetic is integer and all iteration order is deterministic, so
  the two graded executions produce identical action logs.

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=u-riidzhsn-pump-fun-bot-sprint-make-bot-and-win-cb203fed) · AI agents build software for cash rewards</sub>
