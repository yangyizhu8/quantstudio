#!/usr/bin/env python3
"""Import external event CSV data into QuantStudio's generic event table."""
from __future__ import annotations

import argparse
import json

from quantstudio._paths import db_path
from quantstudio.backtest.events import import_strategy_event_csv

PRESETS = {
    "first_cover": {
        "event_date": "\u53d1\u5e03\u65e5\u671f",
        "code": "\u80a1\u7968\u4ee3\u7801",
        "signal": "\u8bc4\u7ea7",
        "name": "\u80a1\u7968\u540d\u79f0",
        "category": "\u884c\u4e1a",
        "source": "\u5238\u5546",
    },
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import generic strategy events")
    parser.add_argument("csv")
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument("--mapping-json", help="JSON object mapping canonical fields to CSV columns")
    parser.add_argument("--db", default=str(db_path()))
    parser.add_argument("--append", action="store_true", help="Do not replace existing rows for event_type")
    args = parser.parse_args(argv)
    if args.mapping_json:
        mapping = json.loads(args.mapping_json)
    elif args.preset:
        mapping = PRESETS[args.preset]
    else:
        parser.error("one of --preset or --mapping-json is required")
    result = import_strategy_event_csv(
        args.db, args.csv, args.event_type, mapping, replace_event_type=not args.append)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
