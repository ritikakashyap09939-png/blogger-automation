"""
fetch_image.py
--------------
Finds a landscape photo for the OLDEST pending post using the Pexels API and
inserts it at the very top of that post's markdown body as raw HTML.

Why raw HTML in the markdown?
- The poster converts markdown to HTML; raw <img> tags pass straight through.
- Blogger uses the FIRST image in a post as its card thumbnail.

Behaviour:
- Uses the post's front-matter `image_query`. If the search finds nothing,
  retries with a simpler query derived from the title, then falls back to
  "coding laptop".
- Never reuses a photo: seen Pexels photo IDs are logged in used_images.txt.
- Hotlinks the Pexels image URL (we do NOT upload images to Blogger).
- Adds a caption "Photo by NAME on Pexels" linking to the photo page.

Environment variables:
    PEXELS_API_KEY   (required)
"""

import os
import re
import sys
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = PROJECT_ROOT / "posts"
USED_IMAGES_FILE = PROJECT_ROOT / "used_images.txt"

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
FALLBACK_QUERY = "coding laptop"

load_dotenv(PROJECT_ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, treating blanks as missing."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def read_used_ids() -> set[str]:
    """Return the set of Pexels photo IDs we have already used."""
    if not USED_IMAGES_FILE.exists():
        return set()
    return {line.strip() for line in USED_IMAGES_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()}


def pick_oldest_pending_post() -> Path | None:
    """Return the alphabetically-first .md file in posts/ (oldest first)."""
    if not POSTS_DIR.exists():
        return None
    candidates = [p for p in POSTS_DIR.iterdir()
                  if p.is_file() and p.suffix.lower() == ".md"]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name)
    return candidates[0]


def parse_front_matter(raw: str) -> tuple[dict, str]:
    """Split markdown into (front_matter_dict, body). Tolerates missing matter."""
    text = raw.replace("\r\n", "\n")
    stripped = text.strip()
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.split("\n")
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    body = "\n".join(lines[closing + 1:])
    return meta, body


def search_pexels(api_key: str, query: str) -> list[dict]:
    """Search Pexels for landscape photos. Returns the raw photo dicts."""
    headers = {"Authorization": api_key}
    params = {"query": query, "orientation": "landscape", "per_page": 15}
    response = requests.get(PEXELS_SEARCH_URL, headers=headers, params=params, timeout=30)
    if response.status_code == 401:
        raise RuntimeError(
            "Pexels returned 401: PEXELS_API_KEY is missing or invalid."
        )
    if response.status_code != 200:
        print(f"  Pexels error (HTTP {response.status_code}) for '{query}', continuing.")
        return []
    return response.json().get("photos", [])


def choose_photo(api_key: str, post_meta: dict, title: str) -> dict | None:
    """Try image_query, then a title-derived query, then the fallback query."""
    used_ids = read_used_ids()

    image_query = str(post_meta.get("image_query", "")).strip()
    queries = []
    if image_query:
        queries.append(image_query)
    # A simple query derived from the first few words of the title.
    title_words = re.findall(r"[a-zA-Z]+", title or "")[:3]
    if title_words:
        simple = " ".join(w.lower() for w in title_words)
        if simple not in queries:
            queries.append(simple)
    if FALLBACK_QUERY not in queries:
        queries.append(FALLBACK_QUERY)

    for query in queries:
        print(f"  Searching Pexels for: '{query}'")
        for photo in search_pexels(api_key, query):
            photo_id = str(photo.get("id", ""))
            if photo_id and photo_id not in used_ids:
                return photo
    return None


def build_image_html(photo: dict, title: str) -> str:
    """Build the <img> + caption HTML inserted at the top of the post."""
    # Prefer the large landscape size, fall back to the original.
    src = (
        photo.get("src", {}).get("large")
        or photo.get("src", {}).get("original")
        or ""
    )
    photographer = photo.get("photographer", "Unknown")
    photo_page = photo.get("url", "https://www.pexels.com")
    alt = (title or "blog post image").replace('"', "'")

    img = (
        f'<img src="{src}" alt="{alt}" '
        f'style="max-width:100%;height:auto">'
    )
    caption = (
        f'<p><small>Photo by '
        f'<a href="{photo_page}" target="_blank" rel="noopener">{photographer}</a> '
        f'on Pexels</small></p>'
    )
    return f"{img}\n{caption}"


def main() -> int:
    api_key = env("PEXELS_API_KEY")
    if not api_key:
        print("ERROR: PEXELS_API_KEY is not set.")
        return 1

    post_file = pick_oldest_pending_post()
    if post_file is None:
        print("No pending posts in posts/. Nothing to fetch an image for.")
        return 0
    print(f"Post: {post_file.name}")

    raw = post_file.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    title = str(meta.get("title", "")).strip()

    # Idempotency: if an image was already inserted, do nothing.
    if body.lstrip().startswith("<img"):
        print("Image already present at the top of the post. Skipping.")
        return 0

    photo = choose_photo(api_key, meta, title)
    if photo is None:
        print("ERROR: no unused Pexels photo found for this post.")
        return 1

    photo_id = str(photo.get("id"))
    image_html = build_image_html(photo, title)

    # Rebuild the file: front matter, then image HTML, then the body.
    lines = raw.replace("\r\n", "\n").strip().split("\n")
    closing = None
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                closing = i
                break
    front_matter = "\n".join(lines[: closing + 1]) if closing is not None else ""
    new_content = f"{front_matter}\n\n{image_html}\n\n{body.strip()}\n"
    post_file.write_text(new_content, encoding="utf-8")

    with USED_IMAGES_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{photo_id}\n")

    print(f"Inserted photo id {photo_id} by {photo.get('photographer')}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as err:
        print(f"\nERROR: {err}")
        sys.exit(1)
