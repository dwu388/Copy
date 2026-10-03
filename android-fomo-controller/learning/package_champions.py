#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from learning.core import read_payload
from learning.run_maintenance import deploy


def main() -> None:
    parser = argparse.ArgumentParser(description="Package the independently promoted champions for Android staging")
    parser.add_argument("--state-dir", type=Path, default=Path("local_learning_state"))
    args = parser.parse_args()
    forecast_path = args.state_dir / "models" / "forecast" / "champion.json.gz"
    policy_path = args.state_dir / "models" / "policy" / "champion.json"
    if not forecast_path.is_file() or not policy_path.is_file():
        raise FileNotFoundError("Both a forecast champion and a policy champion are required")
    forecast = read_payload(forecast_path)
    policy = json.loads(policy_path.read_text())
    deploy(args.state_dir, forecast_path, policy_path, forecast, policy)
    print(args.state_dir / "deployment" / "staging")


if __name__ == "__main__":
    main()
