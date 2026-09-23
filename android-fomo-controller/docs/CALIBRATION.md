# Fomo selector calibration

The notification side can be built without knowing Fomo's internal UI IDs. The PREPARE UI path cannot.

The repository therefore intentionally ships with blank Fomo selectors and fails closed if PREPARE is selected before calibration.

## 1. Validate notification capture first

Use OBSERVE mode until events show:

- action
- trader
- coin
- market cap
- source amount
- Android notification key/state

If the parsed title or text format differs from your real notifications, update TradeParser before touching UI automation.

## 2. Validate exact-notification opening

Switch to DRY_RUN.

A qualifying new notification should:

1. be stored locally;
2. move to QUEUED;
3. invoke the exact live notification's contentIntent;
4. open Fomo;
5. verify that the parsed coin appears in Fomo;
6. finish as DRY_RUN_VERIFIED.

If it becomes INTENT_UNAVAILABLE, confirm the notification is still active and has a body contentIntent.

If it becomes PAGE_MISMATCH, inspect what the notification actually opens.

## 3. Dump the Fomo hierarchy

Manually navigate to the screen that contains the Buy/Sell controls, then run from the repository root:

~~~bat
android-fomo-controller\scripts\dump_fomo_ui.bat
~~~

This writes:

~~~text
android-fomo-controller\scripts\fomo-ui.xml
~~~

Search the XML for:

~~~text
text="Buy"
text="Sell"
resource-id=
class="android.widget.EditText"
clickable="true"
~~~

Prefer a stable resource-id over visible text or absolute coordinates.

## 4. Fill FomoSelectors.kt

Edit:

~~~text
app/src/main/java/com/dwu/fomocontroller/automation/FomoSelectors.kt
~~~

Example only:

~~~kotlin
const val BUY_ENTRY_RESOURCE_ID = "family.fomo.app:id/buy_button"
const val SELL_ENTRY_RESOURCE_ID = "family.fomo.app:id/sell_button"
const val AMOUNT_INPUT_RESOURCE_ID = "family.fomo.app:id/amount_input"
~~~

Do not copy these example IDs unless your UI dump actually contains them.

## 5. Rebuild and test PREPARE

PREPARE stops after it:

- verifies the coin;
- opens the corresponding Buy/Sell entry form;
- fills the calculated amount.

It intentionally does not press a final transaction control.

## If the hierarchy contains no useful IDs

Possible reasons include Compose/WebView/custom-rendered UI.

Next options, in order:

1. use content descriptions if stable;
2. use accessibility text plus parent/child relationships;
3. inspect the WebView hierarchy if exposed;
4. add bounded coordinate/gesture fallback only after validating screen resolution and layout.

Do not add unconditional screen coordinates as the first selector method.
