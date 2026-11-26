import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from intelligent_memory import (
    add_or_update_memory,
    contains_sensitive_data,
    edit_memory_entry,
    merge_memory_data,
    normalize_memory_metadata,
    select_memory_records,
)


class IntelligentMemoryTests(unittest.TestCase):
    def test_legacy_strings_are_migrated_without_changing_content(self):
        facts = ["Пользователь предпочитает короткие ответы.", "Проект называется MoyaIshka."]
        migrated, metadata = normalize_memory_metadata(facts, [])
        self.assertEqual(migrated, facts)
        self.assertEqual(len(metadata), 2)
        self.assertEqual(metadata[0]["category"], "Предпочтения")
        self.assertEqual(metadata[1]["category"], "Проекты")
        self.assertTrue(all(item["status"] == "active" for item in metadata))

    def test_duplicate_is_updated_instead_of_added(self):
        facts, metadata, action = add_or_update_memory([], [], "Я предпочитаю короткие ответы.")
        self.assertEqual(action, "added")
        facts2, metadata2, action2 = add_or_update_memory(facts, metadata, "Я предпочитаю короткие ответы.")
        self.assertEqual(action2, "duplicate")
        self.assertEqual(facts2, facts)
        self.assertEqual(len(metadata2), 1)

    def test_explicit_contradiction_supersedes_previous_preference(self):
        facts, metadata, _ = add_or_update_memory([], [], "Я предпочитаю короткие ответы.")
        facts, metadata, action = add_or_update_memory(facts, metadata, "Я предпочитаю подробные ответы.")
        self.assertEqual(action, "superseded")
        self.assertEqual(len(facts), 2)
        self.assertEqual(metadata[0]["status"], "superseded")
        self.assertEqual(metadata[1]["status"], "active")

    def test_edit_preserves_created_at_and_reclassifies(self):
        facts, metadata, _ = add_or_update_memory([], [], "Мой проект называется MoyaIshka.")
        created = metadata[0]["created_at"]
        facts, metadata, action = edit_memory_entry(facts, metadata, 0, "Я предпочитаю короткие ответы.")
        self.assertEqual(action, "updated")
        self.assertEqual(metadata[0]["created_at"], created)
        self.assertEqual(metadata[0]["category"], "Предпочтения")

    def test_explicit_temporary_memory_gets_expiry(self):
        _, metadata, _ = add_or_update_memory([], [], "Завтра нужно проверить сборку.")
        self.assertTrue(metadata[0]["expires_at"])
        self.assertEqual(metadata[0]["category"], "Временные задачи")

    def test_sensitive_data_is_not_stored(self):
        facts, metadata, action = add_or_update_memory([], [], "Мой пароль: password123")
        self.assertEqual(action, "blocked")
        self.assertEqual(facts, [])
        self.assertEqual(metadata, [])
        self.assertTrue(contains_sensitive_data("api_key=abc123"))

    def test_synonym_search_finds_related_preference_without_embeddings(self):
        facts = ["Пользователь предпочитает короткие ответы."]
        selected = select_memory_records(
            facts, [], "Отвечай лаконично",
            semantic_enabled=False,
        )
        self.assertEqual(selected[0]["text"], facts[0])

    def test_expired_and_superseded_records_are_not_retrieved(self):
        facts, metadata, _ = add_or_update_memory([], [], "Я предпочитаю короткие ответы.")
        facts, metadata, _ = add_or_update_memory(facts, metadata, "Я предпочитаю подробные ответы.")
        selected = select_memory_records(facts, metadata, "какой стиль ответа", semantic_enabled=False)
        self.assertEqual([item["text"] for item in selected], [facts[1]])

    def test_cloud_merge_keeps_metadata_attached_to_memory(self):
        local = ["Мой проект называется MoyaIshka."]
        local_meta = normalize_memory_metadata(local, [])[1]
        remote = ["Я предпочитаю короткие ответы."]
        remote_meta = normalize_memory_metadata(remote, [])[1]
        merged, metadata = merge_memory_data(local, local_meta, remote, remote_meta)
        self.assertEqual(merged, local + remote)
        self.assertEqual(len(metadata), 2)
        self.assertEqual(metadata[0]["category"], "Проекты")
        self.assertEqual(metadata[1]["category"], "Предпочтения")

    def test_semantic_vectors_can_find_memory_without_shared_keywords(self):
        memories = ["Основная операционная система компьютера — Windows 11.", "Пользователь любит читать научную фантастику."]
        query_vector = [1.0, 0.0]
        memory_vectors = [[0.98, 0.2], [0.0, 1.0]]
        with tempfile.TemporaryDirectory() as temp:
            def fake_embed(_url, _model, texts, timeout=10.0):
                return [query_vector, *memory_vectors]
            with patch("intelligent_memory._embed_texts", side_effect=fake_embed):
                selected = select_memory_records(
                    memories, [], "Какая у меня операционная система?",
                    semantic_enabled=True, ollama_url="http://localhost:11434",
                    cache_path=Path(temp) / "vectors.json",
                )
        self.assertTrue(selected)
        self.assertIn("Windows 11", selected[0]["text"])

    def test_non_loopback_embedding_server_is_never_used(self):
        with patch("intelligent_memory.urllib.request.urlopen") as opener:
            from intelligent_memory import _embed_texts
            self.assertIsNone(_embed_texts("https://example.com", "nomic-embed-text", ["тест"]))
            opener.assert_not_called()

    def test_legacy_sensitive_memory_is_never_selected(self):
        selected = select_memory_records(
            ["Секретный API key: abc123", "Пользователь предпочитает короткие ответы."],
            [], "помни API key короткие ответы", semantic_enabled=False,
        )
        self.assertEqual([item["text"] for item in selected], ["Пользователь предпочитает короткие ответы."])

    def test_expired_memory_is_not_selected(self):
        facts = ["Завтра нужно проверить сборку."]
        metadata = [{"expires_at": "2000-01-01T00:00:00+00:00", "status": "active"}]
        selected = select_memory_records(facts, metadata, "проверить сборку", semantic_enabled=False)
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
