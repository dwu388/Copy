"""Prepare legacy A:D notification workbooks for an offline forecast experiment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning.historical_workbook import import_notification_log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sheet", default="Notifications")
    args = parser.parse_args()
    frame, summary = import_notification_log(args.input, args.sheet)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
