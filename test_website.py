"""Offline checks for the small, fixed GitHub Pages entry point."""

from html.parser import HTMLParser
from pathlib import Path
import unittest
from urllib.parse import urlsplit


DOCS = Path(__file__).resolve().parent / "docs"
CANONICAL = "https://1z99.github.io/project-ai-r36s/projects/227-tv/"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class WebsiteTest(unittest.TestCase):
    def setUp(self):
        self.page = Page()
        self.page.feed((DOCS / "index.html").read_text())

    def test_fixed_redirect_and_fallback_agree(self):
        refresh = [attrs["content"] for tag, attrs in self.page.elements
                   if tag == "meta" and attrs.get("http-equiv") == "refresh"]
        self.assertEqual(refresh, ["0; url=" + CANONICAL])
        canonical = [attrs["href"] for tag, attrs in self.page.elements
                     if tag == "link" and attrs.get("rel") == "canonical"]
        self.assertEqual(canonical, [CANONICAL])
        self.assertTrue(any(tag == "a" and attrs.get("href") == CANONICAL
                            for tag, attrs in self.page.elements))

    def test_only_intended_static_files_and_no_executable_content(self):
        self.assertEqual({path.name for path in DOCS.iterdir()},
                         {".nojekyll", "index.html", "landing.css"})
        for tag, attrs in self.page.elements:
            self.assertNotIn(tag, ("script", "iframe", "form", "object", "embed"))
            self.assertFalse(any(name.startswith("on") for name in attrs))
            if tag == "a":
                target = urlsplit(attrs["href"])
                self.assertEqual(target.scheme, "https")
                self.assertIn(target.hostname, ("1z99.github.io", "github.com"))
                self.assertFalse(target.query or target.username or target.password)
        css = (DOCS / "landing.css").read_text()
        self.assertNotIn("@import", css)
        self.assertNotIn("url(", css)

    def test_restrictive_policy_and_local_styles(self):
        policy = next(attrs["content"] for tag, attrs in self.page.elements
                      if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy")
        for directive in ("default-src 'none'", "style-src 'self'", "base-uri 'none'", "form-action 'none'"):
            self.assertIn(directive, policy)
        styles = [attrs["href"] for tag, attrs in self.page.elements
                  if tag == "link" and attrs.get("rel") == "stylesheet"]
        self.assertEqual(styles, ["landing.css"])
