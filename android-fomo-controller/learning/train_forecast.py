#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from learning.core import build_payload, load_learning_csv, write_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a stable HGB forecast plus capped recent adapter")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--parent-model-id", required=True)
    parser.add_argument("--adapter-days", type=int, default=10)
    parser.add_argument("--adapter-weight", type=float, default=0.30)
    args = parser.parse_args()
    payload = build_payload(
        load_learning_csv(args.dataset), args.model_id, args.parent_model_id,
        args.adapter_days, args.adapter_weight,
    )
    write_payload(payload, args.output)
    print(f"wrote {args.output} model_id={args.model_id}")


if __name__ == "__main__":
    main()
