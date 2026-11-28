# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import main
import backup_interface
import chat_interface
import cloud_interface
import memory_interface
import plugin_interface
import research_interface
import self_improvement_interface
import settings_interface
import update_interface
import voice_interface
import ui_components

class InterfaceModuleTests(unittest.TestCase):
    def test_main_app_inherits_all_extracted_feature_mixins(self):
        mixins = (backup_interface.BackupInterfaceMixin, chat_interface.ChatInterfaceMixin, cloud_interface.CloudInterfaceMixin, memory_interface.MemoryInterfaceMixin, plugin_interface.PluginInterfaceMixin, research_interface.ResearchInterfaceMixin, self_improvement_interface.SelfImprovementInterfaceMixin, settings_interface.SettingsInterfaceMixin, update_interface.UpdateInterfaceMixin, voice_interface.VoiceInterfaceMixin)
        for mixin in mixins:
            with self.subTest(mixin=mixin.__name__): self.assertTrue(issubclass(main.ISHApp, mixin))
    def test_feature_methods_are_owned_by_their_extracted_module(self):
        self.assertIn("_cloud_sync_worker", cloud_interface.CloudInterfaceMixin.__dict__)
        self.assertIn("_restore_backup_worker", backup_interface.BackupInterfaceMixin.__dict__)
        self.assertIn("_reply_worker", chat_interface.ChatInterfaceMixin.__dict__)
        self.assertIn("_research_worker", research_interface.ResearchInterfaceMixin.__dict__)
        self.assertIn("analyze_improvements", self_improvement_interface.SelfImprovementInterfaceMixin.__dict__)
        self.assertIn("check_for_updates", update_interface.UpdateInterfaceMixin.__dict__)
        self.assertIn("start_voice_input", voice_interface.VoiceInterfaceMixin.__dict__)
        self.assertIn("add_memory", memory_interface.MemoryInterfaceMixin.__dict__)
        self.assertIn("create_skill", plugin_interface.PluginInterfaceMixin.__dict__)
        self.assertIn("save_settings", settings_interface.SettingsInterfaceMixin.__dict__)
    def test_runtime_globals_are_bound(self):
        self.assertIs(cloud_interface.APP_VERSION, main.APP_VERSION)
        self.assertIs(backup_interface.DEFAULT_CONFIG, main.DEFAULT_CONFIG)
        self.assertIs(research_interface.search_and_fetch, main.search_and_fetch)
        self.assertIs(self_improvement_interface.project_dir, main.project_dir)
    def test_custom_checkbox_is_reexported_from_main(self):
        self.assertIs(main.PurpleCheckbox, ui_components.PurpleCheckbox)
if __name__ == '__main__': unittest.main(verbosity=2)
