import os
import uuid
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

# Importing app initializes its DB. Never touch the developer's saved history.
with patch.dict(os.environ, {"HOOKY_DB": ":memory:", "DATABASE_URL": ""}):
    import app
from imdb_release_helper import ImdbPopularity


class ReleaseCollectionTests(unittest.TestCase):
    def setUp(self):
        database_path = Path(__file__).resolve().parent / f"test-{uuid.uuid4().hex}.sqlite3"
        self.addCleanup(lambda: database_path.unlink(missing_ok=True))
        db_patch = patch.multiple(app, DB_PATH=database_path, DATABASE_URL="")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        app.init_db()
        self.catalog_patch = patch.object(app, "collect_release_catalog", return_value=([], []))
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)
        self.today = datetime.now(timezone.utc).date()
        self.brand = {"test": {"locations": {"one": {"timezone": "UTC", "base_url": "https://example.com/one"}}}}
        self.movie = {"slug": "future-film", "title": "Future Film", "showings": [
            {"time": "7:00PM", "url": "https://example.com/checkout/1"}
        ]}

    def test_cron_discovers_distant_release_without_manual_refresh(self):
        release_day = (self.today + timedelta(days=60)).isoformat()

        def schedule(brand, location, day):
            return ([self.movie] if day >= release_day else []), "https://example.com"

        metadata = ImdbPopularity("Future Film", "tt1234567", 123, release_day, None,
                                  datetime.now(timezone.utc).isoformat())
        with patch.object(app, "BRANDS", self.brand), patch.object(app, "FUTURE_DAYS", 90), \
                patch.object(app, "fetch_schedule", side_effect=schedule) as fetch, \
                patch.object(app, "lookup_movie", return_value=metadata) as lookup, \
                patch.dict(os.environ, {"OMDB_API_KEY": "test"}):
            results = app.collect_all_locations()
            self.assertEqual(len(results), 91)
            self.assertFalse(any("error" in item for item in results))
            self.assertEqual(fetch.call_count, 91)
            lookup.assert_called_once_with("Future Film")
            with patch.object(app, "update_movie_popularity") as update:
                response = app.app.test_client().get("/api/releases?brand=test&refresh_metadata=0")
                update.assert_not_called()
            data = response.get_json()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(data["releases"][0]["release_date"], release_day)
            self.assertEqual(data["collection_coverage"][0]["collected_through"],
                             (self.today + timedelta(days=90)).isoformat())

    def test_catalog_film_without_showtimes_has_no_future_cutoff(self):
        self.catalog_patch.stop()
        release_day = (self.today + timedelta(days=800)).isoformat()
        response = Mock(text='<h2>Coming Soon</h2><a href="/one/movie/future-film">Future Film</a>')
        metadata = ImdbPopularity("Future Film", "tt1234567", 123, release_day, None,
                                  datetime.now(timezone.utc).isoformat())
        with patch.object(app, "BRANDS", self.brand), patch.object(app, "FUTURE_DAYS", 0), \
                patch.object(app.requests, "get", return_value=response), \
                patch.object(app, "fetch_schedule", return_value=([], "https://example.com")), \
                patch.object(app, "lookup_movie", return_value=metadata) as lookup, \
                patch.dict(os.environ, {"OMDB_API_KEY": "test"}):
            results = app.collect_all_locations()
            self.assertFalse(any("error" in result for result in results))
            lookup.assert_called_once_with("Future Film")
            client = app.app.test_client()
            data = client.get("/api/releases?brand=test&date_to=&refresh_metadata=0").get_json()
            self.assertEqual(data["releases"][0]["release_date"], release_day)
            self.assertEqual(data["date_to"], release_day)
            self.assertTrue(data["future_unlimited"])
            self.assertIsNotNone(data["catalog_updated_at"])
            limited = client.get(f"/api/releases?brand=test&date_to={self.today.isoformat()}&refresh_metadata=0").get_json()
            self.assertEqual(limited["releases"], [])
            with app.db() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM showings").fetchone()[0], 0)

    def test_catalog_parser_includes_coming_soon_without_checkout_links(self):
        html = (Path(__file__).resolve().parent.parent / "Feature Films.html").read_text(encoding="utf-8")
        movies = app.parse_release_catalog(html, "https://hookyentertainment.com/hutto/feature-films/")
        self.assertIn("Spider-Man: Brand New Day", {movie["title"] for movie in movies})
        self.assertEqual(len(movies), len({movie["slug"] for movie in movies}))

    def test_catalog_failure_keeps_previously_discovered_titles(self):
        self.catalog_patch.stop()
        response = Mock(text='<a href="/one/movie/future-film">Future Film</a>')
        with patch.object(app, "BRANDS", self.brand):
            with patch.object(app.requests, "get", return_value=response):
                movies, results = app.collect_release_catalog("test", "one")
                self.assertEqual(len(movies), 1)
            with patch.object(app.requests, "get", side_effect=RuntimeError("offline")):
                movies, results = app.collect_release_catalog("test", "one")
                self.assertTrue(all("error" in result for result in results))
            with app.db() as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM release_catalog").fetchone()[0], 1)

    def test_metadata_failure_does_not_lose_schedules(self):
        with patch.object(app, "BRANDS", self.brand), patch.object(app, "FUTURE_DAYS", 0), \
                patch.object(app, "fetch_schedule", return_value=([self.movie], "https://example.com")), \
                patch.object(app, "update_movie_popularity", side_effect=RuntimeError("metadata unavailable")), \
                self.assertLogs(app.logger, level="ERROR"):
            results = app.collect_all_locations()
        self.assertIn("run_id", results[0])
        self.assertEqual(results[1]["stage"], "release_metadata")
        with app.db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM showings").fetchone()[0], 1)

    def test_failed_day_does_not_stop_future_discovery(self):
        def schedule(brand, location, day):
            if day == self.today.isoformat():
                raise RuntimeError("temporary failure")
            return [self.movie], "https://example.com"

        with patch.object(app, "BRANDS", self.brand), patch.object(app, "FUTURE_DAYS", 14), \
                patch.object(app, "fetch_schedule", side_effect=schedule), \
                patch.object(app, "update_movie_popularity") as update:
            results = app.collect_all_locations()
        self.assertIn("error", results[0])
        self.assertIn("run_id", results[-1])
        update.assert_called_once_with([self.movie])

    def test_movie_locations_distinguishes_empty_missing_and_partial_schedules(self):
        locations = {slug: {"name": slug.title(), "timezone": "UTC"}
                     for slug in ("one", "two", "empty", "missing")}
        brands = {"test": {"locations": locations}, "other": {"locations": locations}}
        day = self.today.isoformat()
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        with patch.object(app, "BRANDS", brands), patch.object(app, "fetch_schedule") as fetch:
            app.save_snapshot("test", "one", day, [self.movie], "https://example.com")
            app.save_snapshot("test", "one", tomorrow, [self.movie], "https://example.com")
            app.save_snapshot("test", "two", day, [self.movie], "https://example.com")
            app.save_snapshot("test", "empty", day, [], "https://example.com")
            app.save_snapshot("other", "missing", day, [self.movie], "https://example.com")
            response = app.app.test_client().get(
                f"/api/schedule-locations?brand=test&date_from={day}&date_to={tomorrow}")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            items = {item["location"]: item for item in data["locations"]}
            self.assertEqual(data["requested_days"], 2)
            self.assertEqual(items["one"]["available_days"], 2)
            self.assertEqual(len(items["one"]["movies"][0]["dates"]), 2)
            self.assertEqual(items["two"]["available_days"], 1)
            self.assertEqual(items["empty"]["available_days"], 1)
            self.assertEqual(items["empty"]["movies"], [])
            self.assertEqual(items["missing"]["available_days"], 0)
            self.assertEqual(items["missing"]["movies"], [])
            fetch.assert_not_called()

    def test_movie_locations_validates_dates_and_brand(self):
        client = app.app.test_client()
        for query in ("brand=unknown", "date_from=invalid", "date_from=2026-11-05&date_to=2026-11-01",
                      "date_from=2026-01-01&date_to=2028-01-01"):
            with self.subTest(query=query):
                self.assertEqual(client.get(f"/api/schedule-locations?{query}").status_code, 400)


if __name__ == "__main__":
    unittest.main()
