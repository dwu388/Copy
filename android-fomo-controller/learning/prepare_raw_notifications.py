"""Convert a raw Android notification CSV to a local learning export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning.raw_notification_csv import import_raw_notifications


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    frame, summary = import_raw_notifications(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
