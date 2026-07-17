from __future__ import annotations

import os
import re
import sqlite3
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template, request

try:
    import psycopg
    from psycopg.rows import dict_row
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
    "delray-beach": "Delray Beach",
    "fredericksburg": "Fredericksburg",
    "homestead": "Homestead",
    "hutto": "Hutto",
    "nashville": "Nashville",
    "southlake": "Southlake",
    "waxahachie": "Waxahachie",
}
LOCATION_TIMEZONES = {
    "addison": "America/Chicago", "baytown": "America/Chicago",
    "cary": "America/New_York", "delray-beach": "America/New_York",
    "fredericksburg": "America/New_York", "homestead": "America/New_York",
    "hutto": "America/Chicago", "nashville": "America/Chicago",
    "southlake": "America/Chicago", "waxahachie": "America/Chicago",
}

app = Flask(__name__)
logger = logging.getLogger(__name__)


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
    raise last_error


def parse_movies(html: str, page_url: str) -> list[dict]:
    """Parse the SSR fallback: movie link followed by its checkout links."""
    soup = BeautifulSoup(html, "html.parser")
    movies: list[dict] = []
    current = None
    movie_pattern = re.compile(r"/movie/([^/?#]+)")
    showing_pattern = re.compile(r"/checkout/showing/([^/?#]+)/([^/?#]+)")

    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
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
        raise ValueError("Неизвестная локация")
    datetime.strptime(show_date, "%Y-%m-%d")
    source_url = f"{BASE_URL}/{location}/feature-films/?date={show_date}"
    response = requests.get(
        source_url,
        headers={"User-Agent": "HookyHistory/1.0 (+personal analytics)"},
        timeout=25,
    )
    response.raise_for_status()
    return parse_movies(response.text, source_url), source_url


def save_snapshot(location: str, show_date: str, movies: list[dict], source_url: str) -> int:
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
            return existing["id"]

        if existing:
            run_id = existing["id"]
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
                run_id = cursor.fetchone()["id"]
            else:
                cursor = connection.execute(
                    insert_run,
                    (location, show_date, captured_at, source_url, len(movies), showing_count),
                )
                run_id = cursor.lastrowid
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
    grouped = {}
    for row in rows:
        movie = grouped.setdefault(
            row["movie_slug"],
            {"slug": row["movie_slug"], "title": row["movie_title"], "showings": []},
        )
        movie["showings"].append({"time": row["show_time"], "url": row["checkout_url"]})
    return {"run": dict(run), "movies": list(grouped.values())}


@app.get("/")
def index():
    hutto_today = datetime.now(ZoneInfo(LOCATION_TIMEZONES["hutto"])).date().isoformat()
    return render_template("index.html", locations=LOCATIONS, today=hutto_today)


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
            if show_date != location_today:
                return jsonify({"error": "Для этой даты ещё нет сохранённого снимка. Живой HTML Hooky отдаёт только активный день."}), 404
            movies, source_url = fetch_schedule(location, show_date)
            save_snapshot(location, show_date, movies, source_url)
            snapshot = latest_snapshot(location, show_date)
        return jsonify(snapshot)
    except (ValueError, requests.RequestException) as error:
        return jsonify({"error": str(error)}), 400
    except Exception as error:
        logger.exception("Failed to load schedule for %s on %s", location, show_date)
        return jsonify({"error": "Не удалось сохранить расписание", "detail": str(error)}), 500


def collect_all_locations(locations=None):
    locations = locations or list(LOCATIONS)
    results = []
    for location in locations:
        try:
            if location not in LOCATIONS:
                raise ValueError(f"Unknown location: {location}")
            show_date = datetime.now(ZoneInfo(LOCATION_TIMEZONES[location])).date().isoformat()
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
    params, where = [], ""
    if location:
        where, params = "WHERE location=?", [location]
    with db() as connection:
        rows = connection.execute(
            sql(f"""SELECT id, location, show_date, captured_at, movie_count, showing_count
                FROM scrape_runs {where} ORDER BY show_date, captured_at"""),
            params,
        ).fetchall()
    return jsonify([dict(row) for row in rows])


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
else:
    init_db()
