"""No real displays are changed: protocol fixtures and controlled watchdogs."""

import copy
import json
import threading
import unittest
from unittest.mock import patch

from src.display_actions import DisplayMode, DisplayService


def which(name):
    return "/usr/bin/" + name


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Timer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.daemon = True
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Deliberately invoke even a cancelled callback to test a timer that
        # already reached the service lock before confirm/close cancelled it.
        self.callback()


class Timers:
    def __init__(self):
        self.items = []

    def __call__(self, delay, callback):
        timer = Timer(delay, callback)
        self.items.append(timer)
        return timer


def sway_outputs():
    modes = [
        {"width": 1920, "height": 1080, "refresh": 60000},
        {"width": 1280, "height": 720, "refresh": 59940},
        {"width": 1024, "height": 768, "refresh": 60000},
    ]
    return [
        {"name": "eDP-1", "active": True, "scale": 1.0, "transform": "normal", "power": True,
         "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
         "modes": modes, "current_mode": modes[0], "current_workspace": "1"},
        {"name": "HDMI-A-1", "active": True, "scale": 1.5, "transform": "90", "power": False,
         "rect": {"x": 1920, "y": -100, "width": 720, "height": 1280},
         "modes": modes, "current_mode": modes[0], "current_workspace": "2"},
        {"name": "DP-2", "active": False, "scale": -1, "transform": "normal", "power": False,
         "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
         "modes": modes, "current_mode": None, "current_workspace": None},
    ]


class Sway:
    def __init__(self):
        self.outputs = copy.deepcopy(sway_outputs())
        self.calls = []
        self.after_discovery = None
        self.after_apply = None
        self.fail_apply = False
        self.fail_restore = False
        self.ignore_apply = False
        self.invalid_reply = False

    @property
    def commands(self):
        return [argv[-1] for argv in self.calls if argv[3] == "command"]

    def __call__(self, argv, timeout=8):
        self.calls.append(list(argv))
        if argv == ["swaymsg", "-r", "-t", "get_outputs"]:
            result = json.dumps(self.outputs)
            if self.after_discovery:
                self.after_discovery()
            return True, result
        assert argv[:4] == ["swaymsg", "-r", "-t", "command"]
        fields = argv[-1].split()
        output = next(item for item in self.outputs if item["name"] == fields[1])
        restore = "enable" in fields or "disable" in fields
        if restore and self.fail_restore:
            return True, '[{"success": false, "error": "restore failed"}]'
        if "mode" in fields and not (self.ignore_apply and not restore):
            identifier = fields[fields.index("mode") + 1]
            dimensions, rate = identifier[:-2].split("@")
            width, height = dimensions.split("x")
            output["current_mode"] = {"width": int(width), "height": int(height), "refresh": round(float(rate) * 1000)}
        if restore:
            if "disable" in fields:
                output["active"] = False
            else:
                output["active"] = True
                position = fields.index("pos")
                output["rect"]["x"] = int(fields[position + 1])
                output["rect"]["y"] = int(fields[position + 2])
                output["scale"] = float(fields[fields.index("scale") + 1])
                output["transform"] = fields[fields.index("transform") + 1]
                output["power"] = fields[fields.index("power") + 1] == "on"
        elif self.after_apply:
            self.after_apply()
        if self.invalid_reply:
            return True, "not JSON"
        if not restore and self.fail_apply:
            return True, '[{"success": false, "error": "mode failed"}]'
        return True, '[{"success": true}]'


X_QUERY = """Screen 0: minimum 8 x 8, current 3280 x 1080, maximum 32767 x 32767
eDP-1 connected primary 1920x1080+0+0 (normal left inverted right x axis y axis) 344mm x 194mm
   1920x1080     60.00*+  59.94
   1280x720      60.00
HDMI-1 connected 900x1600+1920+-100 left X axis (normal left inverted right x axis y axis) 480mm x 270mm
   1600x900      60.00*+
DP-2 connected (normal left inverted right x axis y axis)
   1920x1080     60.00 +
VGA-1 disconnected (normal left inverted right x axis y axis)
"""

X_VERBOSE = """Screen 0: minimum 8 x 8, current 3280 x 1080, maximum 32767 x 32767
eDP-1 connected primary 1920x1080+0+0 (0x47) normal (normal left inverted right x axis y axis) 344mm x 194mm
    CRTC: 0
    Transform: 1.000000 0.000000 0.000000
               0.000000 1.000000 0.000000
               0.000000 0.000000 1.000000
              filter:
  1920x1080 (0x47) 148.500MHz +HSync +VSync *current +preferred
        h: width  1920 start 2008 end 2052 total 2200 skew 0 clock 67.50KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 60.00Hz
  1280x720 (0x48) 74.250MHz +HSync +VSync
        h: width  1280 start 1390 end 1430 total 1650 skew 0 clock 45.00KHz
        v: height 720 start 725 end 730 total 750 clock 60.00Hz
  1920x1080 (0x4b) 148.352MHz +HSync +VSync
        h: width 1920 start 2008 end 2052 total 2200 skew 0 clock 67.43KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 59.94Hz
HDMI-1 connected 900x1600+1920+-100 (0x49) left X axis (normal left inverted right x axis y axis) 480mm x 270mm
    CRTC: 1
    Transform: 1.250000 0.000000 0.000000
               0.000000 1.250000 0.000000
               0.000000 0.000000 1.000000
              filter: bilinear
    Panning: 1125x2000+1920+0
    Tracking: 1125x2000+1920+0
    Border: 0/0/0/0
  1600x900 (0x49) 108.000MHz +HSync +VSync *current +preferred
        h: width  1600 start 1624 end 1704 total 1800 skew 0 clock 60.00KHz
        v: height 900 start 901 end 904 total 1000 clock 60.00Hz
DP-2 connected (normal left inverted right x axis y axis)
  1920x1080 (0x4a) 148.500MHz +HSync +VSync +preferred
        h: width 1920 start 2008 end 2052 total 2200 skew 0 clock 67.50KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 60.00Hz
VGA-1 disconnected (normal left inverted right x axis y axis)
"""


class XRandR:
    def __init__(self):
        self.calls = []
        self.changed = False
        self.ignore_apply = False
        self.verbose = X_VERBOSE

    def __call__(self, argv, timeout=8):
        self.calls.append(list(argv))
        if argv == ["xrandr", "--query"]:
            query = X_QUERY
            if self.changed:
                query = query.replace("eDP-1 connected primary 1920x1080", "eDP-1 connected primary 1280x720")
                query = query.replace("60.00*+  59.94", "60.00+  59.94").replace("1280x720      60.00", "1280x720      60.00*")
            return True, query
        if argv == ["xrandr", "--verbose"]:
            verbose = self.verbose
            if self.changed:
                verbose = verbose.replace("eDP-1 connected primary 1920x1080", "eDP-1 connected primary 1280x720")
                verbose = verbose.replace("+VSync *current +preferred", "+VSync +preferred", 1)
                verbose = verbose.replace("1280x720 (0x48) 74.250MHz +HSync +VSync", "1280x720 (0x48) 74.250MHz +HSync +VSync *current")
            return True, verbose
        if "--fb" in argv:
            self.changed = False
        elif not self.ignore_apply:
            self.changed = True
        return True, ""


class TestDisplayMode(unittest.TestCase):
    def test_validated_serialization_and_defaults(self):
        mode = DisplayMode("eDP-1", 1920, 1080, 59.94, "1920x1080@59.94Hz")
        self.assertEqual(DisplayMode.from_dict(mode.to_dict()), mode)
        self.assertEqual(mode.scale, 1)
        self.assertFalse(mode.current)
        value = mode.to_dict()
        value.pop("scale")
        value.pop("current")
        self.assertEqual(DisplayMode.from_dict(value), mode)

    def test_rejects_injection_nonfinite_values_and_bool_numbers(self):
        valid = DisplayMode("eDP-1", 1920, 1080, 60, "1920x1080").to_dict()
        for key, value in [
            ("output", "*"), ("output", "eDP-1; exec touch /tmp/bad"),
            ("identifier", "--auto"), ("identifier", "1920x1080\nexec x"),
            ("width", True), ("height", 0), ("width", 40000),
            ("refresh", float("nan")), ("refresh", float("inf")), ("refresh", 10 ** 1000),
            ("scale", 0), ("current", 1),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                DisplayMode.from_dict(dict(valid, **{key: value}))
        for record in (None, {}, dict(valid, command="xrandr --auto")):
            with self.assertRaises(ValueError):
                DisplayMode.from_dict(record)


class TestDisplayService(unittest.TestCase):
    def setUp(self):
        self.runner = Sway()
        self.timers = Timers()
        self.clock = Clock()
        self.service = DisplayService(
            self.runner, which, {"XDG_SESSION_TYPE": "wayland", "SWAYSOCK": "/tmp/sway-test.sock", "DISPLAY": ":0"},
            timer_factory=self.timers, clock=self.clock,
        )
        self.addCleanup(self.service.close)

    def mode(self, width=1280, output="eDP-1"):
        modes, error = self.service.list_modes()
        self.assertEqual(error, "")
        return next(mode for mode in modes if mode.width == width and mode.output == output)

    def test_native_sway_modes_keep_scale_current_and_millihertz(self):
        modes, error = self.service.list_modes()
        self.assertEqual(error, "")
        hdmi = next(mode for mode in modes if mode.output == "HDMI-A-1" and mode.current)
        self.assertEqual(hdmi.scale, 1.5)
        self.assertEqual(self.mode().refresh, 59.94)
        self.assertEqual(self.mode().identifier, "1280x720@59.94Hz")
        self.assertTrue(all(call[0] == "swaymsg" for call in self.runner.calls))

    def test_apply_confirm_verifies_and_cancelled_watchdog_cannot_revert(self):
        selected = self.mode()
        change, error = self.service.apply_mode(selected)
        self.assertEqual(error, "")
        self.assertEqual(change.status, "pending")
        self.assertTrue(change.token)
        timer = self.timers.items[-1]
        self.assertTrue(timer.started)
        self.assertFalse(timer.daemon)
        self.assertEqual(change.remaining_seconds, 15)
        ok, _ = change.confirm()
        self.assertTrue(ok)
        self.assertEqual(change.status, "kept")
        self.assertTrue(timer.cancelled)
        timer.fire()
        self.assertEqual(change.status, "kept")
        self.assertEqual(change.remaining_seconds, 0)
        self.assertEqual(self.runner.commands, ["output eDP-1 mode 1280x720@59.94Hz"])
        self.assertTrue(change.confirm()[0])
        self.assertFalse(change.revert()[0])
        self.assertTrue(change.close()[0])

    def test_timeout_restores_all_outputs_including_rotation_position_scale_power_and_disabled(self):
        change, error = self.service.apply_mode(self.mode())
        self.assertEqual(error, "")
        self.clock.now += 15
        self.timers.items[-1].fire()
        self.assertEqual(change.status, "reverted")
        self.assertFalse(change.confirm()[0])
        self.assertIn("output HDMI-A-1 enable mode 1920x1080@60Hz pos 1920 -100 scale 1.5 transform 90 power off", self.runner.commands)
        self.assertIn("output DP-2 disable", self.runner.commands)
        self.assertEqual(self.runner.outputs[0]["current_mode"], sway_outputs()[0]["current_mode"])
        self.assertTrue(change.revert()[0])

    def test_stale_mode_or_changed_scale_is_rejected_before_mutation(self):
        selected = self.mode()
        self.runner.outputs[0]["modes"] = [self.runner.outputs[0]["modes"][0]]
        change, error = self.service.apply_mode(selected)
        self.assertIsNone(change)
        self.assertIn("no longer available", error)
        self.assertEqual(self.runner.commands, [])
        self.runner.outputs = sway_outputs()
        self.runner.outputs[0]["scale"] = 2
        self.assertIsNone(self.service.apply_mode(selected)[0])
        self.assertEqual(self.runner.commands, [])

    def test_inactive_monitor_is_listed_but_activation_is_refused(self):
        change, error = self.service.apply_mode(self.mode(output="DP-2"))
        self.assertIsNone(change)
        self.assertIn("Enable this monitor", error)
        self.assertEqual(self.runner.commands, [])

    def test_reported_success_without_actual_mode_is_rolled_back(self):
        self.runner.ignore_apply = True
        change, error = self.service.apply_mode(self.mode())
        self.assertIsNone(change)
        self.assertIn("does not match", error)
        self.assertIn("was restored", error)
        self.assertTrue(self.timers.items[-1].cancelled)

    def test_ipc_error_with_success_exit_code_and_partial_change_rolls_back(self):
        self.runner.fail_apply = True
        change, error = self.service.apply_mode(self.mode())
        self.assertIsNone(change)
        self.assertIn("mode failed", error)
        self.assertEqual(self.runner.outputs[0]["current_mode"], sway_outputs()[0]["current_mode"])

    def test_cancel_after_slow_discovery_prevents_command(self):
        selected = self.mode()
        active = [True]
        self.runner.after_discovery = lambda: active.__setitem__(0, False)
        change, error = self.service.apply_mode(selected, is_current=lambda: active[0])
        self.assertIsNone(change)
        self.assertIn("cancelled", error)
        self.assertEqual(self.runner.commands, [])
        self.assertEqual(self.timers.items, [])

    def test_cancel_after_apply_restores_before_return(self):
        active = [True]
        self.runner.after_apply = lambda: active.__setitem__(0, False)
        change, error = self.service.apply_mode(self.mode(), is_current=lambda: active[0])
        self.assertIsNone(change)
        self.assertIn("cancelled", error)
        self.assertIn("was restored", error)
        self.assertEqual(self.runner.outputs[0]["current_mode"], sway_outputs()[0]["current_mode"])

    def test_stale_cancelled_request_does_not_revert_a_newer_pending_change(self):
        change, _ = self.service.apply_mode(self.mode())
        count = len(self.runner.commands)
        stale, error = self.service.apply_mode(self.mode(width=1024), is_current=lambda: False)
        self.assertIsNone(stale)
        self.assertIn("cancelled", error)
        self.assertEqual(change.status, "pending")
        self.assertEqual(len(self.runner.commands), count)

    def test_cancel_between_watchdog_start_and_apply_prevents_mutation(self):
        active = [True]
        original = Timer.start

        def start(timer):
            original(timer)
            active[0] = False

        with patch.object(Timer, "start", start):
            change, error = self.service.apply_mode(self.mode(), is_current=lambda: active[0])
        self.assertIsNone(change)
        self.assertIn("cancelled", error)
        self.assertEqual(self.runner.commands, [])
        self.assertTrue(self.timers.items[-1].cancelled)

    def test_confirm_checks_deadline_even_when_watchdog_waits_for_lock(self):
        change, _ = self.service.apply_mode(self.mode())
        self.clock.now += 15
        ok, text = change.confirm()
        self.assertFalse(ok)
        self.assertIn("expired", text)
        self.assertEqual(change.status, "reverted")
        calls = len(self.runner.commands)
        self.timers.items[-1].fire()
        self.assertEqual(len(self.runner.commands), calls)

    def test_slow_confirmation_verification_cannot_extend_deadline(self):
        change, _ = self.service.apply_mode(self.mode())
        self.runner.after_discovery = lambda: setattr(self.clock, "now", 116)
        self.assertFalse(change.confirm()[0])
        self.assertEqual(change.status, "reverted")

    def test_second_change_reverts_first_before_using_new_snapshot(self):
        first, _ = self.service.apply_mode(self.mode())
        second, error = self.service.apply_mode(self.mode(width=1024))
        self.assertEqual(error, "")
        self.assertEqual(first.status, "reverted")
        self.assertEqual(second.status, "pending")
        self.assertEqual(self.runner.outputs[0]["current_mode"]["width"], 1024)
        self.timers.items[0].fire()
        self.assertEqual(second.status, "pending")
        self.assertEqual(self.runner.outputs[0]["current_mode"]["width"], 1024)

    def test_failed_recovery_is_reported_and_blocks_additional_changes(self):
        selected = self.mode()
        change, _ = self.service.apply_mode(selected)
        self.runner.fail_restore = True
        ok, text = change.revert()
        self.assertFalse(ok)
        self.assertEqual(change.status, "failed")
        self.assertIn("could not be fully restored", text)
        calls = len(self.runner.commands)
        retry, error = self.service.apply_mode(selected)
        self.assertIsNone(retry)
        self.assertIn("desktop settings", error)
        self.assertEqual(len(self.runner.commands), calls)
        self.assertFalse(self.service.close()[0])

    def test_service_and_change_close_restore_pending_change_only_once(self):
        change, _ = self.service.apply_mode(self.mode())
        self.assertTrue(change.close()[0])
        count = len(self.runner.commands)
        self.assertTrue(self.service.close()[0])
        self.timers.items[-1].fire()
        self.assertEqual(len(self.runner.commands), count)
        self.assertEqual(change.status, "reverted")
        self.assertIsNone(self.service.apply_mode(self.mode_from_fixture())[0])
        self.assertIn("closed", self.service.list_modes()[1])

    @staticmethod
    def mode_from_fixture():
        return DisplayMode("eDP-1", 1280, 720, 59.94, "1280x720@59.94Hz")

    def test_timer_and_confirm_race_restore_exactly_once(self):
        change, _ = self.service.apply_mode(self.mode())
        self.clock.now += 15
        barrier = threading.Barrier(3)
        results = []

        def confirm():
            barrier.wait()
            results.append(change.confirm()[0])

        def expire():
            barrier.wait()
            self.timers.items[-1].fire()

        threads = [threading.Thread(target=confirm), threading.Thread(target=expire)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(results, [False])
        self.assertEqual(change.status, "reverted")
        self.assertEqual(len(self.runner.commands), 4)

    def test_invalid_watchdog_timeout_and_timer_start_failure_do_not_mutate(self):
        selected = self.mode()
        for timeout in (0, 121, float("nan"), True):
            self.assertIsNone(self.service.apply_mode(selected, timeout=timeout)[0])
        with patch.object(Timer, "start", side_effect=RuntimeError("no threads")):
            change, error = self.service.apply_mode(selected)
        self.assertIsNone(change)
        self.assertIn("recovery timer", error)
        self.assertEqual(self.runner.commands, [])

    def test_real_independent_watchdog_restores_without_a_gui_or_confirmation(self):
        service = DisplayService(self.runner, which, {"SWAYSOCK": "/tmp/test.sock"})
        self.addCleanup(service.close)
        modes, _ = service.list_modes()
        selected = next(mode for mode in modes if mode.output == "eDP-1" and mode.width == 1280)
        change, error = service.apply_mode(selected, timeout=1)
        self.assertEqual(error, "")
        self.assertFalse(change._timer.daemon)
        change._timer.join(3)
        self.assertFalse(change._timer.is_alive())
        self.assertEqual(change.status, "reverted")
        self.assertEqual(self.runner.outputs[0]["current_mode"], sway_outputs()[0]["current_mode"])

    def test_concurrent_changes_serialize_and_leave_only_the_latest_pending(self):
        choices = [self.mode(), self.mode(width=1024)]
        results = []
        barrier = threading.Barrier(3)

        def apply(mode):
            barrier.wait()
            results.append(self.service.apply_mode(mode))

        threads = [threading.Thread(target=apply, args=(mode,)) for mode in choices]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual([error for _, error in results], ["", ""])
        self.assertCountEqual([change.status for change, _ in results], ["reverted", "pending"])
        pending = next(change for change, _ in results if change.status == "pending")
        self.assertEqual(self.runner.outputs[0]["current_mode"]["width"], pending._mode.width)
        self.assertTrue(self.service.close()[0])
        self.assertEqual(self.runner.outputs[0]["current_mode"], sway_outputs()[0]["current_mode"])

    def test_backend_never_falls_back_to_xwayland_or_an_arbitrary_sway_install(self):
        calls = []
        for env in (
            {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"},
            {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
            {},
        ):
            service = DisplayService(lambda argv, timeout: calls.append(argv), which, env)
            modes, error = service.list_modes()
            self.assertEqual(modes, [])
            self.assertTrue(error)
        self.assertEqual(calls, [])

    def test_malformed_and_unsafe_sway_discovery_never_executes_a_command(self):
        cases = ["{}", "[]", "not JSON"]
        for key, value in (("name", "*"), ("name", "eDP-1; exec x"), ("transform", "invalid"), ("scale", float("nan"))):
            outputs = sway_outputs()
            outputs[0][key] = value
            cases.append(json.dumps(outputs))
        outputs = sway_outputs()
        outputs[0]["current_mode"] = {"width": 999, "height": 999, "refresh": 60000}
        cases.append(json.dumps(outputs))
        for reply in cases:
            with self.subTest(reply=reply):
                self.service._runner = lambda argv, timeout: (True, reply)
                self.assertIsNone(self.service.apply_mode(self.mode_from_fixture())[0])


class TestXRandR(unittest.TestCase):
    def setUp(self):
        self.runner = XRandR()
        self.timers = Timers()
        self.service = DisplayService(self.runner, which, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
                                      timer_factory=self.timers, clock=Clock())
        self.addCleanup(self.service.close)

    def test_x11_enumerates_rates_and_full_output_snapshot(self):
        modes, error = self.service.list_modes()
        self.assertEqual(error, "")
        self.assertEqual(len(modes), 5)
        hdmi = next(mode for mode in modes if mode.output == "HDMI-1")
        self.assertEqual(hdmi.scale, 1.25)
        self.assertTrue(hdmi.current)
        self.assertEqual(self.runner.calls, [["xrandr", "--query"], ["xrandr", "--verbose"]])

    def test_x11_apply_and_restore_whole_layout_in_one_argv(self):
        modes, _ = self.service.list_modes()
        selected = next(mode for mode in modes if mode.width == 1280)
        change, error = self.service.apply_mode(selected)
        self.assertEqual(error, "")
        self.assertEqual(self.runner.calls[4], ["xrandr", "--output", "eDP-1", "--mode", "0x48", "--rate", "60", "--crtc", "0"])
        self.assertTrue(change.revert()[0])
        restore = next(argv for argv in self.runner.calls if "--fb" in argv)
        self.assertEqual(restore[:3], ["xrandr", "--fb", "3280x1080"])
        self.assertNotIn("--noprimary", restore)
        self.assertIn("--primary", restore)
        self.assertIn("1920x-100", restore)
        self.assertIn("1.25,0,0,0,1.25,0,0,0,1", restore)
        self.assertIn("1125x2000+1920+0/1125x2000+1920+0/0/0/0/0", restore)
        self.assertNotIn("--filter", restore)
        self.assertEqual(restore[-6:], ["--output", "DP-2", "--off", "--output", "VGA-1", "--off"])
        hdmi = restore.index("HDMI-1")
        self.assertIn("left", restore[hdmi:])
        self.assertIn("x", restore[hdmi:])
        self.assertEqual(change.status, "reverted")

    def test_missing_transform_or_changed_topology_is_refused_before_change(self):
        self.runner.verbose = X_VERBOSE.replace("Transform:", "Unknown:")
        modes, error = self.service.list_modes()
        self.assertEqual(modes, [])
        self.assertIn("transform", error)
        self.runner.verbose = X_VERBOSE.replace("VGA-1 disconnected", "VGA-2 disconnected")
        self.assertTrue(self.service.list_modes()[1])
        self.assertTrue(all(len(argv) == 2 for argv in self.runner.calls))

    def test_no_primary_uses_the_global_clear_option(self):
        base_runner = self.runner

        def runner(argv, timeout=8):
            ok, text = base_runner(argv, timeout)
            return ok, text.replace("connected primary ", "connected ")

        self.service._runner = runner
        modes, _ = self.service.list_modes()
        change, error = self.service.apply_mode(next(mode for mode in modes if mode.width == 1280))
        self.assertEqual(error, "")
        self.assertTrue(change.revert()[0])
        restore = next(argv for argv in self.runner.calls if "--fb" in argv)
        self.assertIn("--noprimary", restore)
        self.assertNotIn("--primary", restore)

    def test_fractional_transform_restores_exact_fixed_point_without_filter_override(self):
        self.runner.verbose = X_VERBOSE.replace("1.250000", "1.333328").replace("filter: bilinear", "filter: nearest")
        modes, error = self.service.list_modes()
        self.assertEqual(error, "")
        hdmi = next(mode for mode in modes if mode.output == "HDMI-1")
        self.assertEqual(hdmi.scale, 87381 / 65536)
        change, error = self.service.apply_mode(next(mode for mode in modes if mode.width == 1280))
        self.assertEqual(error, "")
        self.assertTrue(change.revert()[0])
        restore = next(argv for argv in self.runner.calls if "--fb" in argv)
        self.assertIn("1.3333282470703125,0,0,0,1.3333282470703125,0,0,0,1", restore)
        self.assertIn("nearest", restore)
        self.assertNotIn("bilinear", restore)

    def test_same_name_and_rounded_rate_keep_distinct_native_ids(self):
        base_runner = self.runner

        def runner(argv, timeout=8):
            ok, text = base_runner(argv, timeout)
            return ok, text.replace("59.94", "60.00")

        self.service._runner = runner
        modes, error = self.service.list_modes()
        self.assertEqual(error, "")
        same_size = [mode for mode in modes if mode.output == "eDP-1" and mode.width == 1920]
        self.assertEqual(len(same_size), 2)
        self.assertEqual([mode.identifier for mode in same_size], ["0x47", "0x4b"])
        self.assertEqual([mode.current for mode in same_size], [True, False])


if __name__ == "__main__":
    unittest.main()
