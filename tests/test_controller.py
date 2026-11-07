"""Run with: python -m unittest discover -s tests -v (requires lupa)."""
from pathlib import Path
import unittest

from lupa.lua52 import LuaRuntime
from lupa.lua53 import LuaRuntime as Lua53
from lupa.lua54 import LuaRuntime as Lua54

ROOT = Path(__file__).resolve().parents[1]
HARNESS = (ROOT / "tests/controller.lua").read_text(encoding="utf-8")


class ControllerTests(unittest.TestCase):
    runtime = LuaRuntime
    firmware_file = "nuclear_SourceCode.lua"

    def run_scenario(self, scenario, recovery_attempt=3):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.globals().firmware = (ROOT / self.firmware_file).read_text(encoding="utf-8")
        lua.globals().scenario = scenario
        lua.globals().recovery_attempt = recovery_attempt
        return lua.execute(HARNESS)

    def test_me_ignores_other_container_fuels(self):
        result = self.run_scenario("me_priority")
        self.assertIn("SIMULATION_DONE", result.err)

    def test_me_does_not_inherit_hybrid_mode(self):
        result = self.run_scenario("me_stale_hybrid")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertEqual(len(result.transfers), 0)

    def test_third_fuel_is_rejected(self):
        self.assertIn("不支持的混合燃料棒", self.run_scenario("third_fuel").err)

    def test_supported_hybrid_is_accepted(self):
        result = self.run_scenario("hybrid")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertEqual(len(result.transfers), 0)

    def writes(self, result):
        return [result.writes[i] for i in range(1, len(result.writes) + 1)]

    def assert_delayed_shutdown(self, result, removed_at):
        shutdowns = [write for write in self.writes(result)
                     if write.side is None and write.value == 0 and write.time > 0]
        self.assertEqual(len(shutdowns), 1)
        self.assertGreaterEqual(shutdowns[0].time - removed_at, 10 - 1e-9)
        self.assertLessEqual(shutdowns[0].time - removed_at, 10.05 + 1e-9)
        self.assertFalse(any(write.value > 0 and write.time >= shutdowns[0].time
                             for write in self.writes(result)))

    def test_redstone_removal_delays_shutdown_ten_seconds(self):
        result = self.run_scenario("delay_stop")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertTrue(any(write.value == 1 for write in self.writes(result)))
        self.assertEqual([write.value for write in self.writes(result) if write.side == 0], [1])
        self.assert_delayed_shutdown(result, result.inputEvents[1].time)

    def test_repeated_redstone_events_do_not_extend_shutdown(self):
        result = self.run_scenario("delay_noise")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertEqual(len(result.inputEvents), 4)
        self.assert_delayed_shutdown(result, result.inputEvents[1].time)

    def test_signal_restoration_cancels_shutdown_and_next_removal_restarts_timer(self):
        result = self.run_scenario("delay_resume")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertEqual(len(result.inputEvents), 3)
        self.assertEqual([write.value for write in self.writes(result) if write.side == 0], [1])
        self.assert_delayed_shutdown(result, result.inputEvents[3].time)

    def test_inactive_redstone_events_do_not_start_reactors_or_schedule_shutdown(self):
        result = self.run_scenario("delay_idle")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertFalse(any(write.value > 0 for write in self.writes(result)))

    def test_shutdown_turns_off_all_reactors_together(self):
        result = self.run_scenario("delay_multiple")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assert_delayed_shutdown(result, result.inputEvents[1].time)
        removed_at = result.inputEvents[1].time
        active = {}
        for write in self.writes(result):
            if write.time < removed_at and write.side is not None:
                active[write.side] = write.value
        self.assertEqual(active.get(3), 1)
        self.assertEqual(active.get(4), 1)

    def test_replacement_still_stops_immediately_during_shutdown_countdown(self):
        result = self.run_scenario("stop_replace")
        self.assertIn("SIMULATION_DONE", result.err)
        removed_at = result.inputEvents[1].time
        replacement_stop = next(write for write in self.writes(result)
                                if write.side == 0 and write.value == 0)
        self.assertLess(replacement_stop.time - removed_at, 1)
        self.assertGreaterEqual(result.transfers[1].time - replacement_stop.time, 1)
        self.assertTrue(any(write.side == 0 and write.value == 1
                            and replacement_stop.time < write.time < removed_at + 10
                            for write in self.writes(result)))
        self.assert_delayed_shutdown(result, removed_at)

    def test_failed_direction_probe_is_reported(self):
        self.assertIn("无法识别方向", self.run_scenario("mapping_failure").err)

    def test_horizontal_reactor_can_start(self):
        self.assertIn("SIMULATION_DONE", self.run_scenario("mapping_success").err)

    def test_multiple_horizontal_reactors_can_start(self):
        result = self.run_scenario("mapping_multiple")
        self.assertIn("SIMULATION_DONE", result.err)
        started = {result.writes[i].side for i in range(1, len(result.writes) + 1)
                   if result.writes[i].value == 1}
        self.assertTrue({3, 4}.issubset(started))

    def assert_retry_intervals(self, times):
        for earlier, later in zip(times, times[1:]):
            self.assertGreaterEqual(later - earlier, 1 - 1e-9)

    def assert_never_started(self, result):
        self.assertFalse(any(write.outputs[0] > 0 for write in self.writes(result)))

    def test_fault_activates_alarm_after_turning_off_reactor(self):
        for scenario, message in [("output_full", "输出空间不足"),
                                  ("coolant_missing", "缺少冷却单元"),
                                  ("me_output_full", "输出空间不足"),
                                  ("alarm_small_reactor", "必须为六仓反应堆"),
                                  ("alarm_startup", "不支持的混合燃料棒")]:
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn(message, result.err)
                self.assertEqual(result.outputs[0], 0)
                self.assertEqual(result.outputs[1], 15)
                writes = self.writes(result)
                first_alarm = next(i for i, write in enumerate(writes) if write.value == 15)
                self.assertEqual(writes[first_alarm - 1].value, 0)

    def test_fault_holds_alarm_and_keeps_reactors_off_for_thirty_seconds(self):
        result = self.run_scenario("alarm_running")
        self.assertTrue(result.running)
        self.assertIsNotNone(result.faultTime)
        self.assertGreaterEqual(result.time - result.faultTime, 30)
        alarms = [write for write in self.writes(result) if write.value == 15]
        self.assertGreaterEqual(len(alarms), 30)
        self.assertLessEqual(alarms[0].time - result.faultTime, 0.05)
        for previous, current in zip(alarms, alarms[1:]):
            self.assertAlmostEqual(current.time - previous.time, 1)
        writes = self.writes(result)
        first_alarm = next(i for i, write in enumerate(writes) if write.value == 15)
        for write in writes[first_alarm:]:
            self.assertEqual(write.outputs[1], 15)
            self.assertEqual(write.outputs[0], 0)
            self.assertEqual(write.outputs[4], 0)

    def test_fault_latch_ignores_recovered_supplies_and_control_signals(self):
        result = self.run_scenario("alarm_latched_recover")
        self.assertTrue(result.running)
        self.assertIn("不支持的混合燃料棒", result.err)
        self.assertEqual([result.inputEvents[i].value for i in range(1, len(result.inputEvents) + 1)], [0, 1])
        self.assertEqual(result.outputs[1], 15)
        self.assertEqual(result.outputs[0], 0)
        self.assertEqual(len(result.transfers), 0)

    def test_fault_alarm_recovers_from_temporary_redstone_failure(self):
        result = self.run_scenario("alarm_write_recover_running")
        self.assertTrue(result.running)
        self.assertIn("BROKEN_TRANSPOSER", result.err)
        self.assertEqual(result.outputs[0], 0)
        self.assertEqual(result.outputs[1], 15)

    def test_fault_signal_noise_does_not_speed_up_output_refreshes(self):
        result = self.run_scenario("alarm_event_noise")
        self.assertTrue(result.running)
        alarms = [write for write in self.writes(result) if write.value == 15]
        self.assertGreaterEqual(len(alarms), 30)
        self.assertLessEqual(len(alarms), 31)
        for previous, current in zip(alarms, alarms[1:]):
            self.assertAlmostEqual(current.time - previous.time, 1)

    def test_runtime_component_error_stops_all_reactors_and_alarms(self):
        for scenario, reactor_sides in [("alarm_running", [0, 4]),
                                        ("alarm_horizontal_running", [3, 4]),
                                        ("alarm_rotated_running", [5, 3])]:
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn("BROKEN_TRANSPOSER", result.err)
                self.assertTrue(any(all(write.outputs[side] == 1 for side in reactor_sides)
                                    for write in self.writes(result)))
                self.assertEqual(result.outputs[1], 15)
                for side in reactor_sides:
                    self.assertEqual(result.outputs[side], 0)

    def test_failed_probe_only_alarms_in_safe_vertical_directions(self):
        result = self.run_scenario("alarm_horizontal_probe")
        self.assertIn("无法识别方向", result.err)
        self.assertEqual(result.outputs[1], 15)
        for side in range(2, 6):
            self.assertEqual(result.outputs[side], 0)

    def test_alarm_failure_preserves_original_error(self):
        result = self.run_scenario("alarm_write_failure_running")
        self.assertTrue(result.running)
        self.assertIn("BROKEN_TRANSPOSER", result.err)
        self.assertNotIn("BROKEN_REDSTONE", result.err)
        self.assertNotIn("error in error handling", result.err)

    def test_restart_clears_alarm_output(self):
        result = self.run_scenario("alarm_reset")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertTrue(all(result.outputs[side] == 0 for side in range(6)))

    def test_normal_stops_and_successful_retries_do_not_alarm(self):
        for scenario in ["delay_stop", "stop_replace", "output_recover",
                         "coolant_recover", "me_output_recover"]:
            with self.subTest(scenario=scenario):
                result = self.run_scenario(scenario)
                self.assertIn("SIMULATION_DONE", result.err)
                self.assertFalse(any(write.value == 15 for write in self.writes(result)))

    def test_full_output_stops_after_three_retries(self):
        result = self.run_scenario("output_full")
        self.assertIn("输出空间不足", result.err)
        attempts = [result.transfers[i] for i in range(1, len(result.transfers) + 1)]
        self.assertEqual(len(attempts), 4)  # initial attempt, followed by three retries
        self.assertTrue(all(attempt["from"] == 0 and not attempt.moved for attempt in attempts))
        self.assert_retry_intervals([attempt.time for attempt in attempts])
        self.assert_never_started(result)

    def test_output_can_recover_on_each_retry(self):
        for retry in range(1, 4):
            with self.subTest(retry=retry):
                result = self.run_scenario("output_recover", retry)
                self.assertIn("SIMULATION_DONE", result.err)
                outgoing = [result.transfers[i] for i in range(1, len(result.transfers) + 1)
                            if result.transfers[i]["from"] == 0]
                self.assertEqual(len(outgoing), retry + 1)
                self.assertEqual(outgoing[-1].moved, 1)
                self.assert_retry_intervals([attempt.time for attempt in outgoing])
                self.assertTrue(any(result.writes[i].value == 1 for i in range(1, len(result.writes) + 1)))

    def test_coolant_shortage_stops_after_three_retries(self):
        result = self.run_scenario("coolant_missing")
        self.assertIn("缺少冷却单元", result.err)
        checks = [result.supplyReads[i] for i in range(1, len(result.supplyReads) + 1)
                  if result.supplyReads[i].time >= result.shortageSince]
        self.assertEqual(len(checks), 4)
        self.assertTrue(all(not check.available for check in checks))
        self.assert_retry_intervals([check.time for check in checks])
        self.assert_never_started(result)

    def test_coolant_can_recover_on_each_retry(self):
        for retry in range(1, 4):
            with self.subTest(retry=retry):
                result = self.run_scenario("coolant_recover", retry)
                self.assertIn("SIMULATION_DONE", result.err)
                incoming = [result.transfers[i] for i in range(1, len(result.transfers) + 1)
                            if result.transfers[i].to == 0 and result.transfers[i].moved]
                self.assertEqual(len(incoming), 1)
                self.assertGreaterEqual(incoming[0].time - result.shortageSince, retry)
                self.assertTrue(any(result.writes[i].value == 1 for i in range(1, len(result.writes) + 1)))

    def test_full_me_interface_stops_after_three_retries(self):
        result = self.run_scenario("me_output_full")
        self.assertIn("输出空间不足", result.err)
        checks = [result.supplyReads[i].time for i in range(1, len(result.supplyReads) + 1)
                  if result.supplyReads[i].time > 1]
        self.assertEqual(len(checks), 3)
        self.assert_retry_intervals(checks)
        self.assertEqual(len(result.transfers), 0)
        self.assert_never_started(result)

    def test_me_interface_recovers_on_third_retry(self):
        result = self.run_scenario("me_output_recover")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertEqual(len(result.transfers), 2)
        self.assertGreaterEqual(result.transfers[1].time, 4.05)
        self.assertTrue(all(result.transfers[i].moved for i in range(1, 3)))


class SourceLua53Tests(ControllerTests):
    runtime = Lua53


class SourceLua54Tests(ControllerTests):
    runtime = Lua54


class EEPROMLua52Tests(ControllerTests):
    firmware_file = "nuclear.lua"


class EEPROMLua53Tests(EEPROMLua52Tests):
    runtime = Lua53


class EEPROMLua54Tests(EEPROMLua52Tests):
    runtime = Lua54


if __name__ == "__main__":
    unittest.main()
