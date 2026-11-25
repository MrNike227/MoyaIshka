import unittest
from unittest import mock
from resource_governor import ResourceGovernor


class ResourceGovernorTests(unittest.TestCase):
    def test_default_profile_keeps_eight_gib_available(self):
        governor = ResourceGovernor()
        self.assertEqual(governor.min_available_ram_mb, 8192)

    def test_ram_budget_is_clamped_to_five_gib(self):
        governor = ResourceGovernor(max_ram_mb=9000)
        self.assertEqual(governor.max_ram_mb, 5120)
        governor.configure(max_ram_mb=2048, cpu_pause_percent=70)
        self.assertEqual(governor.max_ram_mb, 2048)
        self.assertEqual(governor.cpu_pause_percent, 70)

    def test_foreground_busy_does_not_mark_system_pressure(self):
        governor = ResourceGovernor()
        governor.set_foreground_busy(True)
        with mock.patch.object(governor, "_memory_usage_mb", return_value=(500.0, 12000.0)):
            try:
                cpu_mock = mock.patch("resource_governor.psutil.cpu_percent", return_value=5.0)
                cpu_mock.start()
            except Exception:
                cpu_mock = None
            try:
                snapshot = governor.snapshot()
            finally:
                if cpu_mock is not None:
                    cpu_mock.stop()
        self.assertTrue(snapshot.throttled)
        self.assertFalse(snapshot.system_pressure)

    def test_foreground_busy_flag_is_reflected_in_snapshot(self):
        governor = ResourceGovernor(max_ram_mb=5120)
        governor.set_foreground_busy(True)
        snap = governor.snapshot()
        self.assertTrue(snap.throttled)
        self.assertIn("пользовательский запрос", snap.reason)
        governor.set_foreground_busy(False)

    def test_monitor_can_start_and_stop(self):
        governor = ResourceGovernor(sample_interval=0.1, sleep_interval=0.25)
        governor.start_monitor(interval_seconds=0.75)
        self.assertIsNotNone(governor._monitor_thread)
        governor.stop()
        self.assertTrue(governor._stop.is_set())

    def test_priority_helper_is_safe(self):
        # On non-Windows test hosts the helper should be a no-op/low-nice operation
        # and never raise into the main application.
        ResourceGovernor.set_current_thread_low_priority()


if __name__ == "__main__":
    unittest.main(verbosity=2)
