"""Railway Cron entrypoint: collect every location once, then exit."""

import json
from app import BRANDS, collect_all_locations, init_db


def main() -> int:
    init_db()
    results = collect_all_locations()
    print(json.dumps(results, ensure_ascii=False))
    failures = [result for result in results if "error" in result]
    location_count = sum(len(brand["locations"]) for brand in BRANDS.values())
    print(f"Collected {len(results) - len(failures)}/{len(results)} daily schedules across {location_count} locations in {len(BRANDS)} brands")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
