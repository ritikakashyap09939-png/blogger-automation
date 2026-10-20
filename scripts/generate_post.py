"""
generate_post.py
----------------
Generates ONE original blog post about one of Ritika's portfolio skills using
the Google Gemini API, then saves it to posts/<date>-<slug>.md for the posting
step to pick up.

Key behaviours:
- Rotates through skills.txt so every skill gets covered evenly.
- Logs the chosen skill + title to used_topics.txt (committed back each run),
  and never repeats a title (or a very similar one).
- The blog must be PRIMARILY about exactly one skill from skills.txt.
- Every post contains 1-2 natural portfolio links with VARIED anchor text
  (an anchors log keeps two identical anchors from appearing back to back).
- Validates the result and regenerates up to 3 times before failing clearly.

Environment variables:
    GEMINI_API_KEY   (required)
    GEMINI_MODEL     (optional, default "gemini-2.0-flash")
"""

import os
import re
import sys
import json
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = PROJECT_ROOT / "posts"
SKILLS_FILE = PROJECT_ROOT / "skills.txt"
USED_TOPICS_FILE = PROJECT_ROOT / "used_topics.txt"
ANCHORS_FILE = PROJECT_ROOT / "anchors.txt"

PORTFOLIO_URL = "https://ritika-rajput-portfolio.vercel.app"
PORTFOLIO_DOMAIN = "ritika-rajput-portfolio.vercel.app"

DEFAULT_MODEL = "gemini-2.0-flash"

MIN_WORDS = 700
MAX_WORDS = 1000
MIN_LINKS = 1
MAX_LINKS = 2
MAX_ATTEMPTS = 3

load_dotenv(PROJECT_ROOT / ".env")


def env(name: str, default: str | None = None) -> str | None:
    """Read an environment variable, treating blanks as missing."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


# ---------------------------------------------------------------------------
# Small helpers for the log files
# ---------------------------------------------------------------------------

def read_lines(path: Path) -> list[str]:
    """Return the non-empty, stripped lines of a file (empty list if missing)."""
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def read_skills() -> list[str]:
    """Load the canonical skills list. This file is never edited by code."""
    skills = read_lines(SKILLS_FILE)
    if not skills:
        raise RuntimeError(
            "skills.txt is missing or empty. It must contain one skill per line."
        )
    return skills


def pick_skill(skills: list[str]) -> str:
    """
    Rotate through skills.txt so all skills are used evenly.

    We count how many times each skill already appears in used_topics.txt and
    pick the one with the fewest uses (ties broken by order in skills.txt).
    """
    used_counts = {skill: 0 for skill in skills}
    for line in read_lines(USED_TOPICS_FILE):
        # Format of each line: "Skill | Title"
        skill_part = line.split("|", 1)[0].strip()
        if skill_part in used_counts:
            used_counts[skill_part] += 1

    # min() is stable: it keeps the first skill on ties (i.e. file order).
    return min(skills, key=lambda s: used_counts[s])


def read_used_titles() -> list[str]:
    """Return all previously used titles (lowercased) from used_topics.txt."""
    titles = []
    for line in read_lines(USED_TOPICS_FILE):
        if "|" in line:
            titles.append(line.split("|", 1)[1].strip().lower())
    return titles


def titles_too_similar(new_title: str, used_titles: list[str]) -> bool:
    """
    Very light duplicate check: exact match, or a high word-overlap ratio.
    Keeps the bot from posting near-identical topics again.
    """
    new_words = set(re.findall(r"[a-z0-9]+", new_title.lower()))
    for used in used_titles:
        if new_title.lower() == used:
            return True
        used_words = set(re.findall(r"[a-z0-9]+", used))
        if not new_words or not used_words:
            continue
        overlap = len(new_words & used_words) / len(new_words | used_words)
        if overlap >= 0.8:
            return True
    return False


def read_last_anchor() -> str | None:
    """Return the most recently used anchor text, so we can avoid repeating it."""
    anchors = read_lines(ANCHORS_FILE)
    return anchors[-1] if anchors else None


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def build_prompt(skill: str, allowed_skills: list[str], avoid_anchor: str | None) -> str:
    """Compose the single instruction we send to Gemini."""
    allowed_list = ", ".join(allowed_skills)
    anchor_rule = (
        f"Do NOT use '{avoid_anchor}' as an anchor this time; pick a different "
        "natural phrase."
        if avoid_anchor else
        "Pick a natural, varied anchor phrase."
    )
    return f"""
You are writing for the personal developer blog of Ritika Rajput.

ALLOWED SKILLS (the only subjects you may write about):
{allowed_list}

TASK: Write one blog post that is PRIMARILY about exactly this skill: "{skill}".

HARD RULES:
- The main subject must be "{skill}". Do not make any other technology the
  main subject.
- Never claim experience with anything that is not in the ALLOWED SKILLS list.
- First-person developer voice. Practical, specific, no fluff, no keyword
  stuffing. Do not invent statistics or fake results.
- 700-1000 words. Use "##" headings, short paragraphs, and a short conclusion.
- Include one short code example in a fenced code block ONLY if it genuinely
  helps; otherwise skip it.
- Include exactly one natural link to my portfolio (two only if it truly fits).
  Use varied, natural anchor text - never repeat the same anchor phrase.
  Link format: [anchor text]({PORTFOLIO_URL})
- {anchor_rule}

Respond with ONLY a JSON object (no markdown fences, no extra text) shaped like:
{{
  "title": "post title",
  "skill": "{skill}",
  "labels": ["label1", "label2", "label3"],
  "image_query": "2-4 english words describing the topic visually",
  "body_markdown": "full markdown body here"
}}
""".strip()


def call_gemini(api_key: str, model: str, prompt: str) -> str:
    """Call Gemini and return the raw text of the first candidate."""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # Ask Gemini for strict JSON so parsing is reliable.
        "generationConfig": {"responseMimeType": "application/json"},
    }
    response = requests.post(url, params={"key": api_key}, json=payload, timeout=90)
    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini API error (HTTP {response.status_code}): {response.text[:300]}"
        )
    data = response.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemini response shape: {exc}\n{data}")


def parse_gemini_json(text: str) -> dict:
    """Parse the model's JSON, tolerating stray code fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Gemini did not return valid JSON: {exc}")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def strip_links(text: str) -> str:
    """Replace markdown links with just their anchor text."""
    return re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)


def word_count(markdown_body: str) -> int:
    """Count words ignoring markdown symbols."""
    text = strip_links(markdown_body)
    text = re.sub(r"[#*`>_\-]", " ", text)
    return len(text.split())


def count_portfolio_links(markdown_body: str) -> int:
    """Count links that point at the portfolio domain."""
    pattern = re.compile(
        r"\[[^\]]+\]\(\s*https?://" + re.escape(PORTFOLIO_DOMAIN) + r"[^\)]*\)",
        re.IGNORECASE,
    )
    return len(pattern.findall(markdown_body))


def find_anchors(markdown_body: str) -> list[str]:
    """Return the anchor texts used for portfolio links."""
    pattern = re.compile(
        r"\[([^\]]+)\]\(\s*https?://" + re.escape(PORTFOLIO_DOMAIN) + r"[^\)]*\)",
        re.IGNORECASE,
    )
    return [m.strip() for m in pattern.findall(markdown_body)]


def validate(data: dict, skills: list[str]) -> None:
    """Raise ValueError with a clear message if the generated post is invalid."""
    title = str(data.get("title", "")).strip()
    skill = str(data.get("skill", "")).strip()
    labels = data.get("labels")
    body = str(data.get("body_markdown", "")).strip()
    image_query = str(data.get("image_query", "")).strip()

    if not title:
        raise ValueError("generated title is empty")
    if skill not in skills:
        raise ValueError(
            f"generated skill '{skill}' is not an exact line in skills.txt"
        )
    if not isinstance(labels, list) or not (3 <= len(labels) <= 5):
        raise ValueError("labels must be a list of 3 to 5 items")
    if not image_query:
        raise ValueError("image_query is empty")

    words = word_count(body)
    if words < MIN_WORDS or words > MAX_WORDS:
        raise ValueError(f"body has {words} words, expected {MIN_WORDS}-{MAX_WORDS}")

    links = count_portfolio_links(body)
    if links < MIN_LINKS or links > MAX_LINKS:
        raise ValueError(f"body has {links} portfolio links, expected {MIN_LINKS}-{MAX_LINKS}")

    # Title / labels must not be mainly about a technology outside skills.txt.
    # We only flag obvious off-list technology keywords in the TITLE.
    off_list = [
        "kubernetes", "docker", "rust", "golang", "java", "kotlin",
        "django", "flask", "laravel", "angular", "vue",
    ]
    title_lower = title.lower()
    main_skill_words = {w for w in re.findall(r"[a-z]+", skill.lower())}
    for tech in off_list:
        if tech in title_lower and tech not in main_skill_words:
            raise ValueError(f"title mentions off-list technology '{tech}'")


# ---------------------------------------------------------------------------
# Writing the post file
# ---------------------------------------------------------------------------

def slugify(title: str) -> str:
    """Build a safe, sortable filename from the title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    slug = slug[:50] or "post"
    return f"{date.today().isoformat()}-{slug}.md"


def write_post_file(meta: dict, body: str) -> Path:
    """Write the markdown file with YAML front matter and return its path."""
    labels = ", ".join(str(l) for l in meta["labels"])
    front_matter = (
        "---\n"
        f"title: {meta['title']}\n"
        f"labels: {labels}\n"
        f"skill: {meta['skill']}\n"
        f"image_query: {meta['image_query']}\n"
        "---\n"
    )
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    path = POSTS_DIR / slugify(meta["title"])
    path.write_text(front_matter + "\n" + body + "\n", encoding="utf-8")
    return path


def main() -> int:
    api_key = env("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set.")
        return 1

    model = env("GEMINI_MODEL", DEFAULT_MODEL)
    skills = read_skills()
    skill = pick_skill(skills)
    used_titles = read_used_titles()
    last_anchor = read_last_anchor()

    print("=" * 60)
    print("Generate post")
    print(f"  model : {model}")
    print(f"  skill : {skill}")
    print("=" * 60)

    data = None
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"Attempt {attempt}/{MAX_ATTEMPTS}...")
        try:
            raw = call_gemini(api_key, model, build_prompt(skill, skills, last_anchor))
            data = parse_gemini_json(raw)
            validate(data, skills)

            # Extra checks that need the used-title list.
            if titles_too_similar(str(data["title"]), used_titles):
                raise ValueError(f"title '{data['title']}' is too similar to an earlier post")

            # Anchor must differ from the previous one.
            anchors = find_anchors(str(data["body_markdown"]))
            if last_anchor and anchors and anchors[0].lower() == last_anchor.lower():
                raise ValueError(
                    f"first anchor '{anchors[0]}' repeats the previous anchor"
                )
            break
        except (ValueError, RuntimeError) as exc:
            last_error = str(exc)
            print(f"  invalid: {last_error}")
            data = None
    else:
        print(f"\nERROR: could not generate a valid post after {MAX_ATTEMPTS} attempts: {last_error}")
        return 1

    # Persist results.
    path = write_post_file(data, str(data["body_markdown"]).strip())
    with USED_TOPICS_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{data['skill']} | {data['title']}\n")
    for anchor in find_anchors(str(data["body_markdown"])):
        with ANCHORS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"{anchor}\n")

    print(f"\nSaved: {path.relative_to(PROJECT_ROOT)}")
    print(f"Title: {data['title']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError) as err:
        print(f"\nERROR: {err}")
        sys.exit(1)
