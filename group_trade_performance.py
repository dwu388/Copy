#!/usr/bin/env python3
"""Group Fomo notification trades by entry market cap and original buy size.

Usage:
    python group_trade_performance.py "Android Notification Log (1).xlsx"

Optional output path:
    python group_trade_performance.py input.xlsx --output grouped_trade_performance.xlsx

Assumptions:
- Copy ratio defaults to 1:10, so a trader's $1,000 trade becomes a $100 copied trade.
- Buy lots are tracked separately by trader + coin.
- Later sells are matched FIFO against previously observed buys only.
- Trade dollars / market cap is used as a quantity proxy, which preserves the
  percentage change between entry and exit when token quantity is not present.
"""

from __future__ import annotations

import argparse
import math
import re
from collections import defaultdict, deque
from copy import copy
from pathlib import Path

from openpyxl import Workbook, load_workbook


MC_BINS = [
    (0, 50_000, "<$50k"),
    (50_000, 75_000, "$50k-$75k"),
    (75_000, 150_000, "$75k-$150k"),
    (150_000, 250_000, "$150k-$250k"),
    (250_000, 500_000, "$250k-$500k"),
    (500_000, 1_000_000, "$500k-$1m"),
    (1_000_000, math.inf, "$1m+"),
]

BUY_SIZE_BINS = [
    (0, 500, "<$500"),
    (500, 1_000, "$500-$1k"),
    (1_000, 2_500, "$1k-$2.5k"),
    (2_500, 5_000, "$2.5k-$5k"),
    (5_000, 10_000, "$5k-$10k"),
    (10_000, math.inf, "$10k+"),
]

TITLE_RE = re.compile(r"^(.*?) at \$([\d,.]+)([kKmM]?) MC")
TEXT_RE = re.compile(r"@([^\s]+)\s+(?:bought|sold)\s+\$([\d,]+(?:\.\d+)?)")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input Android notification .xlsx file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("grouped_trade_performance.xlsx"),
    )
    parser.add_argument(
        "--copy-ratio",
        type=float,
        default=0.10,
        help="Fraction of original trade to copy. Default: 0.10",
    )
    return parser.parse_args()


def parse_market_cap(title: str):
    match = TITLE_RE.search(title or "")
    if not match:
        return None, None

    coin = match.group(1).strip()
    value = float(match.group(2).replace(",", ""))
    suffix = match.group(3).lower()

    if suffix == "k":
        value *= 1_000
    elif suffix == "m":
        value *= 1_000_000

    return coin, value


def parse_trade_text(text: str):
    match = TEXT_RE.search(text or "")
    if not match:
        return None, None

    trader = match.group(1)
    amount = float(match.group(2).replace(",", ""))
    return trader, amount


def find_bin(value, bins):
    for low, high, label in bins:
        if low <= value < high:
            return label
    raise ValueError(f"Value {value} did not match a bin")


def read_trades(path: Path, copy_ratio: float):
    workbook = load_workbook(path, data_only=True, read_only=True)
    worksheet = (
        workbook["Notifications"]
        if "Notifications" in workbook.sheetnames
        else workbook[workbook.sheetnames[0]]
    )

    trades = []
    for row in worksheet.iter_rows(min_row=2, max_col=4, values_only=True):
        if len(row) < 4:
            continue

        time, trade_type, title, text = row
        if not all([time, trade_type, title, text]):
            continue

        coin, market_cap = parse_market_cap(str(title))
        trader, original_amount = parse_trade_text(str(text))
        if coin is None or trader is None:
            continue

        trades.append(
            {
                "time": time,
                "type": str(trade_type).lower(),
                "trader": trader,
                "coin": coin,
                "mc": market_cap,
                "original_amount": original_amount,
                "copy_amount": original_amount * copy_ratio,
            }
        )

    trades.sort(key=lambda trade: trade["time"])
    return trades


def empty_stats():
    return {
        "buy_alerts": 0,
        "copy_buy_volume": 0.0,
        "sold_entry_value": 0.0,
        "sale_proceeds": 0.0,
        "remaining_entry_value": 0.0,
    }


def analyze(trades):
    stats = defaultdict(empty_stats)
    lots = defaultdict(deque)

    for trade in trades:
        key = (trade["trader"], trade["coin"])

        if trade["type"] == "bought":
            mc_group = find_bin(trade["mc"], MC_BINS)
            size_group = find_bin(trade["original_amount"], BUY_SIZE_BINS)
            group = (mc_group, size_group)

            stats[group]["buy_alerts"] += 1
            stats[group]["copy_buy_volume"] += trade["copy_amount"]

            lots[key].append(
                {
                    "qty": trade["copy_amount"] / trade["mc"],
                    "entry_mc": trade["mc"],
                    "group": group,
                }
            )

        elif trade["type"] == "sold":
            sell_qty = trade["copy_amount"] / trade["mc"]

            while sell_qty > 1e-15 and lots[key]:
                lot = lots[key][0]
                matched_qty = min(sell_qty, lot["qty"])
                group_stats = stats[lot["group"]]

                group_stats["sold_entry_value"] += matched_qty * lot["entry_mc"]
                group_stats["sale_proceeds"] += matched_qty * trade["mc"]

                lot["qty"] -= matched_qty
                sell_qty -= matched_qty

                if lot["qty"] <= 1e-15:
                    lots[key].popleft()

    for queue in lots.values():
        for lot in queue:
            stats[lot["group"]]["remaining_entry_value"] += (
                lot["qty"] * lot["entry_mc"]
            )

    rows = []
    for _, _, mc_label in MC_BINS:
        for _, _, size_label in BUY_SIZE_BINS:
            group_stats = stats[(mc_label, size_label)]
            if group_stats["buy_alerts"] == 0:
                continue

            sold_entry_value = group_stats["sold_entry_value"]
            realized_pnl = group_stats["sale_proceeds"] - sold_entry_value
            realized_roi = (
                realized_pnl / sold_entry_value if sold_entry_value else None
            )
            open_pct = (
                group_stats["remaining_entry_value"]
                / group_stats["copy_buy_volume"]
                if group_stats["copy_buy_volume"]
                else None
            )

            rows.append(
                {
                    "Entry MC Group": mc_label,
                    "Original Buy Size Group": size_label,
                    "Buy Alerts": group_stats["buy_alerts"],
                    "1:10 Copy Buy Volume": group_stats["copy_buy_volume"],
                    "Sale Proceeds": group_stats["sale_proceeds"],
                    "Realized P/L": realized_pnl,
                    "Realized ROI": realized_roi,
                    "Open % of Buy Volume": open_pct,
                }
            )

    return rows


def write_output(rows, output_path: Path):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "MC x Buy Size"

    headers = list(rows[0].keys()) if rows else [
        "Entry MC Group",
        "Original Buy Size Group",
        "Buy Alerts",
        "1:10 Copy Buy Volume",
        "Sale Proceeds",
        "Realized P/L",
        "Realized ROI",
        "Open % of Buy Volume",
    ]
    worksheet.append(headers)

    for row in rows:
        worksheet.append([row[header] for header in headers])

    for cell in worksheet[1]:
        if cell.value is not None:
            header_font = copy(cell.font)
            header_font.bold = True
            cell.font = header_font

    currency_cols = {4, 5, 6}
    percent_cols = {7, 8}
    for row in worksheet.iter_rows(min_row=2):
        for column_number, cell in enumerate(row, start=1):
            if column_number in currency_cols:
                cell.number_format = '$#,##0.00;[Red]($#,##0.00);-'
            elif column_number in percent_cols:
                cell.number_format = "0.0%"

    widths = [18, 24, 12, 20, 16, 16, 14, 20]
    for column_number, width in enumerate(widths, start=1):
        worksheet.column_dimensions[chr(64 + column_number)].width = width

    worksheet.freeze_panes = "A2"
    workbook.save(output_path)


def main():
    args = parse_args()
    trades = read_trades(args.input, args.copy_ratio)
    rows = analyze(trades)
    write_output(rows, args.output)

    print(f"Parsed {len(trades)} trades")
    print(f"Created {len(rows)} populated MC x buy-size groups")
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
