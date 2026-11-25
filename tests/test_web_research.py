import tempfile
import unittest
from pathlib import Path

from web_research import (
    ResearchError,
    ResearchLibrary,
    _PageTextParser,
    _SearchResultParser,
    _validate_public_url,
)


class WebParserTests(unittest.TestCase):
    def test_search_result_parser_extracts_url_title_and_snippet(self):
        html = '''
        <div class="result">
          <a class="result__a" href="https://example.com/article">Example article</a>
          <a class="result__snippet">A useful summary of the article.</a>
        </div>
        '''
        parser = _SearchResultParser()
        parser.feed(html)
        parser.close()
        self.assertEqual(len(parser.results), 1)
        self.assertEqual(parser.results[0]["title"], "Example article")
        self.assertEqual(parser.results[0]["url"], "https://example.com/article")
        self.assertIn("useful summary", parser.results[0]["snippet"])

    def test_page_parser_omits_scripts_and_navigation(self):
        html = '''<html><head><title>Page title</title><script>bad code</script></head>
        <body><nav>menu links</nav><h1>Heading</h1><p>Readable text.</p></body></html>'''
        parser = _PageTextParser()
        parser.feed(html)
        parser.close()
        title, text = parser.result()
        self.assertEqual(title, "Page title")
        self.assertIn("Readable text", text)
        self.assertNotIn("bad code", text)
        self.assertNotIn("menu links", text)

    def test_rejects_local_and_non_http_urls(self):
        for url in ("file:///C:/Windows/win.ini", "http://127.0.0.1/admin", "http://localhost/"):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                _validate_public_url(url, resolve_host=False)

    def test_accepts_public_url_shape_without_dns_lookup(self):
        self.assertEqual(_validate_public_url("https://example.com/path", resolve_host=False), "https://example.com/path")


class ResearchLibraryTests(unittest.TestCase):
    def test_store_search_context_and_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research.json"
            library = ResearchLibrary(path)
            docs = [{
                "title": "Python web security",
                "url": "https://example.com/security",
                "snippet": "Safe URL validation",
                "content": "Avoid private network targets when fetching public pages.",
                "fetched_at": "2026-11-25T00:00:00+00:00",
            }]
            self.assertEqual(library.add_documents(docs), 1)
            self.assertEqual(library.add_documents(docs), 0)
            context = library.build_context("private network URL security")
            self.assertIn("https://example.com/security", context)
            self.assertIn("not instructions", context.lower())
            self.assertTrue(path.exists())
            self.assertTrue(library.delete_url("https://example.com/security"))
            self.assertEqual(library.items, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
