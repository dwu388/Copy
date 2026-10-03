#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning.core import compare_grouped, load_learning_csv, read_payload, score_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare champions with a lifecycle-grouped paired bootstrap")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("champion", type=Path)
    parser.add_argument("challenger", type=Path)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    frame = load_learning_csv(args.dataset)
    result = compare_grouped(
        frame, score_payload(read_payload(args.champion), frame),
        score_payload(read_payload(args.challenger), frame), args.bootstrap_samples,
    )
    print(json.dumps(result.__dict__, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
