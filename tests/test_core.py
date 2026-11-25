import json
import os
import tempfile
import unittest
import queue
from pathlib import Path

os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="moya_test_appdata_"))

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as main_module
from main import DEFAULT_CONFIG, MoyaIshkaApp, Store, extract_openai_content, read_attachment


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        self.store = Store(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_create_message_and_title(self):
        chat_id = self.store.create_conversation()
        self.store.add_message(chat_id, "user", "Привет, Ишка")
        self.store.add_message(chat_id, "assistant", "Привет!")
        self.assertEqual(self.store.get_conversation(chat_id)["title"], "Привет, Ишка")
        self.assertEqual(len(self.store.get_messages(chat_id)), 2)

    def test_export_markdown(self):
        chat_id = self.store.create_conversation("Проверка")
        self.store.add_message(chat_id, "user", "Вопрос")
        self.store.add_message(chat_id, "assistant", "Ответ")
        markdown = self.store.export_markdown(chat_id)
        self.assertIn("# Проверка", markdown)
        self.assertIn("## Вы", markdown)
        self.assertIn("Ответ", markdown)

    def test_clear_and_delete(self):
        chat_id = self.store.create_conversation()
        self.store.add_message(chat_id, "user", "Удаляемое сообщение")
        self.store.clear_messages(chat_id)
        self.assertEqual(self.store.get_messages(chat_id), [])
        self.store.delete_conversation(chat_id)
        self.assertIsNone(self.store.get_conversation(chat_id))

    def test_import_legacy_history(self):
        history_path = Path(self.temp.name) / "history.json"
        history_path.write_text(json.dumps([
            {"role": "user", "content": "Старый вопрос"},
            {"role": "assistant", "content": "Старый ответ"},
        ], ensure_ascii=False), encoding="utf-8")
        self.store.import_legacy_history(history_path)
        chats = self.store.list_conversations()
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(self.store.get_messages(chats[0]["id"])), 2)


class ParsingTests(unittest.TestCase):
    def test_extract_openai_content(self):
        self.assertEqual(extract_openai_content({"choices": [{"message": {"content": " Готово "}}]}), "Готово")

    def test_read_text_attachment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "note.txt"
            path.write_text("Тестовый текст", encoding="utf-8")
            self.assertEqual(read_attachment(path), "Тестовый текст")

    def test_attachment_size_is_limited(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "large.txt"
            path.write_text("я" * 45000, encoding="utf-8")
            text = read_attachment(path)
            self.assertLess(len(text), 45000)
            self.assertIn("Текст сокращён", text)


class KeyboardAndPrivacyTests(unittest.TestCase):
    def test_enter_sends_message(self):
        app = MoyaIshkaApp.__new__(MoyaIshkaApp)
        calls = []
        app.send_message = lambda: calls.append("sent")
        result = app._input_return(type("Event", (), {"state": 0})())
        self.assertEqual(result, "break")
        self.assertEqual(calls, ["sent"])

    def test_shift_enter_keeps_text_widget_newline(self):
        app = MoyaIshkaApp.__new__(MoyaIshkaApp)
        calls = []
        app.send_message = lambda: calls.append("sent")
        result = app._input_return(type("Event", (), {"state": 1})())
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_web_chat_returns_sources_without_api_key_or_ollama(self):
        app = MoyaIshkaApp.__new__(MoyaIshkaApp)
        app.config = {"research_results_limit": 3, "research_fetch_pages": 1}
        app.events = queue.Queue()
        app._call_ollama = lambda *_args: (_ for _ in ()).throw(ConnectionError("Ollama недоступна"))
        original_search = main_module.search_and_fetch
        main_module.search_and_fetch = lambda *_args, **_kwargs: [{
            "title": "Проверенная статья",
            "url": "https://example.com/article",
            "snippet": "Краткая выдержка из статьи",
            "content": "Подробный текст статьи для проверки веб-поиска без ключа.",
        }]
        try:
            app._web_chat_worker("тест", "Только онлайн", {"api_key": ""}, [
                {"role": "user", "content": "поищи тест"}
            ], "chat-1")
        finally:
            main_module.search_and_fetch = original_search
        events = []
        while not app.events.empty():
            events.append(app.events.get_nowait())
        reply = next(payload for event, payload in events if event == "reply")
        self.assertEqual(reply[0], "chat-1")
        self.assertIn("Проверенная статья", reply[1])
        self.assertIn("https://example.com/article", reply[1])
        self.assertEqual(reply[2], "интернет · без API-ключа")

    def test_microphone_is_off_by_default(self):
        self.assertFalse(DEFAULT_CONFIG["microphone_enabled"])

    def test_background_research_is_enabled_for_413_profile(self):
        self.assertTrue(DEFAULT_CONFIG["research_auto_enabled"])
        self.assertTrue(DEFAULT_CONFIG["web_search_enabled"])
        self.assertIn("Искусственный интеллект", DEFAULT_CONFIG["research_topics"])
        self.assertEqual(DEFAULT_CONFIG["background_ram_limit_mb"], 5120)
        self.assertEqual(DEFAULT_CONFIG["background_min_available_ram_mb"], 8192)
        self.assertEqual(DEFAULT_CONFIG["research_results_limit"], 10)
        self.assertEqual(DEFAULT_CONFIG["research_fetch_pages"], 5)
        self.assertEqual(DEFAULT_CONFIG["research_interval_hours"], 3)
        self.assertTrue(DEFAULT_CONFIG["cloud_auto_sync"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
