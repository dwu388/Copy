# Trader win-rate warning and eligibility guard

Version 0.4.0 adds an independent historical check to the current bundled
Adaptive Hybrid strategy. It does not replace the model, add market-cap/buy-size
filters, change the sizing ladder, or activate the experimental model in PR #21.

## Settings and default behavior

The dashboard has a **Trader win-rate guard** selector and **Minimum recent net
win rate (%)** setting. Save settings after changing them.

| Mode | Low win rate | Fewer than 20 known episodes | History failure |
| --- | --- | --- | --- |
| OFF | Allow the existing model/policy to decide | Allow | Allow |
| WARN (default) | Attach an audit warning; retain existing decisions | Warn; retain decisions | Warn; retain decisions |
| FILTER | Skip the buy | Skip the buy | Skip the buy |

Default threshold: 50%, inclusive. Exactly 10 wins in 20 episodes passes. The
window and minimum sample are both 20. This is an observed historical rate,
**not an estimated probability that the next trade will win**. Sells and paper
expiry are never gated. A guard storage error marks its history unavailable
persistently, but does not disable position exits or the original wallet logic.
Reset the guard history after repairing the cause to collect a new trusted
window; it does not silently resume using a potentially incomplete record.

A separate `trader_guard.db` stores opportunities, immutable settled episodes,
and append-only assessments. Existing wallet/position/model databases are not
migrated or cleared. History starts with this update, persists across process
restarts, and is not backfilled from the old source-only learning labels.
FILTER therefore waits until each trader has 20 completed episodes. WARN is the
default because the available backtests do not validate a new profitable edge.
The reset button clears only this guard's data, including its audit history;
FILTER then waits for a new 20-episode window.

## What counts as a win

Every complete, strategy-deduplicated BUY is tracked before model exclusion or
eligibility filtering, including buys that are not copied. The first observed
SELL for the same normalized trader/ticker closes its earlier, unexpired guard
buys. All buys closed by one sell form **one episode**, so a repeated-buy cluster
does not count as dozens of independent wins. A win requires positive aggregate
reference net profit; breakeven is not a win.

Reference principal is each source buy's dollars / 20. Standard per-lot fees are
`max($0.95, 0.5% of value)` on entry and exit, irrespective of the account's
current ratio or fee discount. Proceeds cannot be negative after fees. This
is deliberately a stable source-policy proxy, not actual copied-fill P&L.

Unclosed guard buys expire strictly after 24 hours. An expiry sweep groups
currently overdue buys by trader/ticker into one episode and uses the latest
raw notification quote whose **post and capture timestamps are both within five
minutes**. No fresh usable quote means assumed zero proceeds, not evidence of
worthlessness. Expiry runs in OBSERVE, DRY_RUN, and PREPARE, independently of
whether positions were copied. A later sell cannot replace a settled timeout.

The episode becomes known at the actual source-processing or expiry-sweep time;
expiry outcomes are not backdated to the deadline. A buy uses only episodes
with a strictly earlier known time. Matching is ticker-based because contracts
are absent from the source data. Missing notifications and ticker collisions
limit the economic interpretation of the result.

Blocked traders can recover: skipped source buys continue to create history,
and their sells/timeouts can replace old outcomes in the recent 20-episode
window. Existing model-qualified shadow observations also continue to update
risk modes even when FILTER blocks a copied entry.

## Audit and export

Each buy's event audit includes guard mode, status, wins/sample count, historical
win rate, and threshold. A blocked entry has reason `TRADER_WIN_RATE_LOW`,
`TRADER_WIN_RATE_INSUFFICIENT`, or `TRADER_WIN_RATE_UNAVAILABLE`. The independent
assessment journal also covers buys skipped by the model itself; assessments
are eligibility checks, not proof that a trade executed.

**Export trader win-rate audit** writes two CSVs to the app's Documents export
directory:

- `trader_guard_assessments.csv`: source opportunity key, assessment time,
  configuration, sample counts, status, and allowed flag.
- `trader_guard_episodes.csv`: immutable episode results, observation time,
  source-sell/timeout kind, net reference dollars, and lot count.

Export these alongside the raw recorder, learning, and execution CSVs. Reset
only clears guard history; it does not clear the raw recorder or wallet.

## Reproduce the backtest

From `android-fomo-controller`, with the existing Python learning environment:

```powershell
.\.learning-venv\Scripts\python.exe -m learning.backtest_trader_guard `
  .\fomo_buy_sell_notifications.csv `
  '.\fomo_buy_sell_notifications(2).csv' `
  --older-start 2026-10-24 `
  --output .\local_learning_state\trader_guard_backtest.json
```

The backtest replays the **current bundled September model** and the full
adaptive policy, rather than training a new model or using the earlier direct
copy-only strategy. Source post time supplies existing policy timing;
capture time establishes guard evidence availability. Source sells and regular
three-minute/notification/status sweeps advance history and paper holdings.
Only buys with a full 24-hour observation horizon enter the compared wallets.
All observed buys can inform trader history, regardless of copy eligibility.
The simulator follows the existing candidate-size reserve history reconstructed
from intended-size decisions. Fees on a source sell aggregate the actual copied
lots; guard reference fees remain per lot by definition.

Each portfolio starts independently at $1,000. Cold history starts at the
portfolio start, matching a newly installed guard. The older warm-history
sensitivity uses earlier rows in the **same export** only. No histories or
synthetic outcomes are invented across the missing interval between exports.
Thresholds 40% and 50% are fixed comparisons, not a tuning search. Optional
`--max-market-cap` is a counterfactual entry scope only; the guard's history
still observes all valid source opportunities.

The bundled inference scores are computed separately for each raw callback,
so an updated notification cannot overwrite an earlier callback's prediction.
Raw fingerprint deduplication matches the strategy's three-minute source-time
rule. The built-in invariant requires OFF and WARN to have identical copied
ledgers, sizing, and balances.

### Zero-slippage results

Aggregate output and input SHA-256 values are committed in
[trader_guard_backtest_results.json](trader_guard_backtest_results.json).
Private raw exports and local experimental models are not committed.

| Mode / threshold | Older final cash (cold history) | Older copies | Latest final cash (cold history) | Latest copies |
| --- | ---: | ---: | ---: | ---: |
| OFF | $99.48 | 115 | $313.67 | 53 |
| WARN / 50% (default) | $99.48 | 115 | $313.67 | 53 |
| FILTER / 50% | $1,533.98 | 109 | $1,000.00 | 0 |
| FILTER / 40% | $1,682.67 | 155 | $850.81 | 13 |

The older period has 3,731 eligible buys; the latest has 498. FILTER/50% has 67
net winners in 109 older copies, but **takes no latest trades**: 376 latest
opportunities have insufficient histories and 122 have low recent rates. The
$1,000 latest result is abstention, not profitable prediction. FILTER/40% has
only one net winner in its 13 latest copies. It preserves more capital than the
unfiltered model but still loses money.

With older same-export warmup, FILTER/50% ends at $1,347.25 on 104 copies and
FILTER/40% ends at $1,366.39 on 142 copies. OFF/WARN remain unchanged. At a 1%
configured paper slippage setting, cold-history older balances are $690.95 OFF/
WARN, $1,494.23 FILTER/50%, and $1,477.90 FILTER/40%; latest balances are $370.98,
$1,000.00, and $867.43 respectively. Higher assumed slippage can improve total
results because the fee gate rejects a different set of trades; it is not an
improvement in execution quality. The replay matches the current paper code's
exit-side haircut and two-side fee-burden approximation, not measured fills.

These are retrospective policy-rule simulations. Immediate fills, missing UI
failures/reservations, unknown Android suspension/sweep timing, unverified market
caps, source partial-sale quantities, assumed write-offs, and ambiguous ticker
identity prevent an exact reconstruction of the observed paper wallet. The
latest export was already inspected in earlier analyses. These results neither
prove future profit nor automatically promote any model or threshold. Default
WARN adds evidence without claiming that a blocking threshold has been validated.

## Validation and installation

Python tests cover the history lifecycle, strict availability, grouped source
sells, timeout immutability, recovery from skipped buys, duplicate identity,
quote freshness, OFF/WARN ledger identity, source-callback prediction identity,
wallet reconciliation, ladder promotion/demotion, and actual Android SQLite DDL.
A shared JSON fixture is executed by Python and Kotlin tests for net valuations
and eligibility decisions.

All app and unit-test Kotlin sources compiled with Kotlin 2.2.21, JVM target 17,
and Android 16/Robolectric platform classes. All 22 JVM/JUnit tests and 28 Python
tests passed. The normal Gradle wrapper could not download its distribution
using this environment's Java network path, so a full AGP build/API-37 APK was
not produced here. The fallback compiler/JUnit run is not an APK/device test.

After checking out this branch, build and test using the configured Android SDK
on Windows, then install without clearing app data:

```powershell
.\gradlew.bat testDebugUnitTest assembleDebug
adb -s ZY22L495GB install -r .\app\build\outputs\apk\debug\app-debug.apk
```

Open the dashboard, retain **WARN** to collect/audit history, or explicitly
select **FILTER** and save the desired threshold. The existing model, position
exits, and risk ladder remain active.
