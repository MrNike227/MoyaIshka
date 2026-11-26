import unittest

from context_engine import select_relevant_memories, trim_conversation_context


class ContextEngineTests(unittest.TestCase):
    def test_relevant_memory_is_selected_using_russian_word_forms(self):
        memories = [
            "Пользователь использует Windows 10 Pro 22H2.",
            "В проекте MoyaIshka используется Python и PyInstaller.",
            "Пользователь предпочитает короткие ответы.",
        ]
        self.assertEqual(
            select_relevant_memories(memories, "ошибки программы на Windows"),
            [memories[0]],
        )

    def test_irrelevant_memory_is_not_sent_when_query_matches_another_fact(self):
        memories = [
            "Используется Windows 10 Pro 22H2.",
            "Нужно отвечать коротко.",
            "Проект собирается через PyInstaller onedir.",
        ]
        self.assertEqual(
            select_relevant_memories(memories, "настройка PyInstaller"),
            [memories[2]],
        )

    def test_memory_is_deduplicated_and_bounded(self):
        result = select_relevant_memories(
            ["Python", "Python", "x" * 1000, "Windows"], "Python",
            max_items=2, max_chars=50
        )
        self.assertLessEqual(len(result), 2)
        self.assertLessEqual(sum(map(len, result)), 50)
        self.assertEqual(len(result), len(set(result)))

    def test_no_query_uses_recent_memory_not_everything(self):
        memories = [f"Факт {index}" for index in range(20)]
        self.assertEqual(select_relevant_memories(memories, "", max_items=3), memories[-3:])

    def test_context_keeps_system_and_latest_user_under_budget(self):
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "old " * 100},
            {"role": "assistant", "content": "old reply " * 100},
            {"role": "user", "content": "latest question"},
            {"role": "assistant", "content": "latest answer"},
            {"role": "user", "content": "last prompt"},
        ]
        result = trim_conversation_context(messages, max_chars=50, max_messages=5)
        self.assertEqual(result[0]["role"], "system")
        self.assertEqual(result[-1]["content"], "last prompt")
        self.assertLessEqual(sum(len(item["content"]) for item in result), 50)

    def test_oversized_latest_user_is_shortened_not_dropped(self):
        result = trim_conversation_context(
            [{"role": "system", "content": "rules"}, {"role": "user", "content": "A" * 5000}],
            max_chars=1000, max_messages=3
        )
        self.assertEqual(result[-1]["role"], "user")
        self.assertLessEqual(sum(len(item["content"]) for item in result), 1000)


if __name__ == "__main__":
    unittest.main()
