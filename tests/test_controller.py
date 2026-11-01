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

    def run_scenario(self, scenario):
        lua = self.runtime(unpack_returned_tuples=True)
        lua.globals().firmware = (ROOT / self.firmware_file).read_text(encoding="utf-8")
        lua.globals().scenario = scenario
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

    def test_stop_signal_waits_before_replacement(self):
        result = self.run_scenario("stop_replace")
        self.assertIn("SIMULATION_DONE", result.err)
        self.assertGreater(len(result.transfers), 0)
        self.assertGreaterEqual(result.transfers[1].time - result.stopped, 1)

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
