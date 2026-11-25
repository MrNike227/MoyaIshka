import tempfile
import unittest
from unittest import mock
from pathlib import Path

from web_research import (
    ResearchError,
    ResearchLibrary,
    _PageTextParser,
    _SearchResultParser,
    _search_bing_rss,
    search_web,
    _validate_public_url,
    format_research_digest,
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

    @mock.patch("web_research._request_search_response")
    def test_bing_rss_parser_extracts_title_link_and_snippet(self, request_mock):
        feed = '''<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item><title>Тестовый источник</title>
            <link>https://example.com/article</link>
            <description><![CDATA[Полезная &lt;b&gt;выдержка&lt;/b&gt;]]></description>
          </item>
        </channel></rss>'''.encode("utf-8")
        request_mock.return_value = (feed, "application/rss+xml; charset=utf-8", 200)
        result = _search_bing_rss("тест", 5, 15)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Тестовый источник")
        self.assertEqual(result[0]["url"], "https://example.com/article")
        self.assertIn("выдержка", result[0]["snippet"])

    @mock.patch("web_research._search_duckduckgo_html")
    @mock.patch("web_research._search_bing_rss")
    def test_search_falls_back_to_duckduckgo_when_bing_fails(self, bing_mock, ddg_mock):
        bing_mock.side_effect = ResearchError("Bing RSS недоступен")
        ddg_mock.return_value = [{"title": "Fallback", "url": "https://example.com/", "snippet": "ok"}]
        results = search_web("тест", max_results=3)
        self.assertEqual(results[0]["title"], "Fallback")
        bing_mock.assert_called_once()
        ddg_mock.assert_called_once()

    @mock.patch("web_research._search_duckduckgo_html")
    @mock.patch("web_research._search_bing_rss")
    def test_search_reports_both_provider_failures(self, bing_mock, ddg_mock):
        bing_mock.side_effect = ResearchError("нет XML")
        ddg_mock.side_effect = ResearchError("антибот-проверка")
        with self.assertRaises(ResearchError) as raised:
            search_web("тест")
        self.assertIn("Bing RSS", str(raised.exception))
        self.assertIn("DuckDuckGo", str(raised.exception))

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

    def test_research_digest_works_without_an_api_key(self):
        digest = format_research_digest("тестовый запрос", [{
            "title": "Публичный источник",
            "url": "https://example.com/article",
            "snippet": "Краткая выдержка без обращения к языковой модели.",
        }])
        self.assertIn("Интернет-поиск работает без API-ключа", digest)
        self.assertIn("Публичный источник", digest)
        self.assertIn("https://example.com/article", digest)
        self.assertIn("не сгенерированное мнение нейросети", digest)


class ResearchLibraryTests(unittest.TestCase):
    def test_research_library_default_retention_is_1000_items(self):
        with tempfile.TemporaryDirectory() as directory:
            library = ResearchLibrary(Path(directory) / "research.json")
            self.assertEqual(library.max_items, 1000)

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
