# Recursive learning and protected champion promotion

Copy keeps Adaptive Hybrid v2 as generation 0 and adds three feedback loops at different speeds.

| Loop | Runs | Learns from | Changes |
| --- | --- | --- | --- |
| Live risk | Every signal/exit | Drawdown, top-tail shadow outcomes, exposure and bursts | NORMAL / CAUTION / DEFENSIVE |
| Forecast maintenance | Local Windows maintenance cycle | Every resolved parsed BUY, including skipped buys | Stable HGB plus a capped recent adapter |
| Policy maintenance | After rolling-origin cross-fitting | Out-of-sample forecast predictions only | Confidence and sizing constraints |

Execution events record `PLANNED`, `PREPARED`, confirmation, rejection and failure transitions. They support latency and completion-rate calibration. Source-return-to-copied-return or slippage learning is deliberately disabled until actual fill price and proceeds are captured.

## Causal learning contract

Every complete, de-duplicated BUY is inserted into `learning_opportunities` before exclusion or policy selection. It remains `OPEN`, which means unknown, until the first later SELL with the same case-insensitive trader and token. That SELL resolves every earlier open buy for the pair.

Multiple buys resolved by one sell receive the same `lifecycle_group_id`. Training may retain the rows, but promotion and bootstrap sampling operate on lifecycle groups. This prevents one sell episode from masquerading as several independent holdout outcomes.

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

In the app, select **Export recursive-learning CSV**, then pull it:

```bat
adb pull /sdcard/Android/data/com.dwu.fomocontroller/files/Documents/copy_learning_opportunities.csv .
```

Run one maintenance cycle:

```bat
.learning-venv\Scripts\python.exe -m learning.run_maintenance copy_learning_opportunities.csv --state-dir local_learning_state
```

The cycle does not overwrite a champion merely because training completed. It:

1. validates the causal dataset and excludes unresolved rows;
2. reserves a chronological lifecycle cohort that has never evaluated a challenger;
3. trains the same HGB family on earlier evidence;
4. optionally trains a 7–10 day recent adapter, capped at 35% influence;
5. compares champion and challenger with a paired lifecycle bootstrap;
6. requires a positive 95% lower bound, a minimum economic improvement, no material Brier/ROI-MAE regression, and at least 10% coverage;
7. marks the cohort consumed whether the challenger wins or loses;
8. writes accepted and rejected artifacts to separate directories.

The default cycle requires 200 training rows and 40 independent evaluation lifecycles. Insufficient evidence produces `no_cycle`; it never manufactures negative labels from open buys.

`run_learning_loop.bat` is a convenience wrapper for the same command. It is intentionally one-shot so Task Scheduler can set the cadence and overlapping maintenance jobs can be prevented.

## Independent policy maintenance

First reconstruct leakage-safe predictions:

```bat
.learning-venv\Scripts\python.exe -m learning.crossfit_predictions copy_learning_opportunities.csv local_learning_state\crossfit.csv
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

Each accepted adapter increments `adapterPromotionsSinceCompaction` in the local registry. After roughly eight accepted adapters or fourteen days, run `train_forecast.py` against all resolved evidence and treat that artifact as an ordinary challenger. Compaction does not bypass a fresh one-use promotion cohort.
