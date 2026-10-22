"""
fetch_image.py
--------------
Finds a landscape photo for the OLDEST pending post using the Openverse
search API (anonymous, NO API key required) and inserts it at the very top
of that post's markdown body as raw HTML.

Why raw HTML in the markdown?
- The poster converts markdown to HTML; raw <img> tags pass straight through.
- Blogger uses the FIRST image in a post as its card thumbnail.

Behaviour:
- Uses the post's front-matter `image_query`. If the search finds nothing,
  retries with a simpler query derived from the title, then falls back to
  "coding laptop".
- Never reuses a photo: seen Openverse image IDs are logged in used_images.txt.
- Skips results that have no direct image url.
- Hotlinks the Openverse direct image URL (we do NOT upload images to Blogger).
- Adds a caption crediting the creator and the source page, via Openverse.
- If no photo can be found, the job is NOT failed: it prints a warning and
  the post is published without an image.
"""

import html
import re
import sys
import time
from pathlib import Path

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = PROJECT_ROOT / "posts"
USED_IMAGES_FILE = PROJECT_ROOT / "used_images.txt"

OPENVERSE_SEARCH_URL = "https://api.openverse.org/v1/images/"
FALLBACK_QUERY = "coding laptop"
USER_AGENT = "ritika-blogger-automation/1.0"

MAX_RETRIES = 3  # retries AFTER the initial request (max 4 requests per query)
BACKOFF_SECONDS = 5


def read_used_ids() -> set[str]:
    """Return the set of Openverse image IDs we have already used."""
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


def search_openverse(query: str) -> list[dict]:
    """
    Search Openverse for wide, large, commercial-use photographs.
    Returns the raw result dicts. No API key needed (anonymous access).
    On HTTP 429 (too many requests) waits and retries up to MAX_RETRIES times.
    """
    params = {
        "q": query,
        "license_type": "commercial",
        "category": "photograph",
        "aspect_ratio": "wide",
        "size": "large",
        "page_size": 20,
    }
    headers = {"User-Agent": USER_AGENT}

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(
                OPENVERSE_SEARCH_URL, headers=headers, params=params, timeout=30
            )
        except requests.RequestException as exc:
            print(f"  Openverse request failed for '{query}': {exc}")
            return []

        if response.status_code == 429:
            if attempt >= MAX_RETRIES:
                break
            wait = BACKOFF_SECONDS * (attempt + 1)
            print(f"  Openverse: too many requests (HTTP 429) for '{query}'. "
                  f"Waiting {wait}s then retrying "
                  f"(retry {attempt + 1}/{MAX_RETRIES})...")
            time.sleep(wait)
            continue

        if response.status_code != 200:
            print(f"  Openverse error (HTTP {response.status_code}) for '{query}', continuing.")
            return []

        try:
            return response.json().get("results", [])
        except ValueError:
            print(f"  Openverse returned invalid JSON for '{query}', continuing.")
            return []

    print(f"  Openverse still rate-limiting (HTTP 429) for '{query}' "
          f"after {MAX_RETRIES} retries. Giving up on this query.")
    return []


def choose_photo(post_meta: dict, title: str) -> dict | None:
    """Try image_query, then a title-derived query, then the fallback query."""
    used_ids = read_used_ids()

    image_query = str(post_meta.get("image_query") or "").strip()
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
        print(f"  Searching Openverse for: '{query}'")
        for result in search_openverse(query):
            # Skip results with no direct image url (nowhere to hotlink).
            if not str(result.get("url") or "").strip():
                continue
            image_id = str(result.get("id") or "").strip()
            if image_id and image_id not in used_ids:
                return result
    return None


def build_image_html(result: dict, title: str) -> str:
    """Build the <img> + credit caption HTML inserted at the top of the post."""
    src = str(result.get("url") or "").strip()
    creator = str(result.get("creator") or "").strip()
    creator_url = str(result.get("creator_url") or "").strip()
    source_url = str(result.get("foreign_landing_url") or "").strip()
    license_id = str(result.get("license") or "").strip()
    license_version = str(result.get("license_version") or "").strip()
    alt = (title or "blog post image").replace('"', "'")

    img = (
        f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" '
        f'style="max-width:100%;height:auto">'
    )

    # e.g. license "by" + version "2.0" -> "CC BY 2.0".
    if license_id:
        license_label = "CC " + license_id.upper()
        if license_version:
            license_label += f" {license_version}"
    else:
        license_label = "an open license"

    who = html.escape(creator or "Unknown")
    if creator_url:
        who = (f'<a href="{html.escape(creator_url, quote=True)}" '
               f'target="_blank" rel="noopener">{who}</a>')

    license_link = html.escape(source_url or "https://openverse.org", quote=True)
    caption = (
        f'<p><small>Photo by {who} '
        f'(<a href="{license_link}" target="_blank" rel="noopener">{license_label}</a>) '
        f'via <a href="https://openverse.org" target="_blank" rel="noopener">Openverse</a>'
        f'</small></p>'
    )
    return f"{img}\n{caption}"


def main() -> int:
    post_file = pick_oldest_pending_post()
    if post_file is None:
        print("No pending posts in posts/. Nothing to fetch an image for.")
        return 0
    print(f"Post: {post_file.name}")

    raw = post_file.read_text(encoding="utf-8")
    meta, body = parse_front_matter(raw)
    title = str(meta.get("title") or "").strip()

    # Idempotency: if an image was already inserted, do nothing.
    if body.lstrip().startswith("<img"):
        print("Image already present at the top of the post. Skipping.")
        return 0

    photo = choose_photo(meta, title)
    if photo is None:
        print("WARNING: no unused Openverse photo found for this post. "
              "Continuing without an image.")
        return 0

    image_id = str(photo.get("id"))
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
        fh.write(f"{image_id}\n")

    print(f"Inserted image id {image_id} by {photo.get('creator') or 'Unknown'}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as err:
        print(f"\nERROR: {err}")
        sys.exit(1)
