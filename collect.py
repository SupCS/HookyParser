"""Railway Cron entrypoint: collect every location once, then exit."""

import json
from app import BRANDS, FUTURE_DAYS, collect_all_locations, init_db


def main() -> int:
    init_db()
    print(f"Collecting release catalogs without a date cutoff; showtimes for today plus {FUTURE_DAYS} future days", flush=True)
    results = collect_all_locations()
    print(json.dumps(results, ensure_ascii=False))
    failures = [result for result in results if "error" in result]
    schedules = [result for result in results if "date" in result]
    saved = sum("run_id" in result for result in schedules)
    location_count = sum(len(brand["locations"]) for brand in BRANDS.values())
    print(f"Collected {saved}/{len(schedules)} daily schedules across {location_count} locations in {len(BRANDS)} brands; {len(failures)} errors")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
