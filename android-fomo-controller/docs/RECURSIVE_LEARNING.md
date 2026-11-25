# Recursive learning and protected champion promotion

Copy keeps Adaptive Hybrid v2 as generation 0 and adds three feedback loops at different speeds.

| Loop | Runs | Learns from | Changes |
| --- | --- | --- | --- |
| Live risk | Every signal/exit | Drawdown, top-tail shadow outcomes, exposure and bursts | NORMAL / CAUTION / DEFENSIVE |
| Forecast maintenance | Local Windows maintenance cycle | Every mature 24-hour BUY, including skipped buys and assumed timeout losses | Stable HGB plus a capped recent adapter |
| Policy maintenance | After rolling-origin cross-fitting | Out-of-sample forecast predictions only | Confidence and sizing constraints |

Execution events record `PLANNED`, `PREPARED`, confirmation, rejection and failure transitions. They support latency and completion-rate calibration. Source-return-to-copied-return or slippage learning is deliberately disabled until actual fill price and proceeds are captured.

## Causal learning contract

Every complete, de-duplicated BUY is inserted into `learning_opportunities` before exclusion or policy selection. It remains `OPEN`, which means unknown, until the first later SELL with the same case-insensitive trader and token. That SELL resolves every earlier open buy for the pair.

Multiple buys resolved by one sell receive the same `lifecycle_group_id`. Training may retain the rows, but promotion and bootstrap sampling operate on lifecycle groups. This prevents one sell episode from masquerading as several independent holdout outcomes.

The source-sell export is an audit of observed source actions, **not the training
target**. Since `notification_24h_net_reference_v1`, training requires a prepared
raw-notification dataset. Each buy must have a full 24-hour observation window,
even if a sell arrived early. At the scheduled deadline, a same-pair quote with
both capture and post time within five minutes supplies a mark; otherwise zero
proceeds are an explicit assumed write-off. Later source sells remain audit
fields and never overwrite the deadline outcome. `PENDING` buys have no label.
The classifier predicts a positive net return at reference size source/20 with
standard separate buy/sell fees. The regressor retains gross policy ROI because
Android's fee gate subtracts its own fee burden. This reference target does not
guarantee net profitability at other sizes or fee discounts.

Decision availability is `captured_time`; future-dated posts are excluded.
`label_available_time` is the full-horizon deadline. Every train/evaluation split
requires training labels strictly before evaluated buy times and purges shared
lifecycle groups. Old source-only inputs now fail with migration instructions.
Changing label contracts forces a full model fit rather than blending a new
target into old trees. Promotion also requires a positive absolute net utility
bootstrap lower bound, not merely an improvement over a losing champion.

The Android export contains model and policy IDs used when each prediction was made. It contains no account credentials. Treat the exported opportunity and execution CSVs as private data and do not commit them.

## Android database migration

`adaptive_hybrid_v2.db` is version 2. The v1→v2 migration is additive:

- adds `forecast_model_id` and `policy_model_id` to existing decisions;
- adds `learning_opportunities` and lifecycle indexes;
- adds append-only `execution_events`.

Existing wallet, positions, plans, decisions and shadow outcomes are preserved.

## Local forecast maintenance

Install the local trainer dependencies in a dedicated environment:

```bat
cd android-fomo-controller
py -3 -m venv .learning-venv
.learning-venv\Scripts\python.exe -m pip install -r learning\requirements.txt
```

Export the app's **raw notification CSV**, then pull it (use your actual export path):

```bat
adb pull /sdcard/Android/data/com.dwu.fomocontroller/files/Documents/fomo_buy_sell_notifications.csv .
```

Run one maintenance cycle:

```bat
.learning-venv\Scripts\python.exe -m learning.prepare_raw_notifications fomo_buy_sell_notifications.csv local_learning_state\policy_learning.csv
.learning-venv\Scripts\python.exe -m learning.run_maintenance local_learning_state\policy_learning.csv --state-dir local_learning_state
```

The cycle does not overwrite a champion merely because training completed. It:

1. validates the policy-label contract and excludes pending observation windows;
2. reserves a chronological lifecycle cohort that has never evaluated a challenger;
3. trains the same HGB family on earlier evidence;
4. optionally trains a 7–10 day recent adapter, capped at 35% influence;
5. compares champion and challenger with a paired lifecycle bootstrap;
6. requires positive relative and absolute utility lower bounds, a minimum economic improvement, no material Brier/ROI-MAE regression, and at least 10% coverage;
7. marks the cohort consumed whether the challenger wins or loses;
8. writes accepted and rejected artifacts to separate directories.

The default cycle requires 200 training rows and 40 independent evaluation
lifecycles. Insufficient evidence produces `no_cycle`. Timeout negatives are
explicit conservative policy assumptions after a full window, not claims that
the source asset was worthless. Ticker identity, missing feed coverage, actual
fills, and exact Android expiry-sweep times remain unavailable.

`run_learning_loop.bat RAW_NOTIFICATION_CSV` prepares these labels before the
maintenance command. It is one-shot so Task Scheduler can set the cadence.

## Independent policy maintenance

First reconstruct leakage-safe predictions:

```bat
.learning-venv\Scripts\python.exe -m learning.crossfit_predictions local_learning_state\policy_learning.csv local_learning_state\crossfit.csv
```

Then evaluate a policy challenger on its own untouched lifecycle cohort:

```bat
.learning-venv\Scripts\python.exe -m learning.train_policy local_learning_state\crossfit.csv --state-dir local_learning_state
```

Forecast fitting never sees policy final-evaluation outcomes through in-sample predictions. Policy cohorts have a separate one-use registry and promotion history. The first policy search is intentionally narrow: it changes confidence and ratio floors while retaining reserve, concentration, fee, uncertainty and regime protections.

After either champion changes, package the current pair:

```bat
.learning-venv\Scripts\python.exe -m learning.package_champions --state-dir local_learning_state
```

## Android activation and rollback

The packaged directory is `local_learning_state\deployment\staging` and contains:

```text
champion_manifest.json
forecast.json.gz
policy.json
```

Stage all three files together under the app's internal `files/models/staging` directory. On the next process start, Android:

1. verifies the manifest schema and safe relative paths;
2. verifies SHA-256 for both artifacts;
3. parses independent forecast and policy schemas;
4. enforces the recent-adapter 35% cap;
5. runs stored Python/Kotlin parity fixtures and sanity checks;
6. moves the current active pair to `previous` and atomically renames the complete staging directory to `active`.

Loading order is `active → previous → bundled generation 0`. A corrupt, incomplete or incompatible pair cannot replace the running champion. The dashboard shows both active IDs and whether they came from `active`, `previous`, or `bundled`.

An example debug-emulator staging flow is:

```bat
adb push local_learning_state\deployment\staging /data/local/tmp/copy-champion
adb shell run-as com.dwu.fomocontroller mkdir -p files/models
adb shell run-as com.dwu.fomocontroller cp -R /data/local/tmp/copy-champion files/models/staging
adb shell am force-stop com.dwu.fomocontroller
adb shell monkey -p com.dwu.fomocontroller 1
```

Use this only with a debuggable build where `run-as` is available. Production delivery should copy the same complete directory through an authenticated app update channel rather than making model files public.

## Full compaction

Each accepted adapter increments `adapterPromotionsSinceCompaction` in the local registry. After roughly eight accepted adapters or fourteen days, train a full model against mature policy evidence and treat it as an ordinary challenger. Compaction does not bypass a fresh one-use promotion cohort. Existing consumed source-sell cohorts stay consumed; changing the target does not make previously inspected data pristine.

## Controlled retrospective backtest

```bat
.learning-venv\Scripts\python.exe -m learning.backtest_policy older_raw.csv latest_raw.csv --holdout-start 2026-10-24 --output local_learning_state\backtest.json
```

This compares bundled, fresh sell-only, and corrected policy models on equal
mature outcomes. It reports both an older entry-time holdout and the latest
period with models refit only on the older export. Hyperparameters and confidence
thresholds are fixed. A $1,000 wallet uses fixed 1:20 sizing, NORMAL risk caps,
fees, chronological exits, and 0%/1% per-side slippage scenarios. Adaptive modes,
stage promotion, dynamic reserve additions, UI failures, and actual fill prices
are not simulated. Per-lot sell fees can exceed Android's aggregated fees.
Day-block intervals resample fixed replay P&L, not complete new wallet paths.
The scheduled capture-time deadline approximates Android's delayed paper sweep.
No model is deployed by this diagnostic. See `CAUSAL_24H_BACKTEST.md` for the
recorded results and their limitations.
