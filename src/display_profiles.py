"""GTK-free multi-monitor display profile management.

The main window currently reads ``app.x_position`` / ``app.y_position`` /
``app.width`` / ``app.height`` directly from the config to position itself.
On a multi-monitor setup, those absolute coordinates may land on a different
screen than the one the user expects — or off-screen entirely when a monitor
is disconnected.

This module provides:

- :class:`MonitorInfo` — a description of one connected display (index,
  geometry, primary flag, scale factor, connector name).
- :class:`DisplayProfile` — a named set of window geometry values
  (x, y, width, height) plus optional dock settings, associated with a
  monitor index.
- :class:`DisplayProfileSet` — a collection of profiles with helpers to
  find the best profile for a given monitor layout, validate geometries
  against the available monitors, and compute fallbacks.
- :func:`geometry_from_config` / :func:`apply_geometry_to_config` — bridge
  between the ConfigManager's ``app.*`` keys and a :class:`WindowGeometry`.
- :func:`resolve_geometry` — given the current monitors and a profile (or
  raw geometry), return a geometry that is guaranteed to be visible on at
  least one monitor, clamping to the primary display if needed.
- :func:`detect_monitors` — production hook that queries GDK for the
  connected monitors. Returns an empty list when GTK/GDK is unavailable
  (headless, tests), so callers can fall back to the saved geometry.

All functions are pure or injectable; none import GTK at module level.
The GUI and CLI both use them.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Minimum visible area (in pixels) that a window must have on a monitor
#: to be considered "on screen". Prevents a window that is 99% off-screen
#: from being treated as valid.
MIN_VISIBLE_PIXELS = 50

#: Default window size when no profile matches.
DEFAULT_WIDTH = 400
DEFAULT_HEIGHT = 500

#: Profile name used when the user has not configured any profiles.
DEFAULT_PROFILE_NAME = "default"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DisplayProfileError(Exception):
    """Raised when a display profile operation cannot be completed."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorInfo:
    """Description of one connected display."""

    index: int
    x: int
    y: int
    width: int
    height: int
    primary: bool = False
    scale_factor: int = 1
    connector: str = ""

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def contains_point(self, px: int, py: int) -> bool:
        """True when the point (px, py) is inside this monitor's geometry."""
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersection_area(self, x: int, y: int, w: int, h: int) -> int:
        """Area (pixels^2) of the rectangle (x, y, w, h) that overlaps this monitor."""
        ix0 = max(self.x, x)
        iy0 = max(self.y, y)
        ix1 = min(self.right, x + w)
        iy1 = min(self.bottom, y + h)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0
        return (ix1 - ix0) * (iy1 - iy0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "primary": self.primary,
            "scale_factor": self.scale_factor,
            "connector": self.connector,
        }


@dataclass(frozen=True)
class WindowGeometry:
    """Window position and size."""

    x: int
    y: int
    width: int
    height: int

    def to_dict(self) -> Dict[str, Any]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WindowGeometry":
        return cls(
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            width=int(data.get("width", DEFAULT_WIDTH)),
            height=int(data.get("height", DEFAULT_HEIGHT)),
        )


@dataclass
class DisplayProfile:
    """A named window placement associated with a monitor."""

    name: str
    monitor_index: int = 0
    geometry: WindowGeometry = field(default_factory=lambda: WindowGeometry(100, 100, DEFAULT_WIDTH, DEFAULT_HEIGHT))
    dock_mode: str = "float"
    dock_edge: str = "right"
    always_on_top: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "monitor_index": self.monitor_index,
            "geometry": self.geometry.to_dict(),
            "dock_mode": self.dock_mode,
            "dock_edge": self.dock_edge,
            "always_on_top": self.always_on_top,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayProfile":
        if not isinstance(data, dict):
            raise DisplayProfileError("profile must be a dict")
        name = data.get("name", "")
        if not name or not isinstance(name, str):
            raise DisplayProfileError("profile 'name' is required")
        geometry_data = data.get("geometry", {})
        if not isinstance(geometry_data, dict):
            raise DisplayProfileError("profile 'geometry' must be a dict")
        return cls(
            name=name,
            monitor_index=int(data.get("monitor_index", 0)),
            geometry=WindowGeometry.from_dict(geometry_data),
            dock_mode=str(data.get("dock_mode", "float")),
            dock_edge=str(data.get("dock_edge", "right")),
            always_on_top=bool(data.get("always_on_top", True)),
        )

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if not self.name.strip():
            warnings.append("profile name is empty")
        if self.monitor_index < 0:
            warnings.append("monitor_index must be >= 0")
        if self.geometry.width < 100:
            warnings.append("width must be at least 100 pixels")
        if self.geometry.height < 100:
            warnings.append("height must be at least 100 pixels")
        if self.geometry.width > 8192:
            warnings.append("width must be at most 8192 pixels")
        if self.geometry.height > 8192:
            warnings.append("height must be at most 8192 pixels")
        if self.dock_mode not in ("float", "dock"):
            warnings.append("dock_mode must be 'float' or 'dock'")
        if self.dock_edge not in ("left", "right", "top", "bottom"):
            warnings.append("dock_edge must be one of: left, right, top, bottom")
        return warnings


# ---------------------------------------------------------------------------
# Profile set
# ---------------------------------------------------------------------------


@dataclass
class DisplayProfileSet:
    """A collection of display profiles."""

    profiles: Dict[str, DisplayProfile] = field(default_factory=dict)
    active_profile: str = DEFAULT_PROFILE_NAME

    def add(self, profile: DisplayProfile) -> None:
        warnings = profile.validate()
        if warnings:
            raise DisplayProfileError("invalid profile: " + "; ".join(warnings))
        self.profiles[profile.name] = profile

    def remove(self, name: str) -> bool:
        if name in self.profiles:
            del self.profiles[name]
            if self.active_profile == name:
                self.active_profile = next(iter(self.profiles), DEFAULT_PROFILE_NAME)
            return True
        return False

    def get(self, name: str) -> Optional[DisplayProfile]:
        return self.profiles.get(name)

    def names(self) -> List[str]:
        return sorted(self.profiles.keys())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_profile": self.active_profile,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DisplayProfileSet":
        if not isinstance(data, dict):
            return cls()
        profiles_data = data.get("profiles", {})
        if not isinstance(profiles_data, dict):
            profiles_data = {}
        profiles: Dict[str, DisplayProfile] = {}
        for name, profile_data in profiles_data.items():
            try:
                profiles[name] = DisplayProfile.from_dict(profile_data)
            except DisplayProfileError as error:
                logger.warning("Skipping invalid profile %r: %s", name, error)
        return cls(
            profiles=profiles,
            active_profile=str(data.get("active_profile", DEFAULT_PROFILE_NAME)),
        )

    @classmethod
    def default(cls) -> "DisplayProfileSet":
        """A profile set with one default profile on the primary monitor."""
        return cls(profiles={
            DEFAULT_PROFILE_NAME: DisplayProfile(
                name=DEFAULT_PROFILE_NAME,
                monitor_index=0,
                geometry=WindowGeometry(100, 100, DEFAULT_WIDTH, DEFAULT_HEIGHT),
            ),
        })


# ---------------------------------------------------------------------------
# Geometry resolution
# ---------------------------------------------------------------------------


def find_best_monitor(
    geometry: WindowGeometry,
    monitors: List[MonitorInfo],
) -> Optional[MonitorInfo]:
    """Return the monitor with the largest intersection with the window rect.

    Returns None when `monitors` is empty or no monitor intersects the rect.
    """
    if not monitors:
        return None
    best: Optional[MonitorInfo] = None
    best_area = 0
    for monitor in monitors:
        area = monitor.intersection_area(geometry.x, geometry.y, geometry.width, geometry.height)
        if area > best_area:
            best = monitor
            best_area = area
    return best


def is_geometry_visible(
    geometry: WindowGeometry,
    monitors: List[MonitorInfo],
    min_visible: int = MIN_VISIBLE_PIXELS,
) -> bool:
    """True when the window rect has at least `min_visible` pixels on any monitor."""
    if not monitors:
        return True  # No monitor info: assume visible (headless, tests).
    for monitor in monitors:
        visible_width = min(monitor.right, geometry.x + geometry.width) - max(monitor.x, geometry.x)
        visible_height = min(monitor.bottom, geometry.y + geometry.height) - max(monitor.y, geometry.y)
        if visible_width > 0 and visible_height > 0:
            if visible_width * visible_height >= min_visible * min_visible:
                return True
            # Also accept if at least min_visible pixels in each dimension.
            if visible_width >= min_visible and visible_height >= min_visible:
                return True
    return False


def clamp_to_monitor(
    geometry: WindowGeometry,
    monitor: MonitorInfo,
) -> WindowGeometry:
    """Clamp the window rect so it fits entirely within `monitor`.

    The window size is preserved when possible; if the window is larger than
    the monitor, it is shrunk to fit. The position is adjusted so the window
    is fully visible.
    """
    width = min(geometry.width, monitor.width)
    height = min(geometry.height, monitor.height)
    # Center the window on the monitor if it was mostly off-screen.
    x = max(monitor.x, min(geometry.x, monitor.right - width))
    y = max(monitor.y, min(geometry.y, monitor.bottom - height))
    return WindowGeometry(x=x, y=y, width=width, height=height)


def center_on_monitor(
    width: int,
    height: int,
    monitor: MonitorInfo,
) -> WindowGeometry:
    """Return a geometry centered on `monitor` with the given size."""
    x = monitor.x + (monitor.width - width) // 2
    y = monitor.y + (monitor.height - height) // 2
    return WindowGeometry(x=x, y=y, width=width, height=height)


def resolve_geometry(
    geometry: WindowGeometry,
    monitors: List[MonitorInfo],
    profile: Optional[DisplayProfile] = None,
) -> WindowGeometry:
    """Resolve a window geometry against the available monitors.

    Resolution order:
    1. If a profile is given and its monitor_index is valid, use the
       profile's monitor as the target.
    2. Find the monitor with the largest intersection with the geometry.
    3. If no monitor intersects (window is off-screen), center on the
       primary monitor (or monitor 0 if no primary).
    4. Clamp the geometry to the target monitor so it is fully visible.

    When `monitors` is empty (headless), the geometry is returned unchanged.
    """
    if not monitors:
        return geometry

    # Determine the target monitor.
    target: Optional[MonitorInfo] = None
    if profile is not None and 0 <= profile.monitor_index < len(monitors):
        target = monitors[profile.monitor_index]
    if target is None:
        target = find_best_monitor(geometry, monitors)
    if target is None:
        # Window is completely off-screen: use the primary or first monitor.
        target = next((m for m in monitors if m.primary), monitors[0])
        return clamp_to_monitor(
            center_on_monitor(geometry.width, geometry.height, target),
            target,
        )

    # If the window is already mostly on the target, just clamp it.
    return clamp_to_monitor(geometry, target)


def select_profile_for_monitors(
    profile_set: DisplayProfileSet,
    monitors: List[MonitorInfo],
) -> Optional[DisplayProfile]:
    """Select the best profile for the current monitor layout.

    Prefers the active profile when its monitor is connected; otherwise
    picks the first profile whose monitor_index is valid; otherwise None.
    """
    if not monitors:
        return profile_set.get(profile_set.active_profile)
    active = profile_set.get(profile_set.active_profile)
    if active is not None and active.monitor_index < len(monitors):
        return active
    for name in profile_set.names():
        profile = profile_set.profiles[name]
        if profile.monitor_index < len(monitors):
            return profile
    return None


# ---------------------------------------------------------------------------
# Config bridge
# ---------------------------------------------------------------------------


def geometry_from_config(config: Any) -> WindowGeometry:
    """Read window geometry from a ConfigManager's ``app.*`` keys."""
    return WindowGeometry(
        x=int(config.get("app.x_position", 100)),
        y=int(config.get("app.y_position", 100)),
        width=int(config.get("app.width", DEFAULT_WIDTH)),
        height=int(config.get("app.height", DEFAULT_HEIGHT)),
    )


def apply_geometry_to_config(geometry: WindowGeometry, config: Any) -> None:
    """Write window geometry back to a ConfigManager's ``app.*`` keys."""
    config.set("app.x_position", geometry.x)
    config.set("app.y_position", geometry.y)
    config.set("app.width", geometry.width)
    config.set("app.height", geometry.height)


def profiles_from_config(config: Any) -> DisplayProfileSet:
    """Load the display profile set from a ConfigManager."""
    raw = config.get("monitors.profiles", {})
    active = config.get("monitors.active_profile", DEFAULT_PROFILE_NAME)
    if not isinstance(raw, dict):
        return DisplayProfileSet.default()
    data: Dict[str, Any] = {"profiles": raw, "active_profile": active}
    profile_set = DisplayProfileSet.from_dict(data)
    if not profile_set.profiles:
        return DisplayProfileSet.default()
    return profile_set


def save_profiles_to_config(profile_set: DisplayProfileSet, config: Any) -> None:
    """Persist the display profile set to a ConfigManager."""
    config.set("monitors.profiles", {
        name: p.to_dict() for name, p in profile_set.profiles.items()
    })
    config.set("monitors.active_profile", profile_set.active_profile)
    config.flush()


# ---------------------------------------------------------------------------
# Monitor detection (production hook)
# ---------------------------------------------------------------------------


def detect_monitors() -> List[MonitorInfo]:
    """Query GDK for connected monitors and return their info.

    Returns an empty list when GTK/GDK is unavailable (headless, tests).
    The first monitor with ``is_primary()`` is marked ``primary=True``;
    if no monitor is primary, monitor 0 is marked primary.
    """
    try:
        import gi  # type: ignore[import-not-found]
        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk  # type: ignore[import-not-found]
    except (ImportError, ValueError):
        return []

    display = Gdk.Display.get_default()
    if display is None:
        return []

    monitors: List[MonitorInfo] = []
    n = display.get_n_monitors()
    for i in range(n):
        monitor = display.get_monitor(i)
        if monitor is None:
            continue
        geometry = monitor.get_geometry()
        # Scale factor: Gdk.Monitor.get_scale_factor() returns int (1 or 2).
        try:
            scale = monitor.get_scale_factor()
        except Exception:
            scale = 1
        # Connector name: available in GDK 3.22+.
        connector = ""
        try:
            connector = monitor.get_connector() or ""
        except Exception:
            pass
        monitors.append(MonitorInfo(
            index=i,
            x=geometry.x,
            y=geometry.y,
            width=geometry.width,
            height=geometry.height,
            primary=monitor.is_primary(),
            scale_factor=scale,
            connector=connector,
        ))
    # Ensure at least one primary.
    if monitors and not any(m.primary for m in monitors):
        monitors[0] = MonitorInfo(
            index=monitors[0].index,
            x=monitors[0].x,
            y=monitors[0].y,
            width=monitors[0].width,
            height=monitors[0].height,
            primary=True,
            scale_factor=monitors[0].scale_factor,
            connector=monitors[0].connector,
        )
    logger.info("Detected %d monitor(s)", len(monitors))
    return monitors


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_monitor_table(monitors: List[MonitorInfo]) -> str:
    """Human-readable table of connected monitors."""
    if not monitors:
        return "No monitors detected (headless or GDK unavailable)"
    lines = ["{:<6s} {:<18s} {:<10s} {:<8s} {}".format(
        "Index", "Geometry", "Primary", "Scale", "Connector")]
    lines.append("{:<6s} {:<18s} {:<10s} {:<8s} {}".format(
        "-" * 6, "-" * 18, "-" * 10, "-" * 8, "-" * 15))
    for m in monitors:
        geom = "{}x{}+{}+{}".format(m.width, m.height, m.x, m.y)
        primary = "yes" if m.primary else ""
        connector = m.connector or "-"
        lines.append("{:<6d} {:<18s} {:<10s} {:<8d} {}".format(
            m.index, geom, primary, m.scale_factor, connector))
    return "\n".join(lines)


def format_profile_table(profile_set: DisplayProfileSet) -> str:
    """Human-readable table of display profiles."""
    if not profile_set.profiles:
        return "No profiles configured"
    lines = ["{:<16s} {:<8s} {:<22s} {:<8s} {:<10s} {}".format(
        "Name", "Monitor", "Geometry", "Dock", "Edge", "Active")]
    lines.append("{:<16s} {:<8s} {:<22s} {:<8s} {:<10s} {}".format(
        "-" * 16, "-" * 8, "-" * 22, "-" * 8, "-" * 10, "-" * 6))
    for name in profile_set.names():
        p = profile_set.profiles[name]
        geom = "{}x{}+{}+{}".format(
            p.geometry.width, p.geometry.height, p.geometry.x, p.geometry.y)
        active = "*" if name == profile_set.active_profile else ""
        lines.append("{:<16s} {:<8d} {:<22s} {:<8s} {:<10s} {}".format(
            name, p.monitor_index, geom, p.dock_mode, p.dock_edge, active))
    return "\n".join(lines)


def describe_resolution(
    geometry: WindowGeometry,
    monitors: List[MonitorInfo],
    profile: Optional[DisplayProfile] = None,
) -> str:
    """Human-readable description of how a geometry resolves against monitors."""
    resolved = resolve_geometry(geometry, monitors, profile)
    if not monitors:
        return "Geometry {}x{}+{}+{} (no monitor info; headless)".format(
            resolved.width, resolved.height, resolved.x, resolved.y)
    best = find_best_monitor(resolved, monitors)
    if best is None:
        return "Geometry {}x{}+{}+{} (off-screen!)".format(
            resolved.width, resolved.height, resolved.x, resolved.y)
    visible = best.intersection_area(resolved.x, resolved.y, resolved.width, resolved.height)
    total = resolved.width * resolved.height
    pct = round(100 * visible / total) if total > 0 else 0
    return "Geometry {}x{}+{}+{} on monitor {} ({}% visible)".format(
        resolved.width, resolved.height, resolved.x, resolved.y,
        best.index, pct)
