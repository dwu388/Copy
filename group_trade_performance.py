#!/usr/bin/env python3
"""Group Fomo notification trades by entry market cap and original buy size.

Usage:
    python group_trade_performance.py "Android Notification Log (1).xlsx"

Optional output path:
    python group_trade_performance.py input.xlsx --output grouped_trade_performance.xlsx

Output sheets:
- MC x Buy Size: grouped performance sorted by realized ROI, with Excel filters.
- Date Report: an interactive copy of the grouped report for user-selected buy dates.

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
from openpyxl.worksheet.datavalidation import DataValidation
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
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
WHITE_FONT = Font(color="FFFFFF", bold=True)
TITLE_FONT = Font(color="FFFFFF", bold=True, size=16)
THIN_GRAY_BORDER = Border(bottom=Side(style="thin", color="BFBFBF"))
CURRENCY_FORMAT = '$#,##0.00;[Red]($#,##0.00);-'
PERCENT_FORMAT = '0.0%;[Red](0.0%);-'


def write_date_report(workbook, trade_lots):
    """Create a formula-driven report for any combination of selected buy dates."""
    worksheet = workbook.create_sheet("Date Report")
    data_sheet = workbook.create_sheet("Date Report Data")
    data_sheet.append(
        [
            "Buy Date",
            "Entry MC Group",
            "Original Buy Size Group",
            "Copy Buy",
            "Matched Entry Value",
            "Sale Proceeds",
            "Remaining Entry Value",
        ]
    )

    available_dates = sorted({lot["Buy Time"].date() for lot in trade_lots})
    for lot in trade_lots:
        data_sheet.append(
            [
                lot["Buy Time"].date(),
                lot["Entry MC Group"],
                lot["Buy Size Group"],
                lot["Copy Buy"],
                lot["Matched Entry Value"],
                lot["Sale Proceeds"],
                lot["Remaining Entry Value"],
            ]
        )
    data_sheet.sheet_state = "hidden"

    worksheet.sheet_view.showGridLines = False
    worksheet.merge_cells("A1:H1")
    title = worksheet["A1"]
    title.value = "Interactive Report for Selected Buy Dates"
    title.fill = NAVY_FILL
    title.font = TITLE_FONT
    title.alignment = Alignment(horizontal="left", vertical="center")
    worksheet.row_dimensions[1].height = 26

    worksheet.merge_cells("A2:H2")
    note = worksheet["A2"]
    note.value = (
        "Choose up to 100 individual buy dates in the yellow cells. Duplicate dates are counted "
        "once. The table recalculates when Excel opens or a selection changes."
    )
    note.fill = LIGHT_BLUE_FILL
    note.alignment = Alignment(wrap_text=True, vertical="center")
    worksheet.row_dimensions[2].height = 34

    for row in range(4, 14):
        for column in range(1, 11):
            cell = worksheet.cell(row, column)
            cell.fill = INPUT_FILL
            cell.number_format = "yyyy-mm-dd"
            cell.alignment = Alignment(horizontal="center")

    # The dropdown source lives in a hidden column on the same sheet because Excel
    # data validation cannot directly reference a range on another worksheet.
    for row, value in enumerate(available_dates, start=2):
        cell = worksheet.cell(row, 13, value)
        cell.number_format = "yyyy-mm-dd"
    worksheet.column_dimensions["M"].hidden = True
    if available_dates:
        validation = DataValidation(
            type="list",
            formula1=f"=$M$2:$M${len(available_dates) + 1}",
            allow_blank=True,
        )
        validation.promptTitle = "Select a buy date"
        validation.prompt = "Choose one available date. You may use any number of the yellow cells."
        validation.errorTitle = "Date not in source data"
        validation.error = "Select a date from the dropdown list."
        validation.errorStyle = "stop"
        validation.showErrorMessage = True
        validation.showInputMessage = True
        worksheet.add_data_validation(validation)
        validation.add("A4:J13")

    headers = [
        "Entry MC Group",
        "Original Buy Size Group",
        "Buy Alerts",
        "1:10 Copy Buy Volume",
        "Sale Proceeds",
        "Realized P/L",
        "Realized ROI",
        "Open % of Buy Volume",
    ]
    header_row = 15
    for column, header in enumerate(headers, start=1):
        cell = worksheet.cell(header_row, column, header)
        cell.fill = BLUE_FILL
        cell.font = WHITE_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_GRAY_BORDER

    data_last_row = len(trade_lots) + 1
    output_row = header_row + 1
    for _, _, mc_label in MC_BINS:
        for _, _, size_label in BUY_SIZE_BINS:
            worksheet.cell(output_row, 1, mc_label)
            worksheet.cell(output_row, 2, size_label)

            if trade_lots:
                selected = (
                    f"--('Date Report Data'!$B$2:$B${data_last_row}=$A{output_row}),"
                    f"--('Date Report Data'!$C$2:$C${data_last_row}=$B{output_row}),"
                    f"--(COUNTIF($A$4:$J$13,'Date Report Data'!$A$2:$A${data_last_row})>0)"
                )
                count_formula = f"SUMPRODUCT({selected})"

                def selected_sum(column):
                    return (
                        f"SUMPRODUCT({selected},"
                        f"'Date Report Data'!${column}$2:${column}${data_last_row})"
                    )

                copy_buy = selected_sum("D")
                matched_entry = selected_sum("E")
                sale_proceeds = selected_sum("F")
                remaining_entry = selected_sum("G")
                worksheet.cell(output_row, 3, f'=IF({count_formula}=0,"",{count_formula})')
                worksheet.cell(output_row, 4, f'=IF(C{output_row}="","",{copy_buy})')
                worksheet.cell(output_row, 5, f'=IF(C{output_row}="","",{sale_proceeds})')
                worksheet.cell(
                    output_row,
                    6,
                    f'=IF(C{output_row}="","",E{output_row}-{matched_entry})',
                )
                worksheet.cell(
                    output_row,
                    7,
                    f'=IF(OR(C{output_row}="",{matched_entry}=0),"",F{output_row}/{matched_entry})',
                )
                worksheet.cell(
                    output_row,
                    8,
                    f'=IF(OR(C{output_row}="",D{output_row}=0),"",{remaining_entry}/D{output_row})',
                )

            for column in range(1, 9):
                cell = worksheet.cell(output_row, column)
                cell.border = THIN_GRAY_BORDER
            for column in (4, 5, 6):
                worksheet.cell(output_row, column).number_format = CURRENCY_FORMAT
            for column in (7, 8):
                worksheet.cell(output_row, column).number_format = PERCENT_FORMAT
            output_row += 1

    widths = [18, 24, 12, 20, 16, 16, 14, 20]
    for column_number, width in enumerate(widths, start=1):
        worksheet.column_dimensions[get_column_letter(column_number)].width = width
    worksheet.freeze_panes = "C16"


def write_output(rows, trade_lots, output_path: Path):
    workbook = Workbook()
    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
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
    write_date_report(workbook, trade_lots)
    workbook.save(output_path)


def main():
    args = parse_args()
    trades = read_trades(args.input, args.copy_ratio)
    rows, trade_lots = analyze(trades)
    write_output(rows, trade_lots, args.output)

    print(f"Parsed {len(trades)} trades")
    print(f"Created {len(rows)} populated MC x buy-size groups")
    print(f"Added {len(trade_lots)} buy lots to the interactive date report")
    print(f"Saved: {args.output.resolve()}")


if __name__ == "__main__":
    main()
