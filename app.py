from __future__ import annotations

import os
import re
import sqlite3
import time
import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from flask import Flask, jsonify, render_template, request

from imdb_release_helper import lookup_movie, normalize_title

psycopg: Any
dict_row: Any
try:
    import psycopg as psycopg_module
    from psycopg.rows import dict_row as psycopg_dict_row

    psycopg = psycopg_module
    dict_row = psycopg_dict_row
except ImportError:  # Local SQLite development does not require PostgreSQL.
    psycopg = None
    dict_row = None


ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("HOOKY_DB", ROOT / "hooky_history.sqlite3"))
DATABASE_URL = os.environ.get("DATABASE_URL")
BASE_URL = "https://hookyentertainment.com"
LOCATIONS = {
    "addison": "Addison",
    "baytown": "Baytown",
    "cary": "Cary",
    "delray": "Delray Beach",
    "fredericksburg": "Fredericksburg",
    "homestead": "Homestead",
    "hutto": "Hutto",
    "nashville": "Nashville",
    "southlake": "Southlake",
    "waxahachie": "Waxahachie",
}
LOCATION_TIMEZONES = {
    "addison": "America/Chicago", "baytown": "America/Chicago",
    "cary": "America/New_York", "delray": "America/New_York",
    "fredericksburg": "America/New_York", "homestead": "America/New_York",
    "hutto": "America/Chicago", "nashville": "America/Chicago",
    "southlake": "America/Chicago", "waxahachie": "America/Chicago",
}
SITE_IDS = {
    "addison": 217, "baytown": 216, "cary": 221, "delray": 222,
    "fredericksburg": 220, "homestead": 223, "hutto": 214,
    "nashville": 224, "southlake": 206, "waxahachie": 218,
}
CIRCUIT_ID = "119"
FUTURE_DAYS = max(0, min(int(os.environ.get("HOOKY_FUTURE_DAYS", "30")), 31))
MANUAL_FUTURE_DAYS = 13
POPULARITY_CACHE_HOURS = max(1, int(os.environ.get("IMDB_POPULARITY_CACHE_HOURS", "24")))
POPULARITY_ERROR_CACHE_MINUTES = max(
    1, int(os.environ.get("IMDB_POPULARITY_ERROR_CACHE_MINUTES", "15"))
)
SHOWINGS_QUERY = """
query ($date: String, $siteIds: [ID]) {
  showingsForDate(date: $date, siteIds: $siteIds) {
    data { id time movie { name urlSlug } }
    count
  }
}
"""

app = Flask(__name__)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
_missing_omdb_key_logged = False


def db():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("psycopg is required when DATABASE_URL is set")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def sql(query: str) -> str:
    return query.replace("?", "%s") if DATABASE_URL else query


def init_db() -> None:
    schema = """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id {primary_key},
                location TEXT NOT NULL,
                show_date TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                source_url TEXT NOT NULL,
                movie_count INTEGER NOT NULL,
                showing_count INTEGER NOT NULL,
                UNIQUE(location, show_date, captured_at)
            );
            CREATE TABLE IF NOT EXISTS showings (
                id {primary_key},
                run_id BIGINT NOT NULL REFERENCES scrape_runs(id) ON DELETE CASCADE,
                movie_slug TEXT NOT NULL,
                movie_title TEXT NOT NULL,
                show_time TEXT NOT NULL,
                checkout_url TEXT NOT NULL,
                UNIQUE(run_id, checkout_url)
            );
            CREATE INDEX IF NOT EXISTS idx_runs_lookup
            ON scrape_runs(location, show_date, captured_at DESC);
            CREATE TABLE IF NOT EXISTS movie_popularity (
                normalized_title TEXT PRIMARY KEY,
                movie_title TEXT NOT NULL,
                imdb_id TEXT NOT NULL,
                imdb_popularity INTEGER,
                fetched_at TEXT NOT NULL
            );
            """.format(primary_key="BIGSERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY")
    last_error = None
    for attempt in range(5):
        try:
            with db() as connection:
                if DATABASE_URL:
                    for statement in schema.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                else:
                    connection.executescript(schema)
                connection.execute(
                    """DELETE FROM scrape_runs
                       WHERE id NOT IN (
                           SELECT MAX(id) FROM scrape_runs GROUP BY location, show_date
                       )"""
                )
                connection.execute(
                    """CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_location_day
                       ON scrape_runs(location, show_date)"""
                )
            return
        except Exception as error:
            last_error = error
            if attempt == 4:
                raise
            time.sleep(2)
    if last_error is not None:
        raise last_error
    raise RuntimeError("Database initialization failed")


def parse_movies(html: str, page_url: str) -> list[dict[str, Any]]:
    """Parse the SSR fallback: movie link followed by its checkout links."""
    soup = BeautifulSoup(html, "html.parser")
    movies: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    movie_pattern = re.compile(r"/movie/([^/?#]+)")
    showing_pattern = re.compile(r"/checkout/showing/([^/?#]+)/([^/?#]+)")

    for anchor in soup.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href_value = anchor.get("href")
        if not isinstance(href_value, str):
            continue
        href = urljoin(page_url, href_value)
        text = " ".join(anchor.get_text(" ", strip=True).split())
        movie_match = movie_pattern.search(href)
        showing_match = showing_pattern.search(href)
        if movie_match and not showing_match:
            current = {
                "slug": movie_match.group(1),
                "title": text or movie_match.group(1).replace("-", " ").title(),
                "url": href,
                "showings": [],
            }
            movies.append(current)
        elif showing_match and current and showing_match.group(1) == current["slug"]:
            current["showings"].append({"time": text, "url": href, "id": showing_match.group(2)})

    return [movie for movie in movies if movie["showings"]]


def fetch_schedule(location: str, show_date: str) -> tuple[list[dict], str]:
    if location not in LOCATIONS:
        raise ValueError("Unknown location")
    datetime.strptime(show_date, "%Y-%m-%d")
    page_url = f"{BASE_URL}/{location}/feature-films/"
    site_id = SITE_IDS[location]
    response = requests.post(
        f"{BASE_URL}/graphql",
        json={"query": SHOWINGS_QUERY, "variables": {"date": show_date, "siteIds": [site_id]}},
        headers={
            "User-Agent": "HookyHistory/1.0 (+personal analytics)",
            "Accept": "application/json",
            "client-type": "consumer",
            "circuit-id": CIRCUIT_ID,
            "site-id": str(site_id),
            "is-electron-mode": "false",
            "Referer": page_url,
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ValueError(payload["errors"][0].get("message", "Hooky GraphQL error"))
    rows = ((payload.get("data") or {}).get("showingsForDate") or {}).get("data") or []
    grouped: dict[str, dict[str, Any]] = {}
    location_tz = ZoneInfo(LOCATION_TIMEZONES[location])
    for row in rows:
        movie_data = row.get("movie") or {}
        slug = movie_data.get("urlSlug")
        if not slug or not row.get("id") or not row.get("time"):
            continue
        movie = grouped.setdefault(slug, {
            "slug": slug,
            "title": movie_data.get("name") or slug.replace("-", " ").title(),
            "url": f"{BASE_URL}/{location}/movie/{slug}",
            "showings": [],
        })
        local_time = datetime.fromisoformat(row["time"].replace("Z", "+00:00")).astimezone(location_tz)
        movie["showings"].append({
            "time": local_time.strftime("%I:%M%p").lstrip("0"),
            "url": f"{BASE_URL}/{location}/checkout/showing/{slug}/{row['id']}",
            "id": str(row["id"]),
        })
    return list(grouped.values()), page_url


def save_snapshot(location: str, show_date: str, movies: list[dict], source_url: str) -> int:
    update_movie_popularity(movies)
    captured_at = datetime.now(timezone.utc).isoformat()
    showing_count = sum(len(movie["showings"]) for movie in movies)
    location_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date().isoformat()
    with db() as connection:
        existing = connection.execute(
            sql("""SELECT id FROM scrape_runs
                   WHERE location=? AND show_date=? ORDER BY captured_at DESC LIMIT 1"""),
            (location, show_date),
        ).fetchone()

        if existing and show_date < location_today:
            return int(existing["id"])

        if existing:
            run_id: int = int(existing["id"])
            connection.execute(
                sql("""UPDATE scrape_runs SET captured_at=?, source_url=?,
                       movie_count=?, showing_count=? WHERE id=?"""),
                (captured_at, source_url, len(movies), showing_count, run_id),
            )
            connection.execute(sql("DELETE FROM showings WHERE run_id=?"), (run_id,))
        else:
            insert_run = sql(
                """INSERT INTO scrape_runs
                   (location, show_date, captured_at, source_url, movie_count, showing_count)
                   VALUES (?, ?, ?, ?, ?, ?)"""
            )
            if DATABASE_URL:
                cursor = connection.execute(
                    insert_run + " RETURNING id",
                    (location, show_date, captured_at, source_url, len(movies), showing_count),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    raise RuntimeError("PostgreSQL did not return a snapshot id")
                run_id = int(inserted["id"])
            else:
                cursor = connection.execute(
                    insert_run,
                    (location, show_date, captured_at, source_url, len(movies), showing_count),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return a snapshot id")
                run_id = int(cursor.lastrowid)
        showing_rows = [
            (run_id, movie["slug"], movie["title"], showing["time"], showing["url"])
            for movie in movies
            for showing in movie["showings"]
        ]
        if showing_rows:
            cursor = connection.cursor()
            cursor.executemany(
                sql("""INSERT INTO showings
                   (run_id, movie_slug, movie_title, show_time, checkout_url)
                   VALUES (?, ?, ?, ?, ?)"""),
                showing_rows,
            )
    return run_id


def latest_snapshot(location: str, show_date: str):
    with db() as connection:
        run = connection.execute(
            sql("""SELECT * FROM scrape_runs WHERE location=? AND show_date=?
               ORDER BY captured_at DESC LIMIT 1"""),
            (location, show_date),
        ).fetchone()
        if not run:
            return None
        rows = connection.execute(
            sql("SELECT * FROM showings WHERE run_id=? ORDER BY id"), (run["id"],)
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        movie = grouped.setdefault(
            row["movie_slug"],
            {"slug": row["movie_slug"], "title": row["movie_title"], "showings": []},
        )
        movie["showings"].append({"time": row["show_time"], "url": row["checkout_url"]})
    movies = list(grouped.values())
    update_movie_popularity(movies)
    attach_cached_popularity(movies)
    return {"run": dict(run), "movies": movies}


def attach_cached_popularity(movies: list[dict]) -> None:
    """Attach a nullable popularity field to every movie returned by the API."""
    keys = [normalize_title(movie["title"]) for movie in movies]
    cached: dict[str, dict] = {}
    if keys:
        placeholders = ",".join("?" for _ in keys)
        with db() as connection:
            rows = connection.execute(
                sql(f"SELECT normalized_title, imdb_id, imdb_popularity, fetched_at FROM movie_popularity WHERE normalized_title IN ({placeholders})"),
                keys,
            ).fetchall()
        cached = {row["normalized_title"]: dict(row) for row in rows}
    for movie in movies:
        item = cached.get(normalize_title(movie["title"]), {})
        movie["imdb_id"] = item.get("imdb_id")
        movie["imdb_popularity"] = item.get("imdb_popularity")
        movie["imdb_popularity_fetched_at"] = item.get("fetched_at")


def update_movie_popularity(movies: list[dict]) -> None:
    """Refresh new or stale titles without making schedule collection depend on IMDb."""
    global _missing_omdb_key_logged
    if not movies:
        return
    if not os.environ.get("OMDB_API_KEY"):
        if not _missing_omdb_key_logged:
            logger.warning("IMDb popularity disabled: OMDB_API_KEY is missing in the web service")
            _missing_omdb_key_logged = True
        return
    now = datetime.now(timezone.utc)
    with db() as connection:
        rows = connection.execute(
            "SELECT normalized_title, imdb_id, fetched_at FROM movie_popularity"
        ).fetchall()
    cached = {row["normalized_title"]: row for row in rows}
    due: dict[str, dict] = {}
    for movie in movies:
        key = normalize_title(movie["title"])
        cached_row = cached.get(key)
        if cached_row:
            fetched_at = datetime.fromisoformat(cached_row["fetched_at"])
            ttl = (
                POPULARITY_CACHE_HOURS * 3600
                if cached_row["imdb_id"]
                else POPULARITY_ERROR_CACHE_MINUTES * 60
            )
            if (now - fetched_at).total_seconds() < ttl:
                continue
        due[key] = movie
    if not due:
        logger.info("IMDb popularity cache is fresh for %d movie(s)", len(movies))
        return

    logger.info("IMDb popularity lookup started for %d movie(s)", len(due))
    with ThreadPoolExecutor(max_workers=min(4, len(due))) as executor:
        futures = {executor.submit(lookup_movie, movie["title"]): (key, movie) for key, movie in due.items()}
        for future in as_completed(futures):
            key, movie = futures[future]
            try:
                result = future.result()
                with db() as connection:
                    connection.execute(
                        sql("""INSERT INTO movie_popularity
                            (normalized_title, movie_title, imdb_id, imdb_popularity, fetched_at)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(normalized_title) DO UPDATE SET
                            movie_title=excluded.movie_title, imdb_id=excluded.imdb_id,
                            imdb_popularity=excluded.imdb_popularity, fetched_at=excluded.fetched_at"""),
                        (key, movie["title"], result.imdb_id, result.rank, result.fetched_at),
                    )
                logger.info(
                    "IMDb popularity updated for %s: #%s (%s)",
                    movie["title"], result.rank, result.imdb_id,
                )
            except Exception as error:
                logger.warning("IMDb popularity lookup failed for %s: %s", movie["title"], error)
                failed_at = datetime.now(timezone.utc).isoformat()
                with db() as connection:
                    connection.execute(
                        sql("""INSERT INTO movie_popularity
                            (normalized_title, movie_title, imdb_id, imdb_popularity, fetched_at)
                            VALUES (?, ?, ?, NULL, ?)
                            ON CONFLICT(normalized_title) DO UPDATE SET fetched_at=excluded.fetched_at"""),
                        (key, movie["title"], "", failed_at),
                    )


@app.get("/")
def index():
    hutto_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES["hutto"])).date().isoformat()
    today_by_location = {
        location: datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date().isoformat()
        for location in LOCATIONS
    }
    return render_template(
        "index.html",
        locations=LOCATIONS,
        today=hutto_today,
        hooky_config={
            "manualFutureDays": MANUAL_FUTURE_DAYS,
            "cronFutureDays": FUTURE_DAYS,
            "todayByLocation": today_by_location,
        },
    )


@app.get("/health")
def health():
    try:
        with db() as connection:
            connection.execute("SELECT 1")
        return jsonify({"status": "ok", "database": "postgres" if DATABASE_URL else "sqlite"})
    except Exception as error:
        return jsonify({"status": "error", "error": str(error)}), 503


@app.get("/api/schedule")
def schedule():
    location = request.args.get("location", "hutto")
    if location not in LOCATIONS:
        return jsonify({"error": "Unknown location"}), 400
    location_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date().isoformat()
    show_date = request.args.get("date", location_today)
    refresh = request.args.get("refresh") == "1"
    try:
        snapshot = None if refresh else latest_snapshot(location, show_date)
        if snapshot is None:
            if show_date < location_today:
                return jsonify({"error": "No saved data is available for this past date, and Hooky no longer publishes its schedule."}), 404
            movies, source_url = fetch_schedule(location, show_date)
            save_snapshot(location, show_date, movies, source_url)
            snapshot = latest_snapshot(location, show_date)
        return jsonify(snapshot)
    except (ValueError, requests.RequestException) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        logger.exception("Failed to load schedule for %s on %s", location, show_date)
        return jsonify({"error": "Could not save the schedule", "detail": str(error)}), 500


def collect_all_locations(locations=None):
    locations = locations or list(LOCATIONS)
    results = []
    for location in locations:
        if location not in LOCATIONS:
            results.append({"location": location, "error": f"Unknown location: {location}"})
            continue
        location_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date()
        for offset in range(FUTURE_DAYS + 1):
            show_date = (location_today + timedelta(days=offset)).isoformat()
            try:
                movies, source_url = fetch_schedule(location, show_date)
                run_id = save_snapshot(location, show_date, movies, source_url)
                results.append({"location": location, "date": show_date, "run_id": run_id})
            except Exception as error:
                results.append({"location": location, "date": show_date, "error": str(error)})
    return results


@app.post("/api/collect")
def collect():
    collector_key = os.environ.get("COLLECTOR_KEY")
    if collector_key and request.headers.get("X-Collector-Key") != collector_key:
        return jsonify({"error": "Unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    return jsonify({"results": collect_all_locations(payload.get("locations"))})


@app.get("/api/history")
def history():
    location = request.args.get("location")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    conditions, params = [], []
    if location:
        conditions.append("location=?")
        params.append(location)
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
            conditions.append("show_date>=?")
            params.append(date_from)
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
            conditions.append("show_date<=?")
            params.append(date_to)
    except ValueError:
        return jsonify({"error": "Invalid date range"}), 400
    if date_from and date_to and date_from > date_to:
        return jsonify({"error": "date_from must not be after date_to"}), 400
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with db() as connection:
        rows = connection.execute(
            sql(f"""SELECT id, location, show_date, captured_at, movie_count, showing_count
                FROM scrape_runs {where} ORDER BY show_date, captured_at"""),
            params,
        ).fetchall()
        run_ids = [row["id"] for row in rows]
        details = {}
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            showing_rows = connection.execute(
                sql(f"""SELECT run_id, movie_title, COUNT(*) AS showing_count
                    FROM showings WHERE run_id IN ({placeholders})
                    GROUP BY run_id, movie_title ORDER BY run_id, movie_title"""),
                run_ids,
            ).fetchall()
            for showing in showing_rows:
                details.setdefault(showing["run_id"], []).append({
                    "title": showing["movie_title"],
                    "showing_count": showing["showing_count"],
                })
    result = []
    for row in rows:
        item = dict(row)
        item["movies"] = details.get(row["id"], [])
        result.append(item)
    return jsonify(result)


def popularity_impact(rank: int | None) -> int | None:
    """Map IMDb's inverse rank to a readable 0–100 logarithmic impact score."""
    if not rank or rank <= 0:
        return None
    return round(max(0, min(100, 100 - 25 * math.log10(rank))))


@app.get("/api/releases")
def release_timeline():
    today = datetime.now(timezone.utc).date()
    date_from = request.args.get("date_from", (today - timedelta(days=90)).isoformat())
    date_to = request.args.get("date_to", (today + timedelta(days=31)).isoformat())
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date range"}), 400
    if start > end:
        return jsonify({"error": "date_from must not be after date_to"}), 400

    with db() as connection:
        rows = connection.execute(
            """SELECT DISTINCT r.show_date, r.location, s.movie_title
               FROM scrape_runs r JOIN showings s ON s.run_id = r.id
               ORDER BY r.show_date, s.movie_title"""
        ).fetchall()
        popularity_rows = connection.execute(
            "SELECT normalized_title, imdb_id, imdb_popularity FROM movie_popularity"
        ).fetchall()

    popularity = {row["normalized_title"]: dict(row) for row in popularity_rows}
    releases: dict[str, dict] = {}
    for row in rows:
        key = normalize_title(row["movie_title"])
        item = releases.setdefault(key, {
            "title": row["movie_title"],
            "release_date": row["show_date"],
            "locations": set(),
        })
        if row["show_date"] < item["release_date"]:
            item["release_date"] = row["show_date"]
            item["locations"] = set()
        if row["show_date"] == item["release_date"]:
            item["locations"].add(row["location"])

    result = []
    first_recorded_date = min((item["release_date"] for item in releases.values()), default=None)
    baseline_excluded = 0
    for key, item in releases.items():
        # Movies present on the database's very first day were already playing;
        # their real first appearance is unknown, so do not label them releases.
        if item["release_date"] == first_recorded_date:
            baseline_excluded += 1
            continue
        if not date_from <= item["release_date"] <= date_to:
            continue
        imdb = popularity.get(key, {})
        rank = imdb.get("imdb_popularity")
        result.append({
            "title": item["title"],
            "release_date": item["release_date"],
            "imdb_id": imdb.get("imdb_id") or None,
            "imdb_popularity": rank,
            "impact_score": popularity_impact(rank),
            "location_count": len(item["locations"]),
        })
    result.sort(key=lambda item: (item["release_date"], -(item["impact_score"] or -1), item["title"]))
    return jsonify({
        "date_from": date_from,
        "date_to": date_to,
        "today": today.isoformat(),
        "baseline_excluded": baseline_excluded,
        "releases": result,
    })


@app.get("/api/compare")
def compare_locations():
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    if not date_from or not date_to:
        return jsonify({"error": "date_from and date_to are required"}), 400
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date range"}), 400
    if start > end:
        return jsonify({"error": "date_from must not be after date_to"}), 400

    with db() as connection:
        rows = connection.execute(
            sql("""SELECT r.location, r.show_date, s.movie_title, s.show_time
                   FROM scrape_runs r
                   LEFT JOIN showings s ON s.run_id = r.id
                   WHERE r.show_date>=? AND r.show_date<=?
                   ORDER BY r.location, r.show_date, s.movie_title, s.id"""),
            (date_from, date_to),
        ).fetchall()

    summaries = {
        slug: {"location": slug, "name": name, "days": set(), "movies": {}}
        for slug, name in LOCATIONS.items()
    }
    for row in rows:
        if row["location"] not in summaries:
            continue
        summary = summaries[row["location"]]
        summary["days"].add(row["show_date"])
        if not row["movie_title"]:
            continue
        movie = summary["movies"].setdefault(
            row["movie_title"], {"title": row["movie_title"], "showing_count": 0, "times": []}
        )
        movie["showing_count"] += 1
        if start == end:
            movie["times"].append(row["show_time"])

    result = []
    for summary in summaries.values():
        movies = sorted(summary["movies"].values(), key=lambda item: (-item["showing_count"], item["title"]))
        result.append({
            "location": summary["location"],
            "name": summary["name"],
            "days_available": len(summary["days"]),
            "unique_movie_count": len(movies),
            "showing_count": sum(movie["showing_count"] for movie in movies),
            "movies": movies,
        })
    result.sort(key=lambda item: (-item["showing_count"], item["name"]))
    return jsonify({
        "date_from": date_from,
        "date_to": date_to,
        "requested_days": (end - start).days + 1,
        "single_day": start == end,
        "locations": result,
    })


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
else:
    init_db()
