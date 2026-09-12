#!/usr/bin/env python3
"""Group Fomo notification trades by entry market cap and original buy size.

Usage:
    python group_trade_performance.py "Android Notification Log (1).xlsx"

Optional output path:
    python group_trade_performance.py input.xlsx --output grouped_trade_performance.xlsx

Output sheets:
- MC x Buy Size: grouped performance sorted by realized ROI, with Excel filters.
- Top & Bottom Trades: top 50 realized ROIs and largest 50 realized dollar losses.

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
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


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
    all_lots = []
    next_lot_id = 1

    for trade in trades:
        key = (trade["trader"], trade["coin"])

        if trade["type"] == "bought":
            mc_group = find_bin(trade["mc"], MC_BINS)
            size_group = find_bin(trade["original_amount"], BUY_SIZE_BINS)
            group = (mc_group, size_group)

            stats[group]["buy_alerts"] += 1
            stats[group]["copy_buy_volume"] += trade["copy_amount"]

            lot = {
                "lot_id": next_lot_id,
                "trader": trade["trader"],
                "coin": trade["coin"],
                "buy_time": trade["time"],
                "entry_mc": trade["mc"],
                "original_buy": trade["original_amount"],
                "copy_buy": trade["copy_amount"],
                "copy_ratio": (
                    trade["copy_amount"] / trade["original_amount"]
                    if trade["original_amount"]
                    else 0.0
                ),
                "initial_qty": trade["copy_amount"] / trade["mc"],
                "qty": trade["copy_amount"] / trade["mc"],
                "matched_qty": 0.0,
                "first_sell_time": None,
                "last_sell_time": None,
                "sell_alerts": 0,
                "sold_entry_value": 0.0,
                "sale_proceeds": 0.0,
                "group": group,
            }
            next_lot_id += 1
            lots[key].append(lot)
            all_lots.append(lot)

        elif trade["type"] == "sold":
            sell_qty = trade["copy_amount"] / trade["mc"]

            while sell_qty > 1e-15 and lots[key]:
                lot = lots[key][0]
                matched_qty = min(sell_qty, lot["qty"])
                group_stats = stats[lot["group"]]

                matched_entry_value = matched_qty * lot["entry_mc"]
                matched_sale_proceeds = matched_qty * trade["mc"]

                group_stats["sold_entry_value"] += matched_entry_value
                group_stats["sale_proceeds"] += matched_sale_proceeds

                lot["matched_qty"] += matched_qty
                lot["sold_entry_value"] += matched_entry_value
                lot["sale_proceeds"] += matched_sale_proceeds
                lot["sell_alerts"] += 1
                if lot["first_sell_time"] is None:
                    lot["first_sell_time"] = trade["time"]
                lot["last_sell_time"] = trade["time"]

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

    trade_lots = []
    for lot in all_lots:
        realized_pnl = lot["sale_proceeds"] - lot["sold_entry_value"]
        realized_roi = (
            realized_pnl / lot["sold_entry_value"]
            if lot["sold_entry_value"]
            else None
        )
        remaining_entry_value = lot["qty"] * lot["entry_mc"]
        open_pct = (
            remaining_entry_value / lot["copy_buy"]
            if lot["copy_buy"]
            else None
        )
        average_exit_mc = (
            lot["sale_proceeds"] / lot["matched_qty"]
            if lot["matched_qty"]
            else None
        )
        hours_to_first_sell = (
            (lot["first_sell_time"] - lot["buy_time"]).total_seconds() / 3600
            if lot["first_sell_time"] is not None
            else None
        )
        hours_to_last_sell = (
            (lot["last_sell_time"] - lot["buy_time"]).total_seconds() / 3600
            if lot["last_sell_time"] is not None
            else None
        )

        if lot["sold_entry_value"] <= 1e-15:
            outcome = "Open only"
        elif realized_pnl > 1e-9:
            outcome = "Profit"
        elif realized_pnl < -1e-9:
            outcome = "Loss"
        else:
            outcome = "Breakeven"

        if lot["sold_entry_value"] <= 1e-15:
            position_status = "Open"
        elif lot["qty"] <= 1e-15:
            position_status = "Closed"
        else:
            position_status = "Partially sold"

        trade_lots.append(
            {
                "Lot": lot["lot_id"],
                "Trader": lot["trader"],
                "Coin": lot["coin"],
                "Buy Time": lot["buy_time"],
                "Entry MC": lot["entry_mc"],
                "Entry MC Group": find_bin(lot["entry_mc"], MC_BINS),
                "Buy Size Group": find_bin(lot["original_buy"], BUY_SIZE_BINS),
                "Original Buy": lot["original_buy"],
                "Copy Buy": lot["copy_buy"],
                "Position Status": position_status,
                "Realized Outcome": outcome,
                "Sell Alerts": lot["sell_alerts"],
                "First Sell Time": lot["first_sell_time"],
                "Last Sell Time": lot["last_sell_time"],
                "Hours to First Sell": hours_to_first_sell,
                "Hours to Last Sell": hours_to_last_sell,
                "Average Exit MC": average_exit_mc,
                "Matched Entry Value": lot["sold_entry_value"],
                "Sale Proceeds": lot["sale_proceeds"],
                "Realized P/L": realized_pnl if lot["sold_entry_value"] else None,
                "Realized ROI": realized_roi,
                "Remaining Entry Value": remaining_entry_value,
                "Open %": open_pct,
            }
        )

    rows.sort(
        key=lambda row: (
            row["Realized ROI"] is None,
            -(row["Realized ROI"] if row["Realized ROI"] is not None else 0),
        )
    )
    trade_lots.sort(key=lambda lot: lot["Buy Time"])
    return rows, trade_lots


NAVY_FILL = PatternFill("solid", fgColor="17365D")
BLUE_FILL = PatternFill("solid", fgColor="5B9BD5")
LIGHT_BLUE_FILL = PatternFill("solid", fgColor="D9EAF7")
LIGHT_GREEN_FILL = PatternFill("solid", fgColor="E2F0D9")
LIGHT_RED_FILL = PatternFill("solid", fgColor="FCE4D6")
WHITE_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(color="FFFFFF", bold=True, size=16)
THIN_GRAY_BORDER = Border(bottom=Side(style="thin", color="BFBFBF"))
CURRENCY_FORMAT = '$#,##0.00;[Red]($#,##0.00);-'
MC_FORMAT = '$#,##0;[Red]($#,##0);-'
PERCENT_FORMAT = '0.0%;[Red](0.0%);-'
NUMBER_FORMAT = '#,##0.00;[Red](#,##0.00);-'


def format_cell_for_header(cell, header):
    if header in {
        "Entry MC",
        "Average Exit MC",
        "Average Entry MC",
        "Median Entry MC",
    }:
        cell.number_format = MC_FORMAT
    elif header in {
        "Original Buy",
        "Average Original Buy",
        "Median Original Buy",
        "Copy Buy",
        "Copy Buy Volume",
        "Matched Entry Value",
        "Sale Proceeds",
        "Realized P/L",
        "Remaining Entry Value",
    }:
        cell.number_format = CURRENCY_FORMAT
    elif header in {
        "Win Rate",
        "Realized ROI",
        "Average Lot ROI",
        "Median Lot ROI",
        "Open %",
    }:
        cell.number_format = PERCENT_FORMAT
    elif "Hours" in header:
        cell.number_format = NUMBER_FORMAT
    elif header.endswith("Time"):
        cell.number_format = "yyyy-mm-dd hh:mm:ss"
    elif header in {
        "Lot",
        "Buy Lots",
        "Realized Lots",
        "Profitable Lots",
        "Losing Lots",
        "Sell Alerts",
    }:
        cell.number_format = "#,##0"


def write_analysis_table(worksheet, start_row, title, headers, data_rows):
    end_column = len(headers)
    worksheet.merge_cells(
        start_row=start_row,
        start_column=1,
        end_row=start_row,
        end_column=end_column,
    )
    title_cell = worksheet.cell(start_row, 1, title)
    title_cell.fill = NAVY_FILL
    title_cell.font = WHITE_FONT
    title_cell.alignment = Alignment(horizontal="left")

    header_row = start_row + 1
    for column_number, header in enumerate(headers, start=1):
        cell = worksheet.cell(header_row, column_number, header)
        cell.fill = BLUE_FILL
        cell.font = WHITE_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_GRAY_BORDER

    first_data_row = header_row + 1
    for row_number, row_values in enumerate(data_rows, start=first_data_row):
        for column_number, header in enumerate(headers, start=1):
            cell = worksheet.cell(row_number, column_number, row_values.get(header))
            cell.alignment = Alignment(
                horizontal="left" if isinstance(cell.value, str) else "right",
                vertical="center",
            )
            cell.border = THIN_GRAY_BORDER
            format_cell_for_header(cell, header)

    last_data_row = first_data_row + len(data_rows) - 1
    return header_row, first_data_row, last_data_row


TRADE_HEADERS = [
    "Lot",
    "Trader",
    "Coin",
    "Buy Time",
    "Entry MC",
    "Entry MC Group",
    "Buy Size Group",
    "Original Buy",
    "Copy Buy",
    "Position Status",
    "Realized Outcome",
    "Sell Alerts",
    "First Sell Time",
    "Last Sell Time",
    "Hours to First Sell",
    "Hours to Last Sell",
    "Average Exit MC",
    "Matched Entry Value",
    "Sale Proceeds",
    "Realized P/L",
    "Realized ROI",
    "Remaining Entry Value",
    "Open %",
]


def add_trade_conditional_formatting(
    worksheet, headers, first_data_row, last_data_row
):
    if first_data_row > last_data_row:
        return

    for header in ["Realized P/L", "Realized ROI"]:
        column_number = headers.index(header) + 1
        cell_range = (
            f"{get_column_letter(column_number)}{first_data_row}:"
            f"{get_column_letter(column_number)}{last_data_row}"
        )
        worksheet.conditional_formatting.add(
            cell_range,
            CellIsRule(
                operator="greaterThan",
                formula=["0"],
                fill=LIGHT_GREEN_FILL,
            ),
        )
        worksheet.conditional_formatting.add(
            cell_range,
            CellIsRule(
                operator="lessThan",
                formula=["0"],
                fill=LIGHT_RED_FILL,
            ),
        )


def write_ranked_trade_analysis(workbook, trade_lots):
    worksheet = workbook.create_sheet("Top & Bottom Trades")
    worksheet.sheet_view.showGridLines = False
    worksheet.sheet_view.zoomScale = 80

    max_section_columns = len(TRADE_HEADERS)
    worksheet.merge_cells(
        start_row=1,
        start_column=1,
        end_row=1,
        end_column=max_section_columns,
    )
    title = worksheet.cell(1, 1, "Top ROI Trades and Largest Realized Losses")
    title.fill = NAVY_FILL
    title.font = TITLE_FONT
    title.alignment = Alignment(horizontal="left", vertical="center")
    worksheet.row_dimensions[1].height = 26

    worksheet.merge_cells(
        start_row=2,
        start_column=1,
        end_row=2,
        end_column=max_section_columns,
    )
    note = worksheet.cell(
        2,
        1,
        "Rankings include all entry market caps. Open-only lots are excluded. Realized ROI and "
        "P/L use only the portion matched to later sells through FIFO matching by trader and coin.",
    )
    note.fill = LIGHT_BLUE_FILL
    note.alignment = Alignment(wrap_text=True, vertical="center")
    worksheet.row_dimensions[2].height = 34

    realized_lots = [lot for lot in trade_lots if lot["Realized ROI"] is not None]
    top_roi_trades = sorted(
        realized_lots,
        key=lambda lot: lot["Realized ROI"],
        reverse=True,
    )[:50]
    largest_losses = sorted(
        [lot for lot in realized_lots if lot["Realized P/L"] < -1e-9],
        key=lambda lot: lot["Realized P/L"],
    )[:50]

    _, top_first, top_last = write_analysis_table(
        worksheet,
        4,
        "Top 50 Trades by Realized ROI",
        TRADE_HEADERS,
        top_roi_trades,
    )
    add_trade_conditional_formatting(
        worksheet, TRADE_HEADERS, top_first, top_last
    )

    _, loss_first, loss_last = write_analysis_table(
        worksheet,
        top_last + 3,
        "Largest 50 Realized Losses by Dollar P/L",
        TRADE_HEADERS,
        largest_losses,
    )
    add_trade_conditional_formatting(
        worksheet, TRADE_HEADERS, loss_first, loss_last
    )

    widths = {
        1: 28,
        2: 20,
        3: 15,
        4: 21,
        5: 15,
        6: 15,
        7: 18,
        8: 16,
        9: 17,
        10: 18,
        11: 18,
        12: 13,
        13: 21,
        14: 21,
        15: 19,
        16: 18,
        17: 17,
        18: 20,
        19: 17,
        20: 16,
        21: 15,
        22: 21,
        23: 14,
    }
    for column_number, width in widths.items():
        worksheet.column_dimensions[get_column_letter(column_number)].width = width

    worksheet.freeze_panes = "D6"


def write_output(rows, trade_lots, output_path: Path):
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

    if worksheet.max_row > 1:
        worksheet.auto_filter.ref = f"A1:H{worksheet.max_row}"
    worksheet.freeze_panes = "A2"
    write_ranked_trade_analysis(workbook, trade_lots)
    workbook.save(output_path)


def main():
    args = parse_args()
    trades = read_trades(args.input, args.copy_ratio)
    rows, trade_lots = analyze(trades)
    write_output(rows, trade_lots, args.output)

    print(f"Parsed {len(trades)} trades")
    print(f"Created {len(rows)} populated MC x buy-size groups")
    print(f"Analyzed {len(trade_lots)} buy lots across all entry market caps")
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
