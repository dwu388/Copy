"""Convert a raw Android notification CSV to a local learning export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning.policy_labels import build_policy_labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--as-of", type=int, help="Observation cutoff in epoch milliseconds")
    args = parser.parse_args()
    frame, summary = build_policy_labels(args.input, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
