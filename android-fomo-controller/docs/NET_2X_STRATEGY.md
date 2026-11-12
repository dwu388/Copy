# Notification boosted-tree strategy for estimated net 2× returns

## Target and what the data establishes

The classifier predicts `P(net return multiple >= 2)` at the **first available
later SELL by the same normalized trader and ticker**. A separate HGB regressor
predicts the median gross market-cap ROI. Selection requires both the classifier
cutoff and a median predicted net multiple of at least two, then applies the
existing reserve, concentration and sizing policy. The actual proposed size is
checked again because minimum fees matter more on small orders.

Let `P = source_buy_usd / copy_ratio`, `G = sell_market_cap / buy_market_cap`,
`s = assumed slippage per side` and `fee(v) = max(0.95, 0.005*v)`:

```text
estimated proceeds = P * G * (1-s)^2
net multiple = max(0, estimated proceeds - fee(estimated proceeds)) / (P + fee(P))
target_2x = net multiple >= 2
```

The bundled assumptions use ratio 20, standard fees and zero slippage, matching
the previous policy's slippage assumption. A gross doubling generally fails the
net target after fees. A 2× return means doubling the original all-in investment
(100% profit), not a 200% profit / 3× return. These are modeled assumptions, not
verified venue fees or measured execution costs.

The CSV does **not** contain token quantities, contract addresses, copied fills,
transaction fees, actual exit proceeds or supply changes. It cannot establish
realized cash gains. Source SELL dollar amounts may describe partial exits and
are never divided by BUY dollars to create return labels. All earlier buys in a
pair resolve at its first observed sell as a hypothetical full-exit proxy. Ticker
collisions, missed notifications and unobserved exits remain limitations. Model
scores are estimates, and the probability cutoff is not a performance guarantee.

## CSV field audit

| CSV fields | Use |
| --- | --- |
| `trader` | Case-normalized identity and prior activity/outcomes known at entry |
| `coin` | Normalized identity (64 most frequent training tickers), token and trader/token history; unseen tickers retain numeric features |
| `market_cap` | Current log cap, recent cap changes, first-sell return label |
| `source_amount` | Current log buy amount, amount relative to cap and trader history, prior buy/sell flow; reference copied size |
| `action` | BUY opportunities, SELL labels, historical buy/sell counts and flow |
| `post_time`, `captured_time` | Availability, delivery delay, UTC time cycles, trailing-hour activity and token age |
| `title`, `normal_text`, `big_text`, `selected_text` | Already represented by parsed trade fields; inspected text adds no reliable independent price/quantity information; incomplete thesis-like rows are excluded |
| `event_id`, `notification_key`, `notification_tag`, `group_key` | Ordering, opportunity/lifecycle tracking and audit only; unique IDs are not predictors |
| `notification_id`, `package_name`, `channel_id`, `category`, `has_content_intent`, `notification_action_count` | Package/schema validation; these are constant/empty in this export and add no predictive variation |

The 26 numeric features are listed in `learning/notification_features.py` and
the machine-readable evaluation report. They cover current price/size, delivery
delay, UTC hour/weekday cycles, token counts and observed age, previous/hourly
market-cap movement, hourly buy/sell counts and signed source flow, trader size
history, pair activity, and previously resolved trader 2× rates/ROI. Token age is
time since its first *recorded* notification, not launch age. Prior outcomes are
smoothed/clipped for features only; actual evaluation returns are not clipped.

## Causal and training contract

- Information is available at `max(post_time, captured_time)`. An entry uses only
  strictly earlier captures, including for prior outcomes; equal-time events
  cannot inform one another.
- Fingerprint duplicates within three minutes are excluded before history updates.
  Invalid rows are excluded. Open buys remain unknown, never negative labels.
  Metrics describe observed resolved opportunities only; unresolved/censored
  positions can make these differ from the outcome of all live signals.
- A matching sell must be later in both source post time and availability time.
- Buys closed by one sell share a lifecycle. Their total training weight is one.
  Reported grouped rates and bootstrap intervals operate on sell lifecycles.
- Train labels must resolve before October 27 UTC. Validation buys start October
  27 and resolve before November 3. Test buys start November 3. Boundary-crossing
  opportunities are purged. No lifecycle crosses these cohorts.
- The cutoff is `max(0.5, validation probability 90th percentile)` and is frozen
  before testing. The ROI gate is fixed at net 2×. HGB uses 200 iterations, 15
  leaves, leaf minimum 25, learning rate .05, L2 3, seed 388 and no random internal
  early-stopping split. No hyperparameters were selected using test outcomes.
- The generated candidate is the evaluated **train-only** model; validation/test rows
  are not silently used for refitting. This preserves the evaluation claim.
- Training creates a candidate and report. It does not automatically promote,
  install, schedule, or place orders. Reusing these dates on later exports gives
  an updated diagnostic, not a fresh independent test. Reserve future dates for
  any new model/feature/threshold decisions.

## Model availability and evaluation

The public `notification_net_2x.json` is generated entirely from synthetic
constants by `tools/create_net2x_bootstrap.py`. It contains no fitted parameters
from private notifications and cannot authorize a BUY. The dashboard explicitly
shows that no trained model is installed. Data capture continues, and existing
confirmed positions can still be exited.

Train and evaluate against your local raw export. The private `evaluation.json`
contains source counts, resolved/open buys, source hash, chronological cohort
sizes, classifier ranking and Brier metrics, selection coverage and grouped 2×
success rates. A three-input model retrained on the same target provides a
baseline. Any empty selected cohort has no estimated success rate. More input
features do not establish an improvement: inspect the held-out comparison and
retain unsuccessful results. Neither a completed training run nor a high model
score establishes a trading edge.

## Reproduce and update

From `android-fomo-controller`, after installing `learning/requirements.txt`:

```bat
py -3 -m learning.train_2x fomo_buy_sell_notifications.csv --train-before 2026-10-27 --test-start 2026-11-03 --output-dir local_learning_state\net2x
```

`run_learning_loop.bat` wraps this command and accepts optional train-before and
test-start dates. The output contains `notification_net_2x.json.gz` and
`evaluation.json`. Add `--stage` to also create a complete local
`deployment/staging` directory with the forecast, matching policy and checksums.
This only prepares files; it never installs or activates a candidate. Deliver the
complete directory privately through the app's existing internal models staging
path (see `RECURSIVE_LEARNING.md`, Android activation), then verify the displayed
model ID and start in OBSERVE/DRY_RUN. Keep the raw CSV and local state private. The checked-in
bootstrap contains synthetic constants and synthetic parity inputs only. Neither
the trained artifact nor raw CSV is committed by this workflow.

The app replays its existing raw recorder to warm feature history on first
evaluation after restart. This works independently of whether earlier signals
were copied or skipped. A later-arriving event whose availability moves backward
relative to the in-memory history fails closed. Missing features, incompatible
schemas and legacy winner-model staging also fail closed. The historical v2
asset remains only for compatibility and baseline tooling. Its recursive
maintenance/export path must not train or promote a 2× candidate.

Verify with `python -m unittest discover -s learning/tests -v` and
`./gradlew :app:testDebugUnitTest`. Cross-language fixtures exercise the complete
feature vector, unseen categories, model scoring and cost-adjusted entry gates.

The normal Gradle task also verifies Android build integration. A standalone
Kotlin source compile or JVM test run does not verify APK packaging, notification
callbacks on a device, or trade execution. Keep these checks distinct when
reporting validation.
