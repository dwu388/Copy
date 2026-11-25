# Causal 24-hour label correction: retrospective results

> Historical component comparison. The integrated 0.5 preset, calibrated paper
> artifact and current full-controller replay are documented in
> [INTEGRATED_PAPER.md](INTEGRATED_PAPER.md). These older numbers are not results
> for the new preset.

Reviewed on November 17, 2026 (Pacific). Base repository: `b27a5c5`.
Aggregate machine-readable output: [causal_24h_backtest_results.json](causal_24h_backtest_results.json).
Private input rows and trained experimental models are not committed.

## Finding

Correcting the label population substantially improves probability calibration.
It does **not** establish a profitable trading policy or a statistically reliable
economic improvement. Keep the bundled forecast unchanged pending new forward
evidence. The protected maintenance smoke test also rejected its challenger.

The corrected classifier predicts a positive **net** reference-size return among
all mature buys. The old classifier predicts gross profitability conditional on
an eventual observed source sell. Both are scored against the same new net
outcomes here, which exposes the old target mismatch rather than retrospectively
reproducing the original model's reported metrics.

## Frozen comparison

- History: supplied `fomo_buy_sell_notifications.csv`, 21,471 raw callbacks,
  October 3–November 10 UTC; 11,508 mature buys and 348 pending buys.
- Historical holdout: entries from October 24 UTC with a full horizon by the
  export end. Models train on labels available strictly before October 24,
  with group purging. Corrected training set: 7,472 buys; holdout: 3,731 buys.
- Latest forward diagnostic: supplied `fomo_buy_sell_notifications(2).csv`,
  1,177 callbacks, November 14–18 UTC (November 13–17 Pacific). Models refit on
  the older export only; 498 mature forward buys, 140 pending. Last historical
  policy label available November 10, before the first evaluated forward buy.
- Three models: bundled September model; same HGB architecture freshly trained
  on eventual-sell-only outcomes; same architecture trained on mature 24h policy
  outcomes. Recent adapters disabled for both fresh comparison models.
- No confidence, model hyperparameter, or policy search on these test periods.
  Confidence rank cutoff fixed at 0.80. The latest file was already inspected
  before this change, so this is a retrospective diagnostic, not a pristine
  prospective holdout. Original September training provenance cannot be
  independently reconstructed from the supplied exports.

## Wallet replay at zero assumed slippage

Each model starts with an independent $1,000 account in each period.

| Period / model | Buys executed | Net win rate | Final cash | Net P&L |
| --- | ---: | ---: | ---: | ---: |
| Historical / bundled | 128 | 46.1% | $151.61 | -$848.39 |
| Historical / fresh sell-only | 113 | 38.9% | $157.07 | -$842.93 |
| Historical / corrected 24h | 120 | 38.3% | $173.18 | -$826.82 |
| Latest / bundled | 72 | 22.2% | $161.97 | -$838.03 |
| Latest / fresh sell-only | 73 | 16.4% | $219.99 | -$780.01 |
| Latest / corrected 24h | 87 | 21.8% | $464.55 | -$535.45 |

The latest correction retains $302.58 more than the bundled model and $244.55
more than a fresh sell-only model, but still loses 53.5% of initial capital.
Historical improvement versus bundled is only $21.57. Win rate does not improve
consistently; lower losses are not proof of a reliable strategy.

At 1% assumed slippage per side, latest final balances are $162.48 bundled,
$266.28 fresh sell-only, and $514.24 corrected. Historical balances are $155.14,
$151.59, and $185.45 respectively. Higher slippage can improve aggregate P&L
because the fee/EV gate rejects more trades; this is selection, not better fills.

## Probability quality and uncertainty

Lower scores are better. Calibration error is the weighted absolute difference
between predicted and observed net-win rates in probability bins.

| Period / model | Brier score | Calibration error |
| --- | ---: | ---: |
| Historical / bundled | 0.2219 | 0.2549 |
| Historical / fresh sell-only | 0.1994 | 0.2122 |
| Historical / corrected 24h | 0.1400 | 0.0371 |
| Latest / bundled | 0.2702 | 0.3534 |
| Latest / fresh sell-only | 0.2093 | 0.2813 |
| Latest / corrected 24h | 0.1390 | 0.1200 |

Paired UTC-calendar-day bootstrap intervals for mean daily P&L improvement,
corrected versus bundled, at zero slippage:

- Historical, 17 calendar days: +$1.27/day; 95% interval **-$48.14 to +$60.09**.
- Latest, 4 calendar days: +$75.64/day; 95% interval **-$158.23 to +$428.25**.

Both include zero. Only four latest days are available, and days may also be
serially correlated. These intervals resample fixed replay ledgers, not complete
wallet paths, and cannot justify promotion.

## Material limitations

This is a controlled model comparison with fixed 1:20 sizing, NORMAL fee gate,
base reserve and exposure/burst caps, fees on both sides, and chronological
capital settlement. It does not reproduce Android adaptive modes, dynamic
reserve additions, stage changes, UI queues/failures, execution latency, or
actual fills. Sell fees are charged per lot, whereas Android can aggregate lots.
The replay's trade counts therefore differ from the observed 55-paper-buy run.

The 24h clock starts at buy capture, and expiry occurs at the scheduled deadline
strictly after 24h; Android starts at ledger opening and can sweep later. An exit
uses the first source sell observed by the deadline or a quote with both post
and capture times within five minutes. No quote means **assumed zero proceeds**,
not proof of worthlessness. History includes 4,727 such assumptions; latest
mature outcomes include 206. Feed gaps and absent stable token identity limit
what this data can prove about actual liquidation.

The historical holdout contains three gross-return rows above 100x, including a
maximum ROI around 5.66 million. Ticker-only matching or source-data errors may
explain these; they are unverified, retained, and flagged in the JSON. Absolute
ROI-error metrics are dominated by these values and should not be interpreted
as clean token-level market evidence. No such >100x rows occur in the latest
period. Mint/contract identity and measured executable prices remain necessary
before trusting real-money performance estimates.

## Reproduce

From `android-fomo-controller`, using the user's two raw exports:

```powershell
.\.learning-venv\Scripts\python.exe -m learning.backtest_policy `
  .\fomo_buy_sell_notifications.csv `
  '.\fomo_buy_sell_notifications(2).csv' `
  --holdout-start 2026-10-24 `
  --output .\local_learning_state\causal_24h_backtest.json
```

SHA-256 input identities are stored in the JSON. The command never deploys a
model. Use the raw-to-policy preparation and protected maintenance workflow in
[RECURSIVE_LEARNING.md](RECURSIVE_LEARNING.md) for future independent evidence.
