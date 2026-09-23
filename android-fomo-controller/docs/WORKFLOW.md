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

The configured copy ratio derives:

~~~text
copyAmount = sourceAmount * copyRatio
~~~

## Selection

Current prototype qualification:

~~~text
marketCap < maxMarketCap
AND
sourceAmount < maxSourceAmount
~~~

This is applied to both bought and sold notifications in v0.1.

That is intentionally simple and should be reviewed before using PREPARE for exits. A future version should keep an authoritative local copied-position ledger and route sells based on actually acquired position quantity.

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

Every job is immutable in the database and the coordinator runs one job at a time.

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
