# Runtime workflow

## Notification capture

The app mirrors the important Tasker behavior:

1. Ignore packages other than family.fomo.app.
2. Read Notification.EXTRA_TEXT.
3. If Notification.EXTRA_BIG_TEXT exists and is nonblank, use it instead.
4. Thesis-only notification:
   - store locally as THESIS_IGNORED;
   - cancel that notification.
5. Bought/sold notification:
   - parse;
   - store in SQLite before any UI work.
6. Other Fomo notification:
   - leave alone.

## Trade parsing

Expected formats currently mirror the Copy analysis script:

Title:

~~~text
TOKEN at $47.3k MC
~~~

Text:

~~~text
@trader bought $837
~~~

or:

~~~text
@trader sold $500
~~~

The parser extracts:

~~~text
action
trader
coin
marketCap
sourceAmount
~~~

Bought notifications are passed to the embedded Adaptive Hybrid v2 model. The
model produces profitable-exit probability, predicted ROI, and a confidence
percentile from its frozen training reference.

## Selection

Buy selection then applies the active regime's confidence threshold, fee/EV
gate, cash reserve, total exposure, trader concentration, token concentration,
and 15-minute burst concentration. It searches progressively safer copy ratios
if the intended ratio does not fit.

Sells do not pass through the buy model. A sell is eligible only when the
strategy ledger contains a confirmed or paper position for the same trader and
token. The first later matching sell exits all matched copied lots, which is the
rule used to train and validate the model.

Exact duplicate action/trader/token/market-cap/source-amount notifications
within three minutes are ignored by the strategy layer but remain preserved in
the append-only raw recorder.

## Exact notification opening

The database persists the Android StatusBarNotification key.

When a queued event is executed, the NotificationListenerService searches its current activeNotifications for the same key and invokes that notification's contentIntent.

It does not:

- click the notification currently at the top of the shade;
- infer a URL;
- use the latest Fomo notification;
- fall back to an unrelated notification.

If the exact live notification is unavailable, execution fails closed.

## UI state machine

~~~text
QUEUED
  |
OPENING
  |
VERIFYING
  |
  +-- DRY_RUN --> DRY_RUN_VERIFIED
  |
  +-- PREPARE + bought
  |      |
  |   BUY_FIND_ENTRY
  |      |
  |   BUY_FILL_AMOUNT
  |      |
  |   PREPARED_BUY
  |
  +-- PREPARE + sold
         |
      SELL_FIND_ENTRY
         |
      SELL_FILL_AMOUNT
         |
      PREPARED_SELL
~~~

The coordinator runs one job at a time. Plans reserve cash and exposure before
UI work. DRY_RUN commits them to a paper ledger after page verification.
PREPARE leaves them reserved until the user confirms or rejects the completed
action from the dashboard.

## Failure behavior

Failures never guess an alternative action.

Examples:

- stale event -> EXPIRED
- notification gone -> INTENT_UNAVAILABLE
- wrong/unknown Fomo destination -> PAGE_MISMATCH/UI_TIMEOUT
- selectors blank -> SELECTORS_NOT_CALIBRATED
- missing control -> UI_TIMEOUT
- click/text action fails -> UI_ACTION_FAILED

The next queued event can proceed after a terminal failure.

## Why execution is local

The previous Tasker workflow used an HTTP response as part of notification clearing.

This prototype makes SQLite the first durable write, so an Apps Script/Google Sheets timeout cannot block local recognition or dry-run navigation.

Cloud logging can be added later as an independent sink.
