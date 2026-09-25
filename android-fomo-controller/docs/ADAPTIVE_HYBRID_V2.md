# Adaptive Hybrid v2 Android implementation

## Frozen opportunity model

The app embeds the fitted sklearn bundle as `adaptive_hybrid_v2.json.gz` and
evaluates its trees directly in Kotlin. The port preserves:

- model version: `adaptive_hybrid_v2`;
- selected preset: `mild`;
- trained-through time: `2026-09-22 21:09:30.822000`;
- features: trader, `log1p(entry market cap)`, and `log1p(source buy USD)`;
- 250-tree HistGradientBoosting classifier and 250-tree regressor;
- 9,633-value classifier confidence reference;
- ROI validation median absolute error: `0.35303748963112425`;
- excluded trader: `DuckSoldier`;
- source outcome rule: first later sell with the same trader and token.

The export script is `tools/export_adaptive_hybrid_v2.py`. JVM parity tests
compare Kotlin inference with sklearn predictions stored during export.

## Selected controller

| Mode | Confidence | Minimum ratio | Reserve | Max open | Trader | Token | 15-minute burst | Uncertainty multiplier |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| NORMAL | 80th percentile | 1:10 | max($150, 5%) | 90% | 35% | 25% | 30% | 0.25 |
| CAUTION | 83rd percentile | 1:20 | max($175, 8%) | 80% | 30% | 20% | 25% | 0.35 |
| DEFENSIVE | 88th percentile | 1:25 | max($225, 15%) | 68% | 20% | 12% | 15% | 0.60 |

The account ladder starts at 1:20, promotes to 1:15 after equity reaches $2,000
and ten qualifying exits confirm the level, and promotes to 1:10 after $4,000
and another ten confirmations. Promotions pause outside NORMAL. Falling below a
promotion equity floor downgrades immediately.

The controller also increases reserve for one-hour signal bursts, losing
streaks, and recent candidate size. Deterioration to CAUTION or DEFENSIVE is
immediate. Recovery requires four completed shadow exits plus improved
drawdown, mean return, win rate, cash, and exposure.

## Fees and conservative EV

Standard fees are `max($0.95, 0.005 × order value)`. With the 10% code they are
`max($0.855, 0.0045 × order value)`. Both cross over at $190. Fees apply on buy
and sell. Optional per-side slippage is supported by the model configuration and
is zero in the selected artifact because the notification log has no measured
execution slippage.

The fee gate uses:

~~~text
conservative predicted ROI = predicted ROI
                           - mode uncertainty multiplier × 0.35303748963112425
~~~

When estimated round-trip burden exceeds the mode trigger, conservative ROI
must exceed the configured multiple of that burden. The strategy never enlarges
an order merely to escape the fee floor.

## Persistent causal state

`adaptive_hybrid_v2.db` stores the controller state separately from the raw
notification recorder. It contains:

- the $1,000 strategy wallet, peak equity, mode, and promotion state;
- all model top-20% shadow buys and their first matching sell outcomes;
- every model decision and its reason;
- planned/prepared capital reservations;
- confirmed or DRY_RUN paper positions;
- the exact position IDs assigned to each sell plan.

The raw recorder remains append-only. Strategy deduplication does not delete or
replace captured notifications.

## Execution integrity

OBSERVE never creates a copied position. DRY_RUN records a paper execution only
after the exact notification opens and the expected token is visible. PREPARE
fills the calibrated form but leaves the plan reserved until the dashboard user
confirms or rejects it. UI failure, expiry, or queue cancellation releases an
unprepared reservation.

Sell plans are created only for confirmed/paper positions, and each plan is
bound to the exact position IDs visible when the sell was received. A later buy
cannot be accidentally swept into an older pending sell.

The remaining material limitation is token identity. The historical data and
current notification parser expose ticker only. Mint/contract capture is needed
before treating same-ticker assets as cryptographically distinct positions.
