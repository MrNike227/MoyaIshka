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

    def test_conversation_ids_reads_sqlite_rows_by_column(self):
        first_id = self.store.create_conversation("Первый")
        second_id = self.store.create_conversation("Второй")
        self.assertEqual(self.store.conversation_ids(), {first_id, second_id})

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
    def test_cloud_merge_uses_sqlite_safe_conversation_id_helper(self):
        source = Path(main_module.__file__).read_text(encoding="utf-8")
        self.assertIn("self.store.conversation_ids()", source)
        self.assertNotIn('str(c.get("id")) for c in self.store.list_conversations()', source)

    def test_extract_openai_content(self):
        self.assertEqual(extract_openai_content({"choices": [{"message": {"content": " Готово "}}]}), "Готово")

    def test_read_text_attachment(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "note.txt"
            path.write_text("Тестовый текст", encoding="utf-8")
            self.assertEqual(read_attachment(path), "Тестовый текст")

    def test_chat_improvement_ideas_are_connected_to_send_and_analysis(self):
        source = Path(main_module.__file__).read_text(encoding="utf-8")
        self.assertIn("save_chat_improvement_idea(", source)
        self.assertIn("load_chat_improvement_ideas(", source)
        self.assertIn("идея сохранена для самоанализа", source)

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



class SelfImprovementModelSelectionTests(unittest.TestCase):
    def _app(self):
        app = MoyaIshkaApp.__new__(MoyaIshkaApp)
        app.config = {
            "mode": "Только онлайн",
            "online_url": "https://api.example.test/v1/chat/completions",
            "online_model": "cloud-model",
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "local-model",
        }
        app.api_key = ""
        pressure = type("Pressure", (), {"system_pressure": False})()
        app.resource_governor = type(
            "Governor", (), {"snapshot": lambda _self: pressure}
        )()
        return app

    def test_self_analysis_uses_local_ollama_without_api_key_in_online_chat_mode(self):
        app = self._app()
        captured = {}

        def local_call(settings, messages):
            captured["settings"] = settings
            captured["messages"] = messages
            return "local self-analysis result"

        app._call_online = lambda *_args: self.fail("Online API must not be called without a key")
        app._call_ollama = local_call

        result = app._current_model_call([{"role": "user", "content": "Проверь код"}])

        self.assertEqual(result, "local self-analysis result")
        self.assertEqual(captured["settings"]["task_profile"], "self_improvement")
        self.assertEqual(captured["settings"]["api_key"], "")
        self.assertEqual(captured["settings"]["ollama_model"], "local-model")

    def test_self_analysis_keeps_online_model_when_api_key_is_configured(self):
        app = self._app()
        app.api_key = "test-api-key"
        app._call_online = lambda _settings, _messages: "online self-analysis result"
        app._call_ollama = lambda *_args: self.fail("Local model must not replace selected online model")

        result = app._current_model_call([{"role": "user", "content": "Проверь код"}])

        self.assertEqual(result, "online self-analysis result")


class OllamaSelfImprovementRecoveryTests(unittest.TestCase):
    def test_self_improvement_retries_on_cpu_after_llama_server_access_violation(self):
        from unittest.mock import patch
        import urllib.error
        import io

        calls = []

        def fake_post(_url, payload, timeout=None):
            calls.append(payload)
            if len(calls) == 1:
                body = b'{"error":"llama-server process has terminated: exit status 0xc0000005"}'
                raise urllib.error.HTTPError(
                    "http://127.0.0.1:11434/api/chat", 500, "Internal Server Error",
                    {}, io.BytesIO(body),
                )
            return {"message": {"content": "Проверка завершена"}, "done_reason": "stop"}

        settings = {
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "local-model",
            "task_profile": "self_improvement",
        }
        with patch.object(main_module, "post_json", side_effect=fake_post):
            result = MoyaIshkaApp._call_ollama(
                settings, [{"role": "user", "content": "Проверь код"}]
            )

        self.assertEqual(result, "Проверка завершена")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["options"]["num_ctx"], 4096)
        self.assertEqual(calls[0]["options"]["num_batch"], 64)
        self.assertNotIn("num_gpu", calls[0]["options"])
        self.assertEqual(calls[1]["options"]["num_gpu"], 0)
        self.assertEqual(calls[1]["options"]["num_batch"], 32)

    def test_ordinary_chat_does_not_silently_switch_to_cpu_after_access_violation(self):
        from unittest.mock import patch
        import urllib.error
        import io

        body = b'{"error":"llama-server process has terminated: exit status 0xc0000005"}'
        failure = urllib.error.HTTPError(
            "http://127.0.0.1:11434/api/chat", 500, "Internal Server Error",
            {}, io.BytesIO(body),
        )
        settings = {
            "ollama_url": "http://127.0.0.1:11434",
            "ollama_model": "local-model",
            "task_profile": "chat",
        }
        with patch.object(main_module, "post_json", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                MoyaIshkaApp._call_ollama(
                    settings, [{"role": "user", "content": "Привет"}]
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
