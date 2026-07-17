"""Railway Cron entrypoint: collect every location once, then exit."""

import json
import sys

from app import LOCATIONS, collect_all_locations, init_db


def main() -> int:
    init_db()
    results = collect_all_locations()
    print(json.dumps(results, ensure_ascii=False))
    failures = [result for result in results if "error" in result]
    print(f"Collected {len(results) - len(failures)}/{len(results)} daily schedules across {len(LOCATIONS)} locations")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
