"""
post_to_blogger.py
------------------
Publishes ONE pending markdown post from posts/ to a Blogger blog using the
Blogger API v3 and an OAuth 2.0 refresh-token flow.

Design rules baked in:
- Max 1 post per run.
- Draft mode by default (AS_DRAFT=true). Live only when it is explicitly false.
- Validation runs BEFORE any API call.
- Skips today's post if one was already published today (by title/date).
- DRY_RUN=true does everything except the real API call and the file move.

Environment variables:
    BLOGGER_CLIENT_ID
    BLOGGER_CLIENT_SECRET
    BLOGGER_REFRESH_TOKEN
    BLOGGER_BLOG_ID
    AS_DRAFT   (default "true")
    DRY_RUN    (default "false")
"""

import os
import re
import sys
import time
import shutil
from datetime import date
from pathlib import Path

import requests
import markdown
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = PROJECT_ROOT / "posts"
PUBLISHED_DIR = POSTS_DIR / "published"

PORTFOLIO_DOMAIN = "ritika-rajput-portfolio.vercel.app"

TOKEN_URL = "https://oauth2.googleapis.com/token"
BLOGGER_POSTS_API = "https://www.googleapis.com/blogger/v3/blogs/{blog_id}/posts"

MIN_WORDS = 300
MIN_LINKS = 1
MAX_LINKS = 3

MAX_RETRIES = 3
BACKOFF_SECONDS = 3

load_dotenv(PROJECT_ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, treating blanks as missing."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def get_config() -> dict:
    """Collect configuration from the environment."""
    return {
        "client_id": env("BLOGGER_CLIENT_ID"),
        "client_secret": env("BLOGGER_CLIENT_SECRET"),
        "refresh_token": env("BLOGGER_REFRESH_TOKEN"),
        "blog_id": env("BLOGGER_BLOG_ID"),
        "as_draft": (env("AS_DRAFT", "true") or "true").lower() == "true",
        "dry_run": (env("DRY_RUN", "false") or "false").lower() == "true",
    }


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def get_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    """Exchange the refresh token for a short-lived access token."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    response = requests.post(TOKEN_URL, data=payload, timeout=30)

    if response.status_code != 200:
        error = "unknown"
        try:
            error = response.json().get("error", "unknown")
        except ValueError:
            pass
        if response.status_code == 400 and error == "invalid_grant":
            raise RuntimeError(
                "invalid_grant: refresh token rejected.\n"
                "Common causes: consent screen still in 'Testing' mode "
                "(tokens expire after ~7 days), or the token was revoked.\n"
                "Fix: publish the consent screen to Production, then re-run "
                "get_refresh_token.py and update BLOGGER_REFRESH_TOKEN."
            )
        if response.status_code == 401:
            raise RuntimeError(
                "401 Unauthorized: check BLOGGER_CLIENT_ID / "
                "BLOGGER_CLIENT_SECRET, then regenerate the refresh token."
            )
        raise RuntimeError(
            f"Failed to get access token (HTTP {response.status_code}): "
            f"{response.text}"
        )

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in token response: {response.text}")
    return token


# ---------------------------------------------------------------------------
# Selecting + parsing the post
# ---------------------------------------------------------------------------

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


def parse_post(raw_text: str) -> tuple[dict, str]:
    """Split a markdown file into (front_matter_dict, body_markdown)."""
    text = raw_text.replace("\r\n", "\n").strip()
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML front matter: {exc}")
    if not isinstance(meta, dict):
        raise ValueError("Front matter must be a YAML mapping (key: value).")
    body = "\n".join(lines[closing + 1:]).strip()
    return meta, body


def normalize_labels(value) -> list[str]:
    """Accept labels as a comma string or a YAML list; return a clean list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def markdown_to_html(body: str) -> str:
    """Convert markdown to HTML, preserving raw HTML (images) and tables."""
    return markdown.markdown(
        body,
        extensions=["fenced_code", "tables", "md_in_html"],
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def strip_links(text: str) -> str:
    """Replace markdown links with their anchor text (raw HTML kept as-is)."""
    text = re.sub(r"<[^>]+>", " ", text)  # drop raw HTML tags
    return re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)


def count_words(body: str) -> int:
    """Rough word count that ignores markdown syntax and raw HTML."""
    text = strip_links(body)
    text = re.sub(r"[#*`>_\-]", " ", text)
    return len(text.split())


def count_portfolio_links(body: str) -> int:
    """Count markdown links pointing at the portfolio domain."""
    pattern = re.compile(
        r"\[[^\]]+\]\(\s*https?://" + re.escape(PORTFOLIO_DOMAIN) + r"[^\)]*\)",
        re.IGNORECASE,
    )
    return len(pattern.findall(body))


def validate_post(meta: dict, body: str) -> None:
    """Validate before posting. Raises ValueError with a clear message."""
    title = str(meta.get("title", "")).strip()
    if not title:
        raise ValueError("Validation failed: 'title' is missing or empty.")

    words = count_words(body)
    if words < MIN_WORDS:
        raise ValueError(
            f"Validation failed: body has {words} words, minimum is {MIN_WORDS}."
        )

    links = count_portfolio_links(body)
    if links < MIN_LINKS:
        raise ValueError(
            "Validation failed: no link to "
            f"https://{PORTFOLIO_DOMAIN} found (need {MIN_LINKS}-{MAX_LINKS})."
        )
    if links > MAX_LINKS:
        raise ValueError(
            f"Validation failed: {links} portfolio links found, "
            f"maximum is {MAX_LINKS}."
        )


# ---------------------------------------------------------------------------
# Blogger API
# ---------------------------------------------------------------------------

def get_existing_titles(blog_id: str, access_token: str) -> set[str]:
    """Fetch existing post titles so we can avoid duplicates."""
    url = BLOGGER_POSTS_API.format(blog_id=blog_id)
    headers = {"Authorization": f"Bearer {access_token}"}
    titles: set[str] = set()
    params = {"maxResults": 100, "fields": "items(title),nextPageToken"}
    while True:
        response = requests.get(url, headers=headers, params=params, timeout=30)
        if response.status_code != 200:
            print(
                f"Warning: could not list existing posts "
                f"(HTTP {response.status_code}). Skipping duplicate check."
            )
            return titles
        data = response.json()
        for item in data.get("items", []):
            title = item.get("title")
            if title:
                titles.add(title.strip().lower())
        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
    return titles


def create_post(blog_id, access_token, title, html, labels, is_draft) -> dict:
    """Create a Blogger post, retrying on 5xx / 429."""
    url = BLOGGER_POSTS_API.format(blog_id=blog_id)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    params = {"isDraft": "true" if is_draft else "false"}
    payload = {
        "kind": "blogger#post",
        "title": title,
        "content": html,
        "labels": labels,
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = requests.post(url, headers=headers, params=params,
                                 json=payload, timeout=60)

        if response.status_code in (200, 201):
            return response.json()

        status = response.status_code
        if status in (400, 401, 403):
            if status == 401:
                raise RuntimeError(
                    "401 Unauthorized: access token rejected. The refresh token "
                    "may be invalid or the consent screen is still in Testing."
                )
            if status == 403:
                raise RuntimeError(
                    "403 Forbidden: token likely lacks the Blogger scope, or the "
                    "Blogger API is not enabled, or you are not the blog owner. "
                    "Re-run get_refresh_token.py with the blogger scope."
                )
            raise RuntimeError(
                f"400 Bad Request: Blogger rejected the post. Details: {response.text}"
            )

        if status == 429 or 500 <= status < 600:
            last_error = f"HTTP {status}: {response.text[:200]}"
            wait = BACKOFF_SECONDS * attempt
            print(f"Attempt {attempt}/{MAX_RETRIES} failed ({last_error}). "
                  f"Retrying in {wait}s...")
            time.sleep(wait)
            continue

        raise RuntimeError(f"Unexpected HTTP {status}: {response.text[:300]}")

    raise RuntimeError(f"Failed after {MAX_RETRIES} attempts. Last: {last_error}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    config = get_config()
    dry_run = config["dry_run"]

    print("=" * 60)
    print("Blogger Auto-Post")
    print(f"  AS_DRAFT = {config['as_draft']}")
    print(f"  DRY_RUN  = {dry_run}")
    print("=" * 60)

    post_file = pick_oldest_pending_post()
    if post_file is None:
        print("No pending posts in posts/. Nothing to do. Exiting.")
        return 0
    print(f"Selected post: {post_file.name}")

    raw_text = post_file.read_text(encoding="utf-8")
    meta, body = parse_post(raw_text)
    validate_post(meta, body)

    title = str(meta.get("title", "")).strip()
    labels = normalize_labels(meta.get("labels"))
    html = markdown_to_html(body)

    print(f"  Title  : {title}")
    print(f"  Labels : {', '.join(labels) if labels else '(none)'}")
    print(f"  Words  : {count_words(body)}")
    print(f"  Links  : {count_portfolio_links(body)} -> portfolio")

    required = ["client_id", "client_secret", "refresh_token", "blog_id"]
    missing = [key for key in required if not config.get(key)]
    if missing and not dry_run:
        env_names = ", ".join("BLOGGER_" + k.upper() for k in missing)
        print(f"ERROR: missing required environment variables: {env_names}")
        return 2

    if dry_run:
        print("\nDRY_RUN=true -> skipping real API call and file move.")
        print("Validation passed and HTML was generated successfully.")
        print(f"HTML preview (first 300 chars):\n{html[:300]}")
        return 0

    print("\nRequesting access token...")
    access_token = get_access_token(
        config["client_id"], config["client_secret"], config["refresh_token"]
    )

    print("Checking existing posts for duplicates...")
    existing_titles = get_existing_titles(config["blog_id"], access_token)
    if title.strip().lower() in existing_titles:
        print(f"Duplicate detected: a post titled '{title}' already exists.")
        print("Skipping. Move or rename this file if it should be posted.")
        return 0

    print(f"Creating post (draft={config['as_draft']})...")
    result = create_post(
        config["blog_id"], access_token, title, html, labels, config["as_draft"]
    )
    post_url = result.get("url") or result.get("alternateLink") or "(no URL returned)"
    print(f"\nSuccess! Post created: {post_url}")

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    destination = PUBLISHED_DIR / post_file.name
    shutil.move(str(post_file), str(destination))
    print(f"Moved {post_file.name} -> posts/published/{post_file.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError) as err:
        print(f"\nERROR: {err}")
        sys.exit(1)
