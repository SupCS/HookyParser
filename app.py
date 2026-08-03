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

from imdb_release_helper import lookup_imdb_id, lookup_movie, normalize_title

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
                release_date TEXT,
                poster_url TEXT,
                poster_checked INTEGER NOT NULL DEFAULT 0,
                release_date_checked INTEGER NOT NULL DEFAULT 0,
                manual_override INTEGER NOT NULL DEFAULT 0,
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
                if DATABASE_URL:
                    popularity_columns = {
                        row["column_name"] for row in connection.execute(
                            """SELECT column_name FROM information_schema.columns
                               WHERE table_name='movie_popularity'"""
                        ).fetchall()
                    }
                else:
                    popularity_columns = {
                        row["name"] for row in connection.execute("PRAGMA table_info(movie_popularity)").fetchall()
                    }
                if "release_date" not in popularity_columns:
                    connection.execute("ALTER TABLE movie_popularity ADD COLUMN release_date TEXT")
                if "release_date_checked" not in popularity_columns:
                    connection.execute(
                        "ALTER TABLE movie_popularity ADD COLUMN release_date_checked INTEGER NOT NULL DEFAULT 0"
                    )
                if "poster_url" not in popularity_columns:
                    connection.execute("ALTER TABLE movie_popularity ADD COLUMN poster_url TEXT")
                if "poster_checked" not in popularity_columns:
                    connection.execute(
                        "ALTER TABLE movie_popularity ADD COLUMN poster_checked INTEGER NOT NULL DEFAULT 0"
                    )
                if "manual_override" not in popularity_columns:
                    connection.execute(
                        "ALTER TABLE movie_popularity ADD COLUMN manual_override INTEGER NOT NULL DEFAULT 0"
                    )
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
            """SELECT normalized_title, imdb_id, release_date, release_date_checked,
                      poster_checked, manual_override, fetched_at FROM movie_popularity"""
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
            # Version 1 was written by a broken release-date parser. Version 2
            # guarantees that OMDb's Released field was actually parsed.
            cache_is_complete = bool(
                cached_row["release_date_checked"] >= 2 and cached_row["poster_checked"]
            )
            cache_is_recent_error = not cached_row["imdb_id"]
            if (cache_is_complete or cache_is_recent_error) and (now - fetched_at).total_seconds() < ttl:
                continue
        due[key] = movie
    if not due:
        logger.info("IMDb popularity cache is fresh for %d movie(s)", len(movies))
        return

    logger.info("IMDb popularity lookup started for %d movie(s)", len(due))
    with ThreadPoolExecutor(max_workers=min(4, len(due))) as executor:
        futures = {}
        for key, movie in due.items():
            cached_row = cached.get(key)
            if cached_row and cached_row["manual_override"] and cached_row["imdb_id"]:
                future = executor.submit(lookup_imdb_id, cached_row["imdb_id"], movie["title"])
            else:
                future = executor.submit(lookup_movie, movie["title"])
            futures[future] = (key, movie)
        for future in as_completed(futures):
            key, movie = futures[future]
            try:
                result = future.result()
                with db() as connection:
                    connection.execute(
                        sql("""INSERT INTO movie_popularity
                            (normalized_title, movie_title, imdb_id, imdb_popularity, release_date, poster_url, poster_checked, release_date_checked, fetched_at)
                            VALUES (?, ?, ?, ?, ?, ?, 1, 2, ?)
                            ON CONFLICT(normalized_title) DO UPDATE SET
                            movie_title=excluded.movie_title, imdb_id=excluded.imdb_id,
                            imdb_popularity=COALESCE(excluded.imdb_popularity, movie_popularity.imdb_popularity),
                            release_date=COALESCE(excluded.release_date, movie_popularity.release_date),
                            poster_url=COALESCE(excluded.poster_url, movie_popularity.poster_url),
                            poster_checked=1,
                            release_date_checked=2,
                            fetched_at=excluded.fetched_at"""),
                        (key, result.title, result.imdb_id, result.rank, result.release_date, result.poster_url, result.fetched_at),
                    )
                if result.popularity_error:
                    logger.warning(
                        "IMDb metadata saved for %s, but popularity is unavailable: %s",
                        movie["title"], result.popularity_error,
                    )
                else:
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
                            (normalized_title, movie_title, imdb_id, imdb_popularity, release_date, fetched_at)
                            VALUES (?, ?, ?, NULL, NULL, ?)
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


@app.get("/api/schedule-range")
def schedule_range():
    location = request.args.get("location", "hutto")
    if location not in LOCATIONS:
        return jsonify({"error": "Unknown location"}), 400
    location_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date()
    date_from = request.args.get("date_from", location_today.isoformat())
    date_to = request.args.get("date_to", date_from)
    try:
        start = datetime.strptime(date_from, "%Y-%m-%d").date()
        end = datetime.strptime(date_to, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Invalid date range"}), 400
    if start > end:
        return jsonify({"error": "From date must not be after To date"}), 400
    days = (end - start).days + 1
    if days > 366:
        return jsonify({"error": "Date range cannot exceed 366 days"}), 400

    with db() as connection:
        rows = connection.execute(
            sql("""SELECT r.show_date, r.captured_at, s.movie_slug, s.movie_title,
                          s.show_time, s.checkout_url, s.id
                   FROM scrape_runs r LEFT JOIN showings s ON s.run_id = r.id
                   WHERE r.location=? AND r.show_date>=? AND r.show_date<=?
                   ORDER BY s.movie_title, r.show_date, s.id"""),
            (location, date_from, date_to),
        ).fetchall()

    grouped: dict[str, dict] = {}
    captured_at = None
    available_dates: set[str] = set()
    for row in rows:
        available_dates.add(row["show_date"])
        if captured_at is None or row["captured_at"] > captured_at:
            captured_at = row["captured_at"]
        if not row["movie_title"]:
            continue
        movie = grouped.setdefault(row["movie_slug"], {
            "slug": row["movie_slug"], "title": row["movie_title"], "dates": {}
        })
        movie["dates"].setdefault(row["show_date"], []).append({
            "time": row["show_time"], "url": row["checkout_url"]
        })
    movies = []
    for movie in grouped.values():
        movie["dates"] = [
            {"date": show_date, "showings": showings}
            for show_date, showings in movie["dates"].items()
        ]
        movies.append(movie)
    attach_cached_popularity(movies)
    return jsonify({
        "location": location,
        "date_from": date_from,
        "date_to": date_to,
        "requested_days": days,
        "available_days": len(available_dates),
        "captured_at": captured_at,
        "movies": movies,
    })


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


def parse_imdb_reference(value: str) -> str | None:
    match = re.search(r"(?:imdb\.com/title/)?(tt\d+)", value or "", re.IGNORECASE)
    return match.group(1).lower() if match else None


@app.post("/api/releases/imdb-override")
def set_imdb_override():
    collector_key = os.environ.get("COLLECTOR_KEY")
    if collector_key and request.headers.get("X-Collector-Key") != collector_key:
        return jsonify({"error": "Editor key required"}), 401
    payload = request.get_json(silent=True) or {}
    original_title = " ".join(str(payload.get("title") or "").split())
    imdb_value = " ".join(str(payload.get("imdb") or "").split())
    imdb_id = parse_imdb_reference(imdb_value)
    if not original_title or not imdb_value:
        return jsonify({"error": "Movie title and an IMDb title, URL, or tt ID are required"}), 400
    try:
        result = lookup_imdb_id(imdb_id, original_title) if imdb_id else lookup_movie(imdb_value)
    except Exception as error:
        return jsonify({"error": str(error)}), 400
    key = normalize_title(original_title)
    with db() as connection:
        connection.execute(
            sql("""INSERT INTO movie_popularity
                (normalized_title, movie_title, imdb_id, imdb_popularity, release_date,
                 poster_url, poster_checked, release_date_checked, manual_override, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 2, 1, ?)
                ON CONFLICT(normalized_title) DO UPDATE SET
                movie_title=excluded.movie_title, imdb_id=excluded.imdb_id,
                imdb_popularity=excluded.imdb_popularity, release_date=excluded.release_date,
                poster_url=excluded.poster_url, poster_checked=1,
                release_date_checked=2, manual_override=1, fetched_at=excluded.fetched_at"""),
            (key, result.title, result.imdb_id, result.rank, result.release_date,
             result.poster_url, result.fetched_at),
        )
    return jsonify({"status": "ok", "title": result.title, "imdb_id": result.imdb_id})


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
            """SELECT DISTINCT r.location, s.movie_title
               FROM scrape_runs r JOIN showings s ON s.run_id = r.id
               ORDER BY s.movie_title"""
        ).fetchall()

    unique_titles = {row["movie_title"] for row in rows}
    update_movie_popularity([{"title": title} for title in unique_titles])

    with db() as connection:
        popularity_rows = connection.execute(
            """SELECT normalized_title, movie_title, imdb_id, imdb_popularity,
                      release_date, poster_url, release_date_checked
               FROM movie_popularity"""
        ).fetchall()

    popularity = {row["normalized_title"]: dict(row) for row in popularity_rows}
    releases: dict[str, dict] = {}
    for row in rows:
        key = normalize_title(row["movie_title"])
        imdb = popularity.get(key)
        if not imdb or not imdb.get("imdb_id") or not imdb.get("release_date"):
            continue
        # Different cinema presentations and title-year suffixes resolve to one IMDb ID.
        item = releases.setdefault(imdb["imdb_id"], {
            "title": imdb["movie_title"],
            "release_date": imdb["release_date"],
            "imdb_id": imdb["imdb_id"],
            "imdb_popularity": imdb["imdb_popularity"],
            "poster_url": imdb["poster_url"],
            "locations": set(),
        })
        item["locations"].add(row["location"])
        if item["imdb_popularity"] is None and imdb["imdb_popularity"] is not None:
            item["imdb_popularity"] = imdb["imdb_popularity"]
        if not item["poster_url"] and imdb["poster_url"]:
            item["poster_url"] = imdb["poster_url"]

    result = []
    for item in releases.values():
        if not date_from <= item["release_date"] <= date_to:
            continue
        rank = item["imdb_popularity"]
        result.append({
            "title": item["title"],
            "release_date": item["release_date"],
            "imdb_id": item["imdb_id"],
            "imdb_popularity": rank,
            "impact_score": popularity_impact(rank),
            "poster_url": item["poster_url"],
            "location_count": len(item["locations"]),
        })
    ranked_ids = {item["imdb_id"] for item in result if item["imdb_popularity"] is not None}
    undefined: dict[str, dict] = {}
    for title in unique_titles:
        imdb = popularity.get(normalize_title(title), {})
        if imdb.get("imdb_id") in ranked_ids:
            continue
        if imdb.get("release_date") and not date_from <= imdb["release_date"] <= date_to:
            continue
        undefined_key = imdb.get("imdb_id") or normalize_title(title)
        if imdb.get("imdb_id") and not imdb.get("release_date"):
            reason = "Official release date unavailable"
        elif imdb.get("imdb_id"):
            reason = "IMDb rank unavailable"
        else:
            reason = "OMDb match unavailable"
        undefined.setdefault(undefined_key, {
            "title": imdb.get("movie_title") or title,
            "release_date": imdb.get("release_date"),
            "imdb_id": imdb.get("imdb_id") or None,
            "poster_url": imdb.get("poster_url"),
            "reason": reason,
        })
    result = [item for item in result if item["imdb_popularity"] is not None]
    result.sort(key=lambda item: (item["release_date"], -item["impact_score"], item["title"]))
    undefined_result = sorted(undefined.values(), key=lambda item: (item["release_date"] or "9999-99-99", item["title"]))
    return jsonify({
        "date_from": date_from,
        "date_to": date_to,
        "today": today.isoformat(),
        "omdb_configured": bool(os.environ.get("OMDB_API_KEY")),
        "releases": result,
        "undefined_releases": undefined_result,
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
