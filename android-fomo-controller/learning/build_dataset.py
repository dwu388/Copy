#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from learning.core import load_learning_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and normalize an Android learning export")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    frame = load_learning_csv(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"resolved_rows={len(frame)} lifecycle_groups={frame.lifecycle_group_id.nunique()}")


if __name__ == "__main__":
    main()
