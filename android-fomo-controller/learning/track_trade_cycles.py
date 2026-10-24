#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from learning.trade_input import build_trade_cycles, load_trade_excel


def default_output(input_path: Path, trader: str | None, coin: str | None) -> Path:
    suffix = "__cycles"
    if trader:
        suffix += f"__{trader}"
    if coin:
        suffix += f"__{coin}"
    return input_path.with_name(input_path.stem + suffix + ".csv")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve buys at first later sell from Excel columns K:P only"
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sheet")
    parser.add_argument("--trader")
    parser.add_argument("--coin")
    parser.add_argument("--pairing", choices=["fifo", "lifo"], default="fifo")
    parser.add_argument(
        "--order", choices=["top-to-bottom", "bottom-to-top"], default="top-to-bottom",
        help="Chronological direction of worksheet rows",
    )
    args = parser.parse_args()

    trades = load_trade_excel(args.input, sheet_name=args.sheet, order=args.order)
    cycles = build_trade_cycles(
        trades, trader=args.trader, coin=args.coin, pairing=args.pairing
    )
    output = args.output or default_output(args.input, args.trader, args.coin)
    output.parent.mkdir(parents=True, exist_ok=True)
    cycles.to_csv(output, index=False)

    closed = int(cycles["status"].eq("closed").sum()) if not cycles.empty else 0
    open_count = int(cycles["status"].eq("open").sum()) if not cycles.empty else 0
    wins = int(cycles["is_win"].eq(1).sum()) if not cycles.empty else 0
    losses_or_breakeven = closed - wins
    print(
        f"source_rows={len(trades)} cycles={len(cycles)} closed={closed} "
        f"wins={wins} losses_or_breakeven={losses_or_breakeven} open={open_count} output={output}"
    )


if __name__ == "__main__":
    main()
