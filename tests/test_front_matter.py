"""
Mocked regression test for the YAML front matter bug.

A title such as "Mastering Fluid Layouts: Moving Beyond Rigid Breakpoints"
used to be written unquoted, which made yaml.safe_load fail with
"mapping values are not allowed here".

This test runs the real generate / fetch_image / post code paths with:
- the Gemini and Openverse HTTP calls mocked,
- every other request blocked, so NO real network call can happen,
- all file writes redirected to a temporary directory.

Run with:  python -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_image  # noqa: E402
import generate_post  # noqa: E402
import post_to_blogger  # noqa: E402

SKILL = "Web Development"

TITLES = [
    "Mastering Fluid Layouts: Moving Beyond Rigid Breakpoints",
    'Mastering "Fluid" Layouts: The Developer\'s Guide',
]

FAKE_PHOTO = {
    "id": "test-image-0001",
    "url": "https://example.com/photo.jpg",
    "creator": "Test Creator",
    "creator_url": "https://example.com/creator",
    "foreign_landing_url": "https://example.com/photo-page",
    "license": "by",
    "license_version": "2.0",
}

FILLER = (
    "Layouts stay flexible when the spacing rules live in one place "
    "instead of being scattered across every stylesheet in the project "
    "folder today."
)

TEST_ENV = {
    "GEMINI_API_KEY": "test-key",
    "GEMINI_MODEL": "test-model",
    "DRY_RUN": "true",
    "AS_DRAFT": "true",
    "BLOGGER_CLIENT_ID": "test-client-id",
    "BLOGGER_CLIENT_SECRET": "test-client-secret",
    "BLOGGER_REFRESH_TOKEN": "test-refresh-token",
    "BLOGGER_BLOG_ID": "test-blog-id",
}


def make_body() -> str:
    """A body long enough for the validators, with one portfolio link."""
    parts = ["## What actually works", ""]
    for _ in range(32):
        parts.append(FILLER)
        parts.append("")
    parts.append("## Where to go next")
    parts.append(
        "Read more in [my portfolio guide](https://ritika-rajput-portfolio.vercel.app)."
    )
    return "\n".join(parts)


def gemini_payload(title: str) -> str:
    return json.dumps({
        "title": title,
        "skill": SKILL,
        "labels": ["web development", "css", "layout"],
        "image_query": "fluid layout code",
        "body_markdown": make_body(),
    })


def blocked_request(*args, **kwargs):
    raise AssertionError("network access is not allowed in this test")


class FrontMatterTestCase(unittest.TestCase):
    """Sandboxed runs of the real scripts: temp dirs, no network."""

    @contextlib.contextmanager
    def sandbox(self):
        """Point every script at a temp dir and block all HTTP traffic."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            posts_dir = root / "posts"
            posts_dir.mkdir(parents=True, exist_ok=True)
            with contextlib.ExitStack() as stack:
                stack.enter_context(
                    mock.patch.object(generate_post, "PROJECT_ROOT", root))
                stack.enter_context(
                    mock.patch.object(generate_post, "POSTS_DIR", posts_dir))
                stack.enter_context(
                    mock.patch.object(generate_post, "USED_TOPICS_FILE",
                                     root / "used_topics.txt"))
                stack.enter_context(
                    mock.patch.object(generate_post, "ANCHORS_FILE",
                                     root / "anchors.txt"))
                stack.enter_context(
                    mock.patch.object(fetch_image, "POSTS_DIR", posts_dir))
                stack.enter_context(
                    mock.patch.object(fetch_image, "USED_IMAGES_FILE",
                                     root / "used_images.txt"))
                stack.enter_context(
                    mock.patch.object(post_to_blogger, "POSTS_DIR", posts_dir))
                stack.enter_context(
                    mock.patch.object(post_to_blogger, "PUBLISHED_DIR",
                                     posts_dir / "published"))
                # Block every real HTTP call.
                stack.enter_context(
                    mock.patch("requests.get", side_effect=blocked_request))
                stack.enter_context(
                    mock.patch("requests.post", side_effect=blocked_request))
                stack.enter_context(mock.patch.dict(os.environ, TEST_ENV))
                yield posts_dir

    # -- helpers ---------------------------------------------------------

    def run_generate(self, title: str) -> Path:
        """Run generate_post.main() with a mocked Gemini answer."""
        with mock.patch.object(
            generate_post, "call_gemini", return_value=gemini_payload(title)
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(generate_post.main(), 0)
        return self.generated_file()

    def generated_file(self) -> Path:
        files = sorted(self.posts_dir.glob("*.md"))
        self.assertEqual(len(files), 1, "exactly one post file should exist")
        return files[0]

    def front_matter_block(self, path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(lines[0].strip(), "---")
        closing = lines.index("---", 1)
        return lines[: closing + 1]

    # -- tests -----------------------------------------------------------

    def test_generated_front_matter_is_quoted_and_parses(self):
        for title in TITLES:
            with self.subTest(title=title), self.sandbox() as posts_dir:
                self.posts_dir = posts_dir
                path = self.run_generate(title)

                # Every value is written as a double-quoted YAML scalar.
                for line in self.front_matter_block(path)[1:-1]:
                    key, _, value = line.partition(":")
                    self.assertTrue(
                        value.strip().startswith('"') and value.strip().endswith('"'),
                        f"{key} is not quoted: {line}",
                    )

                # The poster reads the exact title back.
                meta, body = post_to_blogger.parse_post(
                    path.read_text(encoding="utf-8")
                )
                self.assertEqual(meta["title"], title)
                self.assertEqual(meta["skill"], SKILL)
                self.assertEqual(
                    post_to_blogger.normalize_labels(meta["labels"]),
                    ["web development", "css", "layout"],
                )
                self.assertIn("## What actually works", body)

    def test_fetch_image_keeps_front_matter_intact(self):
        for title in TITLES:
            with self.subTest(title=title), self.sandbox() as posts_dir:
                self.posts_dir = posts_dir
                path = self.run_generate(title)
                original_block = self.front_matter_block(path)

                with mock.patch.object(
                    fetch_image, "search_openverse", return_value=[FAKE_PHOTO]
                ):
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(fetch_image.main(), 0)

                # The front matter block is unchanged after the rewrite.
                self.assertEqual(self.front_matter_block(path), original_block)

                # The rewritten file still parses and keeps the title.
                meta, body = post_to_blogger.parse_post(
                    path.read_text(encoding="utf-8")
                )
                self.assertEqual(meta["title"], title)
                self.assertTrue(body.lstrip().startswith("<img"))
                self.assertIn("<img src=", body)

                # fetch_image itself also reads the title correctly.
                fm_meta, _ = fetch_image.parse_front_matter(
                    path.read_text(encoding="utf-8")
                )
                self.assertEqual(fm_meta["title"], title)

    def test_post_to_blogger_dry_run_accepts_colon_title(self):
        for title in TITLES:
            with self.subTest(title=title), self.sandbox() as posts_dir:
                self.posts_dir = posts_dir
                self.run_generate(title)

                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    exit_code = post_to_blogger.main()
                self.assertEqual(exit_code, 0)
                self.assertIn(f"Title  : {title}", buffer.getvalue())

    def test_legacy_unquoted_front_matter_still_parses(self):
        """Old files (title unquoted, colon inside) must still work."""
        for title in TITLES:
            with self.subTest(title=title), self.sandbox() as posts_dir:
                legacy = (
                    "---\n"
                    f"title: {title}\n"
                    "labels: web development, css, layout\n"
                    f"skill: {SKILL}\n"
                    "image_query: fluid layout code\n"
                    "---\n"
                    "\n"
                    f"{make_body()}\n"
                )
                path = posts_dir / "2026-01-01-legacy.md"
                path.write_text(legacy, encoding="utf-8")

                meta, body = post_to_blogger.parse_post(legacy)
                self.assertEqual(meta["title"], title)
                self.assertEqual(meta["skill"], SKILL)
                self.assertIn("## What actually works", body)

                fm_meta, fm_body = fetch_image.parse_front_matter(legacy)
                self.assertEqual(fm_meta["title"], title)
                self.assertIn("## What actually works", fm_body)

                # The fallback parser splits on the FIRST colon only.
                self.assertEqual(
                    post_to_blogger.parse_simple_front_matter(
                        f"title: {title}\nskill: {SKILL}\n"
                    )["title"],
                    title,
                )
                self.assertEqual(
                    fetch_image.parse_simple_front_matter(
                        f"title: {title}\nskill: {SKILL}\n"
                    )["title"],
                    title,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
