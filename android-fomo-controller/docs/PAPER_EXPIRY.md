# Maximum paper holding period: 24 hours

In DRY_RUN, positions backed by a PAPER_EXECUTED buy are closed at the first
sweep strictly after 24 hours from their ledger opened_time. Checks run at
startup, strategy evaluation, dashboard status refresh, and the notification
listener's three-minute cleanup interval. Android may suspend or stop the app;
overdue positions catch up on the next running check. This is not an exact alarm.
OBSERVE/PREPARE do not perform these write-offs. CONFIRMED real-trade buys are
never selected by this rule.

Valuation uses the latest captured Fomo market cap for the same case-insensitive
trader/token pair. Both the source post time and capture time must be within the
last five minutes, with no future timestamps. A positive finite quote gives the
usual market-cap return proxy, slippage and sell fee. Multiple overdue lots for
one pair share one synthetic paper exit/fee; younger lots stay open.

If no fresh usable quote exists, proceeds and exit fee are zero. This is a
**conservative assumed full loss**, not evidence that the token is actually
worthless or delisted. A missing feed, inactive notifications, parsing failure,
or unavailable valuation can cause this result. The reason is explicitly
TIMEOUT_24H_FULL_LOSS_NO_FRESH_QUOTE, versus
TIMEOUT_24H_FRESH_NOTIFICATION_MARK for a usable mark. Entry fees were already
charged and are not charged again. Open principal is removed; it is not refunded
on a full loss. Closed positions cannot be credited again by a later source sell.

Each exit stores a synthetic sold plan, linked positions, decision with model
IDs and an execution event. Existing PLANNED/PREPARED sell plans linked to an
expiring position are canceled before settlement to avoid double credit.
All strategy updates are in one SQLite transaction. No schema migration or
wallet reset is required. Existing overdue paper positions are included.

Synthetic timeouts do not become observed source SELL labels in the learning
dataset. OPEN learning opportunities are distinct from paper holdings and may
remain unresolved until a source SELL is observed. Do not use the old first-sell
training/backtest results as validation of this new timeout exit policy.

## Reopening the coin

The raw log preserves the original buy notification key, but the original
PendingIntent is owned by Fomo and is not a durable stored coin address. Once the
notification is canceled or the process restarts, a saved key does not recreate
its tap action. The CSV contains no contract address or stable deep link, and
ticker matching alone is ambiguous. This change therefore does not claim to
reopen an expired notification, scrape an uncalibrated page, or confirm a live
market quote. Durable contract identity/deep-link capture and a calibrated price
reader are needed for that follow-up. Timeout exits are ledger-only and do not
trigger screenshots or place any real sell order.
