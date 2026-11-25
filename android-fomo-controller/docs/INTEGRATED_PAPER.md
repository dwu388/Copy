# Integrated paper strategy — 0.5.0

This PR restores the unmerged causal-learning correction and connects the
missing controls to Android. It does **not** establish a profitable model.
The corrected model abstains under the proposed preset in both historical
windows. Use the new run to collect forward evidence, including skipped buys.

## What is integrated

| Gap | Behavior in this version |
| --- | --- |
| Training selected only buys with later sells | Default raw conversion includes mature 24-hour timeouts, including assumed losses; incomplete horizons are withheld. Legacy sell-only conversion requires an explicit diagnostic option. |
| Future outcomes available during training splits | Training outcomes must be available before the decision boundary. Cross-fit, maintenance and evaluation use the causal preparation path. |
| Gross labels versus net execution costs | Reference target includes buy/sell fees; zero sale proceeds do not incur an unfunded exit fee. |
| Old September model still active | Locally built corrected paper artifact, trained on earlier mature outcomes, with held-out chronological probability calibration. Original production champion is retained. |
| No explicit entry group | Hard market-cap and source-purchase controls. Preset: cap **strictly below $50,000**, source purchase **at least $1,000**. The minimum is a fee-control starting point, not a proven optimum. |
| Confidence mistaken for positive return | MODEL requires conservative predicted return to exceed estimated round-trip burden for every order, in addition to confidence and existing risk checks. This is a conservative median-return margin, not a mathematical expected-profit guarantee. |
| No model-free comparison | DRY_RUN-only RULE_ONLY ignores forecasts/confidence but retains hard fee ceilings, reserves, concentration, regimes and sizing ladder. |
| Losses could continue consuming entries | Persistent, latched loss pause, default 20% from a separate estimated-equity peak. Stops buys; sells and 24-hour expiry continue. Manual paper acknowledgement creates an audited new anchor. |
| Old paper wallet contaminated a new comparison | Explicit archive/start button creates a new $1,000 paper session and resets that session's risk/shadow/candidate state. It preserves raw notifications, decisions, learning evidence and trader history. It blocks reset when confirmed positions or PREPARED plans exist. |
| Trader history ignored | Existing OFF/WARN/FILTER remains; preset WARN at 50%. FILTER requires sufficient completed reference episodes. All source opportunities continue building independent history, even when no copy is made. |
| Weak promotion criteria | Absolute positive net utility and a positive bootstrap lower bound are required in addition to comparison with the champion. The paper artifact bypasses no production promotion gate. |

Existing 1:20 start, risk-driven ratio reductions, promotion to 1:15 / 1:10,
reserves, concentration limits, ten-exit promotion requirement, mode demotion,
24-hour paper write-offs, notification clearing and screenshot execution
workflow remain. Changing strategy settings mid-session mixes experiments;
start an explicit new session for a clean comparison.

The new defaults apply on upgrade unless the new controls already have saved
values. Installing preserves the current wallet; only the archive/start button
creates a fresh wallet. That button applies the listed preset and enters DRY_RUN.
It does not reset fee discount or trader history. Set fee discount to 0% to match
the reference replay. RULE_ONLY and the corrected artifact are paper-only;
PREPARE still uses the regular champion.

## Model and time boundaries

Private fitted weights are excluded from GitHub and ignored by Git. The builder
creates `app/src/main/assets/corrected_paper_v1.json.gz` locally before Gradle
packages the APK. This step is required for the corrected preset. The artifact
is marked `paperOnly` and `PAPER_ONLY_NOT_PROMOTED`.
Trees train on 9,253 mature outcomes available before November 1 UTC. They are
then frozen. A logistic calibration map and confidence reference use 2,166
later mature outcomes, through November 10 18:23:38 UTC. Calibration rows are
not an untouched performance test. The builder embeds seven scored fixtures. Local generated-artifact parity was
checked in Python and Kotlin; committed CI tests exercise the calibration math
with fixed synthetic parameters on the already-public base artifact. Missing/invalid corrected artifacts block MODEL paper entries.

The October 24 holdout uses a **separately trained earlier model**, with outcomes
available only before that holdout. The locally built November model never scores
that earlier holdout in the reported comparison. Raw file and artifact hashes,
training cutoffs, assumptions and detailed counts are in
[integrated_paper_results.json](integrated_paper_results.json).

A 24-hour assumed loss is a deliberate accounting convention, not proof that a
token became worthless. Training labels use the scheduled capture-time deadline;
Android and the full replay expire on subsequent processing/sweeps. The sweep
and fill timing difference is still an approximation. No model can know future
sells; including mature unsold buys removes that selected-outcome bias without
eliminating uncertainty.

## Backtest results

Independent $1,000 wallets, zero fee discount, immediate source-notification
market-cap fills, complete controller/risk ladder. These files were previously
inspected, so this remains a retrospective comparison. The older window starts
October 24 UTC and ends with the November 10 file; the latest window is the
separate November 14–18 UTC file. New entries stop 24 hours before each file's
end so outcomes can mature. Trader guard starts cold in each replay; device
history is preserved and can therefore produce different FILTER decisions.

| Strategy | Earlier buys | Earlier net P/L | Latest buys | Latest net P/L |
| --- | ---: | ---: | ---: | ---: |
| Legacy main 0.4, existing WARN selection | 115 | -$900.52 | 53 | -$686.33 |
| Legacy + new safety, cap < $100k / source >= $1k | 27 | +$206.63 | 7 | -$164.20 |
| Corrected paper preset, cap < $50k / source >= $1k | 0 | $0.00 | 0 | $0.00 |
| Corrected preset + trader FILTER 50% | 0 | $0.00 | 0 | $0.00 |
| Corrected preset + trader FILTER 40% | 0 | $0.00 | 0 | $0.00 |
| Corrected comparison, cap < $100k / source >= $1k | 0 | $0.00 | 0 | $0.00 |
| RULE_ONLY < $50k, no source minimum, hard fee ceiling | 73 | +$289.42 | 16 | -$200.43 |

With the 1% slippage setting, earlier/latest P/L is respectively:
legacy -$309.05/-$629.02; legacy+safety +$185.44/-$166.51;
RULE_ONLY +$342.43/-$114.25; corrected variants $0/$0.
Slippage changes eligibility, regimes and the sequence of trades, so it can
increase total replay profit by removing losing entries. That is path dependence,
not evidence that execution costs help. As in the Android controller, this
setting adds two sides to the entry cost gate and applies a haircut to exit
proceeds; it does not reconstruct an entry-price fill. A 20% entry pause is not a guaranteed
maximum drawdown: existing positions, sparse quotes and discrete losses can
carry losses beyond the trigger.

There is **no consistently profitable winner** here. The corrected model's
negative/insufficient return margins and confidence checks cause abstention;
this is not a 100% win rate. We retain the conservative gates instead of tuning
them until a previously seen file produces trades.

Reproduce from the Android project directory (Python dependencies in
`learning/requirements.txt`):

```powershell
python -m learning.build_paper_experiment "PATH_TO_OLDER_RAW.csv"
python -m learning.backtest_integrated_paper "PATH_TO_OLDER_RAW.csv" "PATH_TO_LATEST_RAW.csv" --output docs/integrated_paper_results.json
python -m unittest discover -s learning/tests -v
```

The historical component scripts/reports predate this preset. Use
`backtest_integrated_paper` for these integrated scenarios.

## Install onto a Windows-connected Android device

Merge the PR into GitHub `main` first. These commands assume your existing
checkout and debug signing key. A different signing key causes install failure;
keep the current data backup and use the same development machine/key.
Do not uninstall or clear app data to work around an install error.

From PowerShell:

```powershell
Set-Location "C:\Users\yiumi\Desktop\Copy\android-fomo-controller"
$serial = "ZY22L495GB"
adb devices

# Stop the app and preserve its private databases/settings before the upgrade.
adb -s $serial shell am force-stop com.dwu.fomocontroller
$backup = "copy-before-integrated-$(Get-Date -Format yyyyMMdd-HHmmss).tar"
cmd /c "adb -s $serial exec-out run-as com.dwu.fomocontroller tar -cf - . > $backup"
if ($LASTEXITCODE -ne 0) { throw "Backup failed; resolve before installing." }

# Pull the merged version. Resolve any local Git changes before continuing.
git fetch origin
git switch main
if ($LASTEXITCODE -ne 0) { throw "Could not switch to main." }
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) { throw "Could not update main." }

# Build private paper weights locally using the OLDER supplied raw export
# (Oct 3–Nov 10), not the shorter November 14–18 test file.
# Edit this path to where you saved that older CSV.
$rawTraining = ".\fomo_buy_sell_notifications.csv"
if (!(Test-Path $rawTraining)) { throw "Set rawTraining to the older raw CSV." }
if (!(Test-Path ".\.learning-venv\Scripts\python.exe")) {
    py -3 -m venv .learning-venv
    if ($LASTEXITCODE -ne 0) { throw "Python environment creation failed." }
}
.\.learning-venv\Scripts\python.exe -m pip install -r .\learning\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }
.\.learning-venv\Scripts\python.exe -m learning.build_paper_experiment $rawTraining
if ($LASTEXITCODE -ne 0) { throw "Paper model build failed; do not install." }

# Android Studio's bundled Java, if Java is not already configured.
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat testDebugUnitTest assembleDebug
if ($LASTEXITCODE -ne 0) { throw "Build/test failed; do not install an older APK." }

adb -s $serial install -r ".\app\build\outputs\apk\debug\app-debug.apk"
if ($LASTEXITCODE -ne 0) { throw "Install failed." }
adb -s $serial shell dumpsys package com.dwu.fomocontroller | Select-String 'versionName|versionCode'
adb -s $serial shell am start -n com.dwu.fomocontroller/.MainActivity
```

Expected version: **0.5.0**, versionCode **7**. Install SDK Platform 37 and any
requested build tools in Android Studio's SDK Manager if Gradle reports missing
SDK components. Adapt the Java path if Android Studio is installed elsewhere.
To test before merging, switch to `feature/integrated-paper-strategy` instead
of `main`; do not call an unmerged branch the new main.

In the app:

1. Confirm notification access and Accessibility are enabled. Keep Fomo signed
   in, the device unlocked and battery restrictions disabled for the controller.
2. Set fee discount to 0% and save to match the replay.
3. Tap **Apply paper preset + archive/start new $1,000 session** once. Confirm
   DRY_RUN, PAPER EXPERIMENT, corrected model ID, $1,000 cash, no open positions,
   market cap < $50,000, source >= $1,000, trader WARN and 20% loss limit.
4. Leave DRY_RUN running. Selected notifications use the existing Fomo open /
   coin verification / screenshot path and paper accounting. No qualifying
   buys means no trade screenshots; inspect skip reasons before assuming the
   listener failed. The corrected preset may legitimately make no trades.
5. Export raw buy/sell, recursive-learning, execution-calibration and trader /
   paper-safety audit files. The last export includes `paper_sessions.csv` and
   `entry_circuit_events.csv`. Retain all opportunities, including skipped buys.

For a separate rule-only experiment, first select OBSERVE and Save settings.
Set entry style RULE_ONLY, cap $50,000, source minimum 0, loss limit 20% and
trader WARN; Save settings, then tap **Archive/start $1,000 paper session using
saved settings**. This path does not need private weights or Python. To compare
the public bundled model, use MODEL + LEGACY instead. Save while OBSERVE before
starting the new session, so notifications cannot enter under mixed settings.
Do not combine its results with corrected MODEL or compare different live
periods as simultaneous controlled trials. The hard fee ceiling can still
reject small trades; RULE_ONLY does not mean every sub-$50k notification is copied.

When paused, sells/expiry continue. Resume only by the explicit paper
acknowledgement button; it records a new loss anchor. Unpriced positions stay
at cost until a fresh notification or expiry, and the UI reports their count.
This equity estimate is not a live executable portfolio valuation.

## Validation and remaining limits

45 Python tests and 28 JVM tests passed; all Android Kotlin source and test files
compiled against Android 16 platform classes. Tests cover the original model,
synthetic calibrated-model parity, entry boundaries, strict margins, loss latch,
trader-history behavior, additive database schema and causal labels.
The full Gradle APK build could not run here because the wrapper distribution
download failed with `Network is unreachable`. Run the Gradle commands above
locally; APK packaging, Android migration execution and device UI behavior still
need that local check.

No actual fills, mint/contract identity, executable liquidity, source partial
quantity or UI execution failure measurements are present in the supplied CSV.
The new calibration map is static, not online adaptive calibration. The
corrected artifact is a paper challenger and is not automatically promoted to
PREPARE or to a production forecast champion. Further features need fresh
forward outcomes and the promotion checks, rather than another search over the
same recent trades.
