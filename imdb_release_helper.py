"""IMDb title matching and popularity lookup shared by Flask and the CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

import requests

OMDB_API_URL = "https://www.omdbapi.com/"
IMDB_GRAPHQL_URL = "https://api.graphql.imdb.com/"
IMDB_TITLE_URL = "https://www.imdb.com/title/"
REQUEST_TIMEOUT = 8
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


@dataclass(frozen=True)
class ImdbPopularity:
    title: str
    imdb_id: str
    rank: int | None
    release_date: str | None
    poster_url: str | None
    fetched_at: str


def normalize_title(title: str) -> str:
    return " ".join(title.casefold().split())


def clean_release_title(title: str) -> tuple[str, int | None]:
    """Remove cinema presentation labels while preserving an explicit year hint."""
    value = " ".join(title.split())
    year_match = re.search(r"\s*\(((?:19|20)\d{2})\)\s*$", value)
    year = int(year_match.group(1)) if year_match else None
    if year_match:
        value = value[:year_match.start()].strip()
    value = re.sub(r"^3D\s+", "", value, flags=re.I)
    value = re.sub(
        r"\s*\((?:Open Caption(?:ed)?|Closed Caption(?:ed)?|Spanish Dub(?:bed)?|English Dub(?:bed)?|Hindi|Tamil|Telugu)\)\s*$",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+w/(?:Bonus Footage|Q&A).*$", "", value, flags=re.I)
    return value.strip(), year


def parse_omdb_release_date(value: Any) -> str | None:
    if not value or value == "N/A":
        return None


def parse_poster_url(value: Any) -> str | None:
    poster = str(value or "")
    return poster if poster.startswith(("https://", "http://")) else None
    try:
        return datetime.strptime(str(value), "%d %b %Y").date().isoformat()
    except ValueError:
        return None


def parse_rank(value: Any) -> int | None:
    digits = re.sub(r"\D", "", str(value or ""))
    rank = int(digits) if digits else 0
    return rank if rank > 0 else None


def parse_imdb_popularity(html: str) -> int | None:
    score = re.search(
        r'<div\b[^>]*data-testid=["\']hero-rating-bar__popularity__score["\'][^>]*>\s*([\d][\d,.\s]*)\s*</div>',
        html,
        re.I,
    )
    if score:
        return parse_rank(score.group(1))
    # Current IMDb pages embed the same GraphQL shape used by the live API:
    # "meterRank":{"currentRank":465,...}. Keep the old flat form too.
    embedded = re.search(
        r'["\']meterRank["\']\s*:\s*\{[^{}]{0,500}?["\']currentRank["\']\s*:\s*(\d+)',
        html,
        re.I,
    )
    if not embedded:
        embedded = re.search(r'["\']meterRank["\']\s*:\s*(\d+)', html, re.I)
    return parse_rank(embedded.group(1)) if embedded else None


def find_imdb_movie(title: str, year: int | None = None, session=requests) -> dict[str, Any]:
    api_key = os.environ.get("OMDB_API_KEY")
    if not api_key:
        raise RuntimeError("OMDB_API_KEY is not configured")
    params: dict[str, Any] = {"apikey": api_key, "t": title, "type": "movie", "r": "json"}
    if year is not None:
        params["y"] = year
    response = session.get(OMDB_API_URL, params=params, headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if data.get("Response") != "True":
        raise ValueError(f"OMDb did not find {title}: {data.get('Error', 'unknown error')}")
    imdb_id = data.get("imdbID", "")
    if not re.fullmatch(r"tt\d+", imdb_id):
        raise ValueError("OMDb returned an invalid IMDb ID")
    return data


def find_imdb_id(title: str, year: int | None = None, session=requests) -> tuple[str, str]:
    data = find_imdb_movie(title, year, session)
    return data["imdbID"], data.get("Title") or title


def fetch_imdb_popularity(imdb_id: str, session=requests) -> int:
    query = "query Popularity($id: ID!) { title(id: $id) { id meterRank { currentRank } } }"
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Accept-Language": "en-US,en;q=0.9", "Origin": "https://www.imdb.com", "Referer": "https://www.imdb.com/", "User-Agent": USER_AGENT, "X-Imdb-Client-Name": "imdb-web-next-localized"}
    graphql_error = "no currentRank in the response"
    try:
        response = session.post(
            IMDB_GRAPHQL_URL,
            json={"query": query, "variables": {"id": imdb_id}},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        payload = response.json()
        response.raise_for_status()
        rank = (((payload.get("data") or {}).get("title") or {}).get("meterRank") or {}).get("currentRank")
        if isinstance(rank, int) and rank > 0:
            return rank
        errors = payload.get("errors") or []
        if errors:
            graphql_error = errors[0].get("message", graphql_error)
    except (requests.RequestException, ValueError, TypeError) as error:
        graphql_error = str(error)
    response = session.get(f"{IMDB_TITLE_URL}{imdb_id}/", headers={"Accept": "text/html", "Accept-Language": "en-US,en;q=0.9", "User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    rank = parse_imdb_popularity(response.text)
    if rank is None:
        raise ValueError(f"IMDb Popularity was not found (GraphQL: {graphql_error})")
    return rank


def lookup_movie(title: str, year: int | None = None, session=requests) -> ImdbPopularity:
    cleaned_title, title_year = clean_release_title(title)
    data = find_imdb_movie(cleaned_title, year or title_year, session)
    imdb_id = data["imdbID"]
    return ImdbPopularity(
        data.get("Title") or cleaned_title,
        imdb_id,
        fetch_imdb_popularity(imdb_id, session),
        parse_omdb_release_date(data.get("Released")),
        parse_poster_url(data.get("Poster")),
        datetime.now(timezone.utc).isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Look up a movie's current IMDb popularity rank")
    parser.add_argument("title")
    parser.add_argument("--year", type=int)
    args = parser.parse_args()
    print(json.dumps(asdict(lookup_movie(args.title, args.year)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
