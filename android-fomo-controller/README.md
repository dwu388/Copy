# Fomo Android Controller

Prototype Android companion app for running the Fomo notification workflow mostly inside an Android emulator.

## What this branch implements

- Watches Android notifications with NotificationListenerService.
- Accepts only package \`family.fomo.app\`.
- Prefers expanded notification text over regular notification text.
- Parses \`bought\`, \`sold\`, and thesis-only notifications.
- Appends every Fomo bought/sold notification to a raw local recorder before parsing, filtering, or UI automation.
- Preserves Android notification key, ID, tag, post time, capture time, title, normal text, expanded text, selected text, channel/group/category metadata, content-intent presence, and action count.
- Runs the fitted Adaptive Hybrid v2 classifier, ROI regressor, confidence rank,
  fee gate, sizing ladder, and NORMAL/CAUTION/DEFENSIVE controller locally.
- Keeps shadow outcomes for every model top-20% buy, including skipped copies.
- Maintains a separate confirmed/paper position ledger and never routes an
  unowned sell.
- Opens the exact live notification by invoking its own \`contentIntent\`.
- Uses an AccessibilityService to verify that the expected coin is visible after the notification opens.
- Supports OBSERVE, DRY_RUN, and PREPARE modes.
- PREPARE mode can be enabled only after Fomo resource IDs are calibrated.
- Never presses a final Review/Confirm/Submit control.

This is intentionally a reviewable prototype. Start in OBSERVE, then DRY_RUN. Do not use PREPARE until selector calibration is complete.

## Current Tasker parity

The implementation keeps the important behavior of the current Tasker workflow:

- non-Fomo packages are ignored;
- expanded text wins when present;
- bought and sold notifications are parsed;
- thesis-only notifications are cleared;
- unrelated Fomo notifications are left alone;
- important failures are preserved in the local event log.

Unlike the existing Tasker-to-Sheets path, execution does not wait for an HTTP request. The local SQLite database is the durable first write.

## Raw local buy/sell recorder

The controller now keeps two different local views inside `fomo_controller.db`:

- `recorded_notifications` is the append-only raw recorder. Every Fomo notification whose selected text contains the whole word `bought` or `sold` gets a new row before selection rules are applied.
- `events` remains the controller state table. It is keyed by Android notification key because automation state for a live notification is updated over time.

The raw recorder deliberately has no unique constraint on notification key. If Android/Fomo updates the same notification and the listener receives another qualifying callback, that callback is preserved as another raw row instead of replacing history.

A raw row is retained even when optional parsing is incomplete, Adaptive Hybrid
v2 rejects the opportunity, or the controller stays in OBSERVE mode. This keeps
trading decisions downstream from data capture.

From the app dashboard, **Export raw buy/sell CSV** writes the complete recorder history to the app's Documents directory as:

~~~text
fomo_buy_sell_notifications.csv
~~~

Typical emulator path:

~~~text
/sdcard/Android/data/com.dwu.fomocontroller/files/Documents/fomo_buy_sell_notifications.csv
~~~

Pull it to Windows with:

~~~bat
adb pull /sdcard/Android/data/com.dwu.fomocontroller/files/Documents/fomo_buy_sell_notifications.csv .
~~~

The dashboard clear button is intentionally limited to the controller state log. It does not erase the append-only raw recorder.

## Requirements

Recommended development stack:

- Android Studio Quail 4 / 2026.1.4 Patch 1 or later
- JDK 17
- Android SDK platform 37
- Android emulator with a Google Play system image
- Fomo installed and signed in inside the emulator

The project uses Android Gradle Plugin 9.4.0. Google documents Gradle 9.6.0 and JDK 17 for AGP 9.4.

## Download this branch

Clone only this branch:

~~~bat
git clone --branch feature/android-fomo-controller --single-branch https://github.com/dwu388/Copy.git
cd Copy\android-fomo-controller
~~~

Or use GitHub's Download ZIP option while viewing the \`feature/android-fomo-controller\` branch.

## First run

1. Open the \`android-fomo-controller\` folder in Android Studio.
2. Allow Gradle sync to finish.
3. Create a Pixel-style AVD with a Google Play system image.
4. Install Fomo in the emulator and sign in.
5. Confirm a normal Fomo notification arrives and tapping it opens the expected Fomo item.
6. Run this app on the same emulator.
7. In the app, tap **Notification access** and enable Fomo Controller.
8. Tap **Accessibility settings** and enable Fomo Controller.
9. Leave mode on **OBSERVE**.
10. Generate several real Fomo notifications and press **Refresh events**.
11. Confirm the local log matches the title/text/action you expect.
12. Switch to **DRY_RUN**. A qualifying notification should open its exact Fomo destination and stop after verifying the expected coin.

On some Android versions a sideloaded app's accessibility toggle can be blocked by "Restricted settings". If that happens, open the app's Android App Info screen, allow restricted settings, then return to Accessibility.

## Adaptive Hybrid v2 defaults

The embedded model is the selected `mild` v2 bundle trained through
`2026-09-22 21:09:30.822000`. Its inputs are trader, log entry market cap, and
log source buy amount. `DuckSoldier` is excluded by the fitted bundle.

The first launch uses the standard fee schedule
`max($0.95, 0.50% × order)` and OBSERVE mode. The dashboard can select the
validated 10%-code schedule `max($0.855, 0.45% × order)` and can change the
120-second maximum event age. Strategy thresholds and sizing are versioned with
the model asset rather than exposed as ad hoc dashboard overrides.

See `docs/ADAPTIVE_HYBRID_V2.md` for the complete controller specification.

## Selector calibration

The project does not guess Fomo's Buy/Sell resource IDs.

Before PREPARE mode can interact with a trade form:

1. Open the relevant Fomo screen manually.
2. From Windows run:

~~~bat
scripts\dump_fomo_ui.bat
~~~

3. Open \`scripts\fomo-ui.xml\`.
4. Find stable resource IDs for:
   - the control that opens the Buy form;
   - the control that opens the Sell form;
   - the amount input field.
5. Put those exact resource IDs into:

~~~text
app/src/main/java/com/dwu/fomocontroller/automation/FomoSelectors.kt
~~~

6. Rebuild and reinstall.
7. Test again in DRY_RUN.
8. Only then try PREPARE.

See \`docs/CALIBRATION.md\`.

## Modes

### OBSERVE

Captures, parses, scores, updates shadow outcomes, and logs. It does not reserve
capital, create copied positions, or open Fomo.

### DRY_RUN

For a qualifying event:

1. retrieves the same live Android notification by notification key;
2. invokes that notification's \`contentIntent\`;
3. waits for Fomo;
4. verifies the parsed coin is visible;
5. records \`DRY_RUN_VERIFIED\`;
6. records the selected action in the paper ledger;
7. performs no Buy/Sell click.

This is the recommended validation mode.

### PREPARE

Requires calibrated resource IDs.

For a qualifying event it:

1. opens the exact notification;
2. verifies the expected coin;
3. opens the calibrated Buy or Sell entry form;
4. attempts to put \`sourceAmount * copyRatio\` into the calibrated amount field;
5. stops and records \`PREPARED_BUY\` or \`PREPARED_SELL\`.

It does **not** press a final review, confirm, swap, submit, or transaction control.

After manually completing the prepared action in Fomo, use **Confirm latest
prepared trade executed**. If it was not completed, use **Reject latest prepared
trade**. Until one of those actions is chosen, its capital stays reserved. This
prevents later notifications from spending the same cash or selling a position
that was never acquired.

## Running without taking over Windows

All runtime notification and UI work happens inside Android.

Once validated, the emulator itself can be started without a visible window:

~~~bat
emulator @YOUR_AVD_NAME -no-window -no-audio
~~~

The controller does not send Windows mouse or keyboard input.

## Event states

Typical successful flow:

~~~text
RECEIVED
PARSED
QUEUED
OPENING
VERIFYING
DRY_RUN_VERIFIED
~~~

or, after selector calibration:

~~~text
RECEIVED
PARSED
QUEUED
OPENING
VERIFYING
BUY_FIND_ENTRY / SELL_FIND_ENTRY
BUY_FILL_AMOUNT / SELL_FILL_AMOUNT
PREPARED_BUY / PREPARED_SELL
~~~

Fail-closed states include:

~~~text
PARSE_FAILED
FILTERED
EXPIRED
INTENT_UNAVAILABLE
INTENT_CANCELED
PAGE_MISMATCH
SELECTORS_NOT_CALIBRATED
UI_ELEMENT_MISSING
UI_ACTION_FAILED
~~~

## Build from command line

This repository intentionally avoids committing a Gradle wrapper JAR. With Gradle 9.6 installed:

~~~bat
gradle -p android-fomo-controller assembleDebug
~~~

The APK will be under:

~~~text
android-fomo-controller\app\build\outputs\apk\debug\
~~~

Android Studio can also sync/build the project directly.

## Important limitations

- Fomo's actual accessibility hierarchy has not been observed from your emulator yet, so its resource IDs cannot be safely pre-filled.
- PREPARE therefore remains locked until you calibrate those IDs.
- The prototype does not infer whether a final on-chain/app transaction actually completed.
- PREPARE therefore requires explicit confirmation or rejection from the dashboard.
- The ledger estimates position value from the source notification's market-cap
  ratio. It is not an on-chain balance or fill-price reconciliation.
- Source data identifies tokens by ticker, not mint/contract. Same-ticker
  collisions remain a known risk and fail-safe automation still requires mint capture.
- It intentionally does not implement unattended final transaction submission.
