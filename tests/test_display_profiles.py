"""Tests for src.display_profiles — GTK-free multi-monitor profile management."""

import unittest
from types import SimpleNamespace

from src.display_profiles import (
    DEFAULT_HEIGHT,
    DEFAULT_PROFILE_NAME,
    DEFAULT_WIDTH,
    DisplayProfile,
    DisplayProfileError,
    DisplayProfileSet,
    MonitorInfo,
    WindowGeometry,
    apply_geometry_to_config,
    center_on_monitor,
    clamp_to_monitor,
    describe_resolution,
    find_best_monitor,
    format_monitor_table,
    format_profile_table,
    geometry_from_config,
    is_geometry_visible,
    profiles_from_config,
    resolve_geometry,
    save_profiles_to_config,
    select_profile_for_monitors,
)


def make_monitors() -> list:
    """Two side-by-side monitors: primary on the left, secondary on the right."""
    return [
        MonitorInfo(index=0, x=0, y=0, width=1920, height=1080, primary=True, connector="eDP-1"),
        MonitorInfo(index=1, x=1920, y=0, width=2560, height=1440, primary=False, connector="DP-1"),
    ]


class TestMonitorInfo(unittest.TestCase):
    def test_right_bottom(self):
        m = MonitorInfo(index=0, x=100, y=200, width=800, height=600)
        self.assertEqual(m.right, 900)
        self.assertEqual(m.bottom, 800)

    def test_area(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        self.assertEqual(m.area, 1920 * 1080)

    def test_contains_point(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        self.assertTrue(m.contains_point(0, 0))
        self.assertTrue(m.contains_point(1919, 1079))
        self.assertFalse(m.contains_point(1920, 0))
        self.assertFalse(m.contains_point(-1, 0))
        self.assertFalse(m.contains_point(0, 1080))

    def test_intersection_area_full(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        self.assertEqual(m.intersection_area(100, 100, 400, 500), 400 * 500)

    def test_intersection_area_partial(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1000, height=1000)
        # Window extends past the right edge.
        self.assertEqual(m.intersection_area(800, 0, 400, 100), 200 * 100)

    def test_intersection_area_none(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1000, height=1000)
        self.assertEqual(m.intersection_area(2000, 2000, 100, 100), 0)

    def test_to_dict(self):
        m = MonitorInfo(index=1, x=1920, y=0, width=2560, height=1440,
                        primary=False, scale_factor=2, connector="DP-1")
        d = m.to_dict()
        self.assertEqual(d["index"], 1)
        self.assertEqual(d["connector"], "DP-1")
        self.assertEqual(d["scale_factor"], 2)


class TestWindowGeometry(unittest.TestCase):
    def test_to_dict(self):
        g = WindowGeometry(x=10, y=20, width=400, height=500)
        self.assertEqual(g.to_dict(), {"x": 10, "y": 20, "width": 400, "height": 500})

    def test_from_dict(self):
        g = WindowGeometry.from_dict({"x": 5, "y": 15, "width": 300, "height": 400})
        self.assertEqual(g.x, 5)
        self.assertEqual(g.height, 400)

    def test_from_dict_defaults(self):
        g = WindowGeometry.from_dict({})
        self.assertEqual(g.width, DEFAULT_WIDTH)
        self.assertEqual(g.height, DEFAULT_HEIGHT)


class TestDisplayProfile(unittest.TestCase):
    def test_defaults(self):
        p = DisplayProfile(name="home")
        self.assertEqual(p.monitor_index, 0)
        self.assertEqual(p.dock_mode, "float")
        self.assertEqual(p.dock_edge, "right")
        self.assertTrue(p.always_on_top)

    def test_to_dict_from_dict_roundtrip(self):
        p = DisplayProfile(
            name="office", monitor_index=1,
            geometry=WindowGeometry(2000, 100, 500, 600),
            dock_mode="dock", dock_edge="left", always_on_top=False,
        )
        p2 = DisplayProfile.from_dict(p.to_dict())
        self.assertEqual(p2.name, "office")
        self.assertEqual(p2.monitor_index, 1)
        self.assertEqual(p2.geometry.width, 500)
        self.assertEqual(p2.dock_mode, "dock")
        self.assertFalse(p2.always_on_top)

    def test_from_dict_not_dict(self):
        with self.assertRaises(DisplayProfileError):
            DisplayProfile.from_dict("not a dict")

    def test_from_dict_missing_name(self):
        with self.assertRaises(DisplayProfileError):
            DisplayProfile.from_dict({"geometry": {}})

    def test_from_dict_bad_geometry(self):
        with self.assertRaises(DisplayProfileError):
            DisplayProfile.from_dict({"name": "x", "geometry": "bad"})

    def test_validate_clean(self):
        p = DisplayProfile(name="ok", geometry=WindowGeometry(0, 0, 400, 500))
        self.assertEqual(p.validate(), [])

    def test_validate_empty_name(self):
        p = DisplayProfile(name="  ")
        warnings = p.validate()
        self.assertTrue(any("name" in w for w in warnings))

    def test_validate_bad_monitor_index(self):
        p = DisplayProfile(name="x", monitor_index=-1)
        warnings = p.validate()
        self.assertTrue(any("monitor_index" in w for w in warnings))

    def test_validate_small_width(self):
        p = DisplayProfile(name="x", geometry=WindowGeometry(0, 0, 50, 500))
        warnings = p.validate()
        self.assertTrue(any("width" in w for w in warnings))

    def test_validate_small_height(self):
        p = DisplayProfile(name="x", geometry=WindowGeometry(0, 0, 400, 50))
        warnings = p.validate()
        self.assertTrue(any("height" in w for w in warnings))

    def test_validate_huge(self):
        p = DisplayProfile(name="x", geometry=WindowGeometry(0, 0, 9000, 9000))
        warnings = p.validate()
        self.assertTrue(any("at most" in w for w in warnings))

    def test_validate_bad_dock_mode(self):
        p = DisplayProfile(name="x", dock_mode="invalid")
        warnings = p.validate()
        self.assertTrue(any("dock_mode" in w for w in warnings))

    def test_validate_bad_dock_edge(self):
        p = DisplayProfile(name="x", dock_edge="center")
        warnings = p.validate()
        self.assertTrue(any("dock_edge" in w for w in warnings))


class TestDisplayProfileSet(unittest.TestCase):
    def test_default(self):
        ps = DisplayProfileSet.default()
        self.assertIn(DEFAULT_PROFILE_NAME, ps.profiles)
        self.assertEqual(ps.active_profile, DEFAULT_PROFILE_NAME)

    def test_add_and_get(self):
        ps = DisplayProfileSet()
        p = DisplayProfile(name="test")
        ps.add(p)
        self.assertEqual(ps.get("test"), p)
        self.assertIsNone(ps.get("nonexistent"))

    def test_add_invalid_raises(self):
        ps = DisplayProfileSet()
        with self.assertRaises(DisplayProfileError):
            ps.add(DisplayProfile(name="  "))

    def test_remove(self):
        ps = DisplayProfileSet.default()
        self.assertTrue(ps.remove(DEFAULT_PROFILE_NAME))
        self.assertIsNone(ps.get(DEFAULT_PROFILE_NAME))
        self.assertFalse(ps.remove(DEFAULT_PROFILE_NAME))

    def test_remove_active_updates_active(self):
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="a"))
        ps.add(DisplayProfile(name="b"))
        ps.active_profile = "a"
        ps.remove("a")
        self.assertEqual(ps.active_profile, "b")

    def test_names_sorted(self):
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="zebra"))
        ps.add(DisplayProfile(name="alpha"))
        self.assertEqual(ps.names(), ["alpha", "zebra"])

    def test_to_dict_from_dict_roundtrip(self):
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="home", monitor_index=0,
                              geometry=WindowGeometry(10, 20, 400, 500)))
        ps.add(DisplayProfile(name="work", monitor_index=1,
                              geometry=WindowGeometry(2000, 0, 600, 700)))
        ps.active_profile = "work"
        ps2 = DisplayProfileSet.from_dict(ps.to_dict())
        self.assertEqual(ps2.names(), ["home", "work"])
        self.assertEqual(ps2.active_profile, "work")
        self.assertEqual(ps2.get("work").geometry.width, 600)

    def test_from_dict_skips_invalid(self):
        data = {
            "active_profile": "good",
            "profiles": {
                "good": {"name": "good", "geometry": {"x": 0, "y": 0, "width": 400, "height": 500}},
                "bad": {"geometry": {}},  # missing name
            },
        }
        ps = DisplayProfileSet.from_dict(data)
        self.assertIn("good", ps.profiles)
        self.assertNotIn("bad", ps.profiles)

    def test_from_dict_not_dict(self):
        ps = DisplayProfileSet.from_dict("not a dict")
        self.assertEqual(ps.profiles, {})

    def test_from_dict_bad_profiles_type(self):
        ps = DisplayProfileSet.from_dict({"profiles": "not a dict"})
        self.assertEqual(ps.profiles, {})


class TestFindBestMonitor(unittest.TestCase):
    def test_empty_monitors(self):
        self.assertIsNone(find_best_monitor(WindowGeometry(0, 0, 100, 100), []))

    def test_no_intersection(self):
        monitors = make_monitors()
        g = WindowGeometry(5000, 5000, 100, 100)
        self.assertIsNone(find_best_monitor(g, monitors))

    def test_primary_monitor(self):
        monitors = make_monitors()
        g = WindowGeometry(100, 100, 400, 500)
        best = find_best_monitor(g, monitors)
        self.assertIsNotNone(best)
        self.assertEqual(best.index, 0)

    def test_secondary_monitor(self):
        monitors = make_monitors()
        g = WindowGeometry(2000, 100, 400, 500)
        best = find_best_monitor(g, monitors)
        self.assertIsNotNone(best)
        self.assertEqual(best.index, 1)

    def test_largest_intersection_wins(self):
        monitors = make_monitors()
        # Window straddles the boundary: mostly on monitor 1.
        g = WindowGeometry(1800, 100, 400, 500)
        best = find_best_monitor(g, monitors)
        self.assertIsNotNone(best)
        self.assertEqual(best.index, 1)


class TestIsGeometryVisible(unittest.TestCase):
    def test_no_monitors_assumes_visible(self):
        self.assertTrue(is_geometry_visible(WindowGeometry(0, 0, 100, 100), []))

    def test_fully_visible(self):
        monitors = make_monitors()
        self.assertTrue(is_geometry_visible(WindowGeometry(100, 100, 400, 500), monitors))

    def test_off_screen(self):
        monitors = make_monitors()
        self.assertFalse(is_geometry_visible(WindowGeometry(5000, 5000, 100, 100), monitors))

    def test_barely_visible(self):
        monitors = [MonitorInfo(index=0, x=0, y=0, width=1920, height=1080, primary=True)]
        # Only 10 pixels visible on each axis — below the 50px threshold.
        g = WindowGeometry(1910, 1070, 400, 500)
        self.assertFalse(is_geometry_visible(g, monitors, min_visible=50))

    def test_custom_threshold(self):
        monitors = make_monitors()
        g = WindowGeometry(1910, 1070, 400, 500)  # 10x10 visible on monitor 0
        self.assertTrue(is_geometry_visible(g, monitors, min_visible=5))


class TestClampToMonitor(unittest.TestCase):
    def test_already_inside(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        g = WindowGeometry(100, 100, 400, 500)
        clamped = clamp_to_monitor(g, m)
        self.assertEqual(clamped, g)

    def test_off_right_edge(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        g = WindowGeometry(1800, 100, 400, 500)
        clamped = clamp_to_monitor(g, m)
        self.assertEqual(clamped.x + clamped.width, 1920)
        self.assertEqual(clamped.y, 100)

    def test_off_bottom_edge(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        g = WindowGeometry(100, 1000, 400, 500)
        clamped = clamp_to_monitor(g, m)
        self.assertEqual(clamped.y + clamped.height, 1080)

    def test_larger_than_monitor(self):
        m = MonitorInfo(index=0, x=0, y=0, width=800, height=600)
        g = WindowGeometry(0, 0, 1920, 1080)
        clamped = clamp_to_monitor(g, m)
        self.assertEqual(clamped.width, 800)
        self.assertEqual(clamped.height, 600)

    def test_negative_position(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        g = WindowGeometry(-100, -100, 400, 500)
        clamped = clamp_to_monitor(g, m)
        self.assertGreaterEqual(clamped.x, 0)
        self.assertGreaterEqual(clamped.y, 0)

    def test_non_zero_origin_monitor(self):
        m = MonitorInfo(index=1, x=1920, y=0, width=2560, height=1440)
        g = WindowGeometry(2000, 100, 400, 500)
        clamped = clamp_to_monitor(g, m)
        self.assertGreaterEqual(clamped.x, 1920)
        self.assertLess(clamped.x + clamped.width, 1920 + 2560 + 1)


class TestCenterOnMonitor(unittest.TestCase):
    def test_centered(self):
        m = MonitorInfo(index=0, x=0, y=0, width=1920, height=1080)
        g = center_on_monitor(400, 500, m)
        self.assertEqual(g.x, (1920 - 400) // 2)
        self.assertEqual(g.y, (1080 - 500) // 2)

    def test_non_zero_origin(self):
        m = MonitorInfo(index=1, x=1920, y=0, width=2560, height=1440)
        g = center_on_monitor(400, 500, m)
        self.assertEqual(g.x, 1920 + (2560 - 400) // 2)
        self.assertEqual(g.y, (1440 - 500) // 2)


class TestResolveGeometry(unittest.TestCase):
    def test_no_monitors_returns_unchanged(self):
        g = WindowGeometry(100, 100, 400, 500)
        self.assertEqual(resolve_geometry(g, []), g)

    def test_already_visible(self):
        monitors = make_monitors()
        g = WindowGeometry(100, 100, 400, 500)
        resolved = resolve_geometry(g, monitors)
        self.assertEqual(resolved, g)

    def test_off_screen_centers_on_primary(self):
        monitors = make_monitors()
        g = WindowGeometry(5000, 5000, 400, 500)
        resolved = resolve_geometry(g, monitors)
        # Should be centered on the primary monitor (index 0).
        primary = monitors[0]
        self.assertGreaterEqual(resolved.x, primary.x)
        self.assertLess(resolved.x + resolved.width, primary.right + 1)
        self.assertGreaterEqual(resolved.y, primary.y)
        self.assertLess(resolved.y + resolved.height, primary.bottom + 1)

    def test_off_screen_secondary_no_primary(self):
        monitors = [
            MonitorInfo(index=0, x=0, y=0, width=1920, height=1080, primary=False),
            MonitorInfo(index=1, x=1920, y=0, width=2560, height=1440, primary=False),
        ]
        g = WindowGeometry(5000, 5000, 400, 500)
        resolved = resolve_geometry(g, monitors)
        # Falls back to monitor 0 (first) when no primary.
        self.assertGreaterEqual(resolved.x, 0)
        self.assertLess(resolved.x + resolved.width, 1921)

    def test_with_profile_valid_monitor(self):
        monitors = make_monitors()
        profile = DisplayProfile(name="p", monitor_index=1,
                                  geometry=WindowGeometry(2000, 100, 400, 500))
        g = WindowGeometry(100, 100, 400, 500)
        resolved = resolve_geometry(g, monitors, profile)
        # Profile targets monitor 1, so the geometry should be clamped to it.
        self.assertGreaterEqual(resolved.x, 1920)

    def test_with_profile_invalid_monitor_index(self):
        monitors = make_monitors()
        profile = DisplayProfile(name="p", monitor_index=99,
                                  geometry=WindowGeometry(100, 100, 400, 500))
        g = WindowGeometry(100, 100, 400, 500)
        resolved = resolve_geometry(g, monitors, profile)
        # Falls back to best-monitor logic.
        self.assertEqual(resolved, g)

    def test_clamped_to_monitor(self):
        monitors = make_monitors()
        # Window extends past the right edge of monitor 0, but mostly lands
        # on monitor 1 — resolve_geometry picks monitor 1 as the best match
        # and clamps the window to stay on it.
        g = WindowGeometry(1800, 100, 400, 500)
        resolved = resolve_geometry(g, monitors)
        # The window should be fully on monitor 1 (x >= 1920).
        self.assertGreaterEqual(resolved.x, 1920)
        self.assertLessEqual(resolved.x + resolved.width, 1920 + 2560)


class TestSelectProfileForMonitors(unittest.TestCase):
    def test_no_monitors_returns_active(self):
        ps = DisplayProfileSet.default()
        result = select_profile_for_monitors(ps, [])
        self.assertIsNotNone(result)
        self.assertEqual(result.name, DEFAULT_PROFILE_NAME)

    def test_active_profile_valid(self):
        monitors = make_monitors()
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="home", monitor_index=0))
        ps.add(DisplayProfile(name="work", monitor_index=1))
        ps.active_profile = "work"
        result = select_profile_for_monitors(ps, monitors)
        self.assertEqual(result.name, "work")

    def test_active_profile_invalid_falls_back(self):
        monitors = make_monitors()
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="home", monitor_index=0))
        ps.add(DisplayProfile(name="gone", monitor_index=99))
        ps.active_profile = "gone"
        result = select_profile_for_monitors(ps, monitors)
        self.assertEqual(result.name, "home")

    def test_no_valid_profiles(self):
        monitors = make_monitors()
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="gone", monitor_index=99))
        ps.active_profile = "gone"
        result = select_profile_for_monitors(ps, monitors)
        self.assertIsNone(result)


class TestConfigBridge(unittest.TestCase):
    def test_geometry_from_config(self):
        config = SimpleNamespace(get=lambda k, d=None: {
            "app.x_position": 50, "app.y_position": 60,
            "app.width": 450, "app.height": 550,
        }.get(k, d))
        g = geometry_from_config(config)
        self.assertEqual(g.x, 50)
        self.assertEqual(g.height, 550)

    def test_geometry_from_config_defaults(self):
        config = SimpleNamespace(get=lambda k, d=None: d)
        g = geometry_from_config(config)
        self.assertEqual(g.width, DEFAULT_WIDTH)

    def test_apply_geometry_to_config(self):
        stored = {}
        config = SimpleNamespace(set=lambda k, v: stored.__setitem__(k, v))
        g = WindowGeometry(10, 20, 300, 400)
        apply_geometry_to_config(g, config)
        self.assertEqual(stored["app.x_position"], 10)
        self.assertEqual(stored["app.y_position"], 20)
        self.assertEqual(stored["app.width"], 300)
        self.assertEqual(stored["app.height"], 400)

    def test_profiles_from_config(self):
        raw = {
            "home": {"name": "home", "monitor_index": 0,
                      "geometry": {"x": 0, "y": 0, "width": 400, "height": 500}},
        }
        config = SimpleNamespace(get=lambda k, d=None: {
            "monitors.profiles": raw,
            "monitors.active_profile": "home",
        }.get(k, d))
        ps = profiles_from_config(config)
        self.assertIn("home", ps.profiles)
        self.assertEqual(ps.active_profile, "home")

    def test_profiles_from_config_empty_returns_default(self):
        config = SimpleNamespace(get=lambda k, d=None: d)
        ps = profiles_from_config(config)
        self.assertIn(DEFAULT_PROFILE_NAME, ps.profiles)

    def test_profiles_from_config_non_dict(self):
        config = SimpleNamespace(get=lambda k, d=None: "not a dict" if k == "monitors.profiles" else d)
        ps = profiles_from_config(config)
        self.assertIn(DEFAULT_PROFILE_NAME, ps.profiles)

    def test_save_profiles_to_config(self):
        stored = {}
        flushed = []
        config = SimpleNamespace(
            set=lambda k, v: stored.__setitem__(k, v),
            flush=lambda: flushed.append(True),
        )
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="test", monitor_index=1,
                              geometry=WindowGeometry(2000, 0, 500, 600)))
        ps.active_profile = "test"
        save_profiles_to_config(ps, config)
        self.assertIn("test", stored["monitors.profiles"])
        self.assertEqual(stored["monitors.active_profile"], "test")
        self.assertEqual(flushed, [True])


class TestFormatMonitorTable(unittest.TestCase):
    def test_empty(self):
        text = format_monitor_table([])
        self.assertIn("No monitors", text)

    def test_with_monitors(self):
        text = format_monitor_table(make_monitors())
        self.assertIn("Index", text)
        self.assertIn("eDP-1", text)
        self.assertIn("DP-1", text)
        self.assertIn("yes", text)  # primary marker


class TestFormatProfileTable(unittest.TestCase):
    def test_empty(self):
        text = format_profile_table(DisplayProfileSet())
        self.assertIn("No profiles", text)

    def test_with_profiles(self):
        ps = DisplayProfileSet()
        ps.add(DisplayProfile(name="home", monitor_index=0))
        ps.add(DisplayProfile(name="work", monitor_index=1))
        ps.active_profile = "work"
        text = format_profile_table(ps)
        self.assertIn("home", text)
        self.assertIn("work", text)
        self.assertIn("*", text)  # active marker


class TestDescribeResolution(unittest.TestCase):
    def test_no_monitors(self):
        g = WindowGeometry(100, 100, 400, 500)
        text = describe_resolution(g, [])
        self.assertIn("headless", text)

    def test_visible(self):
        monitors = make_monitors()
        g = WindowGeometry(100, 100, 400, 500)
        text = describe_resolution(g, monitors)
        self.assertIn("monitor 0", text)
        self.assertIn("100% visible", text)

    def test_off_screen_resolved(self):
        monitors = make_monitors()
        g = WindowGeometry(5000, 5000, 400, 500)
        text = describe_resolution(g, monitors)
        self.assertIn("monitor", text)


class TestDetectMonitors(unittest.TestCase):
    def test_returns_empty_without_gtk(self):
        from src.display_profiles import detect_monitors
        # In the test environment, GTK may or may not be available.
        # We just verify it returns a list (possibly empty) without raising.
        result = detect_monitors()
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
