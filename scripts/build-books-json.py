#!/usr/bin/env python3
"""Convert a Goodreads library export CSV into a JSON file enriched with
Open Library descriptions.

Usage:
    python3 scripts/build-books-json.py \
        sample-data/goodreads_library_export.csv \
        sample-data/books.json
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OPENLIB_BASE = "https://openlibrary.org"
USER_AGENT = "mgt858-search-demo/1.0 (student project)"
SLEEP_BETWEEN_REQUESTS = 0.15

TAG_BLOCKLIST = {
    "read",
    "currently-reading",
    "to-read",
    "large type books",
    "translations into english",
    "new york times bestseller",
    "new york times reviewed",
    "open library staff picks",
    "accessible book",
    "in library",
    "protected daisy",
    "books and reading",
    "internet archive wishlist",
    "overdrive",
    "fiction",
    "nonfiction",
    "non-fiction",
    "history",
    "biography",
    "biographies",
    "memoir",
    "poetry",
    "drama",
    "essays",
    "short stories",
}

MIN_TAG_FREQUENCY = 3
MAX_TAGS_PER_BOOK = 6

GENRE_RULES = [
    ("Short stories", ["short stor"]),
    ("Poetry", ["poetry", "poems"]),
    ("Drama", ["drama", "plays (d"]),
    ("Memoir", ["memoir", "autobiograph"]),
    ("Biography", ["biography", "biographies"]),
    ("Essays", ["essay"]),
    ("History", ["history"]),
    ("Fiction", ["fiction", "novel"]),
]


def strip_goodreads_quote(value: str) -> str:
    """Goodreads wraps ISBN/ISBN13 in `="..."`. Strip that."""
    if not value:
        return ""
    match = re.match(r'^="(.*)"$', value)
    return match.group(1) if match else value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "book"


def clean_tag(tag: str) -> str:
    """Lowercase, collapse whitespace, trim common suffixes and parentheticals."""
    t = tag.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)
    t = re.sub(r",\s*fiction$", "", t)
    t = re.sub(r"^fiction,\s*", "", t)
    t = re.sub(r",\s*general$", "", t)
    t = re.sub(r",\s*fiction$", "", t)
    t = t.strip(" ,.;:")
    return t


def assign_genre(raw_tags: list[str], description: str = "", title: str = "") -> str:
    lowered = [t.lower() for t in raw_tags]
    for genre, needles in GENRE_RULES:
        for needle in needles:
            if any(needle in t for t in lowered):
                return genre

    desc = description.lower()
    ttl = title.lower()

    if any(w in desc for w in ["memoir", "autobiography", "her own life", "his own life"]):
        return "Memoir"
    if any(w in desc for w in ["biography", "the life of", "a life of"]):
        return "Biography"
    if any(w in desc for w in ["short stories", "collection of stories", "a collection of"]):
        return "Short stories"
    if any(w in desc for w in ["novel", "a story", "tale of", "tells the story", "fictional"]):
        return "Fiction"
    if any(p in ttl for p in [" guide", ": how", "how to ", "habits", "principles", "rules for", ": a", "your "]):
        return "Nonfiction"
    if any(w in desc for w in ["essays", "reflections", "argues", "research", "explores the", "strategies"]):
        return "Nonfiction"
    return "Other"


def normalize_collection(books: list[dict]) -> None:
    """In-place: clean tags, drop rare/blocklisted, assign genre."""
    from collections import Counter

    for book in books:
        raw = book.get("tags") or []
        book["_raw_tags"] = raw
        book["genre"] = assign_genre(
            raw,
            book.get("description", ""),
            book.get("title", ""),
        )
        cleaned = []
        seen = set()
        for t in raw:
            c = clean_tag(t)
            if not c or c in TAG_BLOCKLIST or c in seen:
                continue
            seen.add(c)
            cleaned.append(c)
        book["tags"] = cleaned

    freq = Counter()
    for b in books:
        freq.update(b["tags"])

    for b in books:
        kept = [t for t in b["tags"] if freq[t] >= MIN_TAG_FREQUENCY]
        b["tags"] = kept[:MAX_TAGS_PER_BOOK]
        b.pop("_raw_tags", None)
        if not b["tags"]:
            del b["tags"]


def http_get_json(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def normalize_description(desc) -> str:
    if isinstance(desc, dict):
        desc = desc.get("value", "")
    if not isinstance(desc, str):
        return ""
    desc = re.sub(r"\n{3,}", "\n\n", desc).strip()
    marker = "----------"
    if marker in desc:
        desc = desc.split(marker, 1)[0].strip()
    return desc


def fetch_description(isbn: str, title: str, author: str) -> tuple[str, list[str]]:
    """Return (description, subjects). Tries ISBN first, then title+author search."""
    work_key = None

    if isbn:
        edition = http_get_json(f"{OPENLIB_BASE}/isbn/{isbn}.json")
        if edition:
            works = edition.get("works") or []
            if works:
                work_key = works[0].get("key")
            if not work_key and edition.get("description"):
                return normalize_description(edition["description"]), []

    if not work_key:
        q = urllib.parse.urlencode(
            {"title": title, "author": author, "limit": 1, "fields": "key"}
        )
        search = http_get_json(f"{OPENLIB_BASE}/search.json?{q}")
        if search and search.get("docs"):
            work_key = search["docs"][0].get("key")

    if not work_key:
        return "", []

    work = http_get_json(f"{OPENLIB_BASE}{work_key}.json")
    if not work:
        return "", []

    description = normalize_description(work.get("description"))
    subjects = [s for s in (work.get("subjects") or [])[:8] if isinstance(s, str)]
    return description, subjects


def row_to_book(row: dict, description: str, subjects: list[str]) -> dict:
    title = row["Title"].strip()
    author = row["Author"].strip()
    book_id = row.get("Book Id") or slugify(f"{title}-{author}")
    shelves_raw = row.get("Bookshelves") or ""
    shelves = [s.strip() for s in shelves_raw.split(",") if s.strip()]
    shelf = row.get("Exclusive Shelf", "").strip()
    if shelf and shelf not in shelves:
        shelves.append(shelf)

    tags = list(dict.fromkeys(shelves + subjects))

    rating = row.get("My Rating", "0")
    try:
        rating_int = int(rating)
    except ValueError:
        rating_int = 0

    pages = row.get("Number of Pages") or ""
    try:
        pages_int = int(pages)
    except ValueError:
        pages_int = None

    year = row.get("Original Publication Year") or row.get("Year Published") or ""
    try:
        year_int = int(year)
    except ValueError:
        year_int = None

    year_range = None
    if year_int is not None:
        if year_int < 1800:
            year_range = "Pre-1800"
        elif year_int < 1900:
            year_range = "1800–1899"
        elif year_int < 1950:
            year_range = "1900–1949"
        elif year_int < 2000:
            year_range = "1950–1999"
        elif year_int < 2010:
            year_range = "2000–2009"
        elif year_int < 2020:
            year_range = "2010–2019"
        else:
            year_range = "2020+"

    book = {
        "id": f"book-{book_id}",
        "title": title,
        "author": author,
        "description": description,
        "publisher": row.get("Publisher", "").strip(),
        "year": year_int,
        "yearRange": year_range,
        "pages": pages_int,
        "myRating": rating_int,
        "shelf": shelf,
        "tags": tags,
        "dateRead": row.get("Date Read", "").strip(),
        "url": f"https://www.goodreads.com/book/show/{book_id}",
    }
    return {k: v for k, v in book.items() if v not in ("", None)}


def main():
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    csv_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    with csv_path.open() as f:
        rows = list(csv.DictReader(f))

    books = []
    missing_desc = 0
    for i, row in enumerate(rows, 1):
        title = row["Title"].strip()
        author = row["Author"].strip()
        isbn = strip_goodreads_quote(row.get("ISBN13") or "") or strip_goodreads_quote(
            row.get("ISBN") or ""
        )
        print(f"[{i}/{len(rows)}] {title} — {author}", flush=True)
        description, subjects = fetch_description(isbn, title, author)
        if not description:
            missing_desc += 1
        books.append(row_to_book(row, description, subjects))
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    normalize_collection(books)

    out_path.write_text(json.dumps(books, indent=2, ensure_ascii=False))
    with_genre = sum(1 for b in books if b.get("genre"))
    with_tags = sum(1 for b in books if b.get("tags"))
    print(
        f"\nWrote {len(books)} books to {out_path}. "
        f"{len(books) - missing_desc} have descriptions, {missing_desc} do not. "
        f"{with_genre} have a genre, {with_tags} have at least one tag."
    )


if __name__ == "__main__":
    main()
