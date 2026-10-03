"""Enumerated monitor actions with a bounded, independent rollback watchdog.

Only X11 RandR and native Sway IPC are supported. An XWayland RandR server
must never be mistaken for the Wayland compositor. No shell, custom modes,
or commands supplied by a model are accepted.

Protocol references:
https://www.x.org/releases/current/doc/man/man1/xrandr.1.xhtml
https://github.com/swaywm/sway/blob/master/sway/sway-output.5.scd
https://github.com/swaywm/sway/blob/master/sway/sway-ipc.7.scd
"""

import copy
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from .action_audit import _sink, record_command
from .process_output import run_bounded


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$")
_OUTPUT = re.compile(r"^(\S+) (connected|disconnected)(.*)$")
_GEOMETRY = re.compile(r"(?:^|\s)(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)(?:\s|$)")
_RATE = re.compile(r"^(\d+(?:\.\d+)?)([*+i]*)$")
_IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
_ROTATIONS = {"normal", "left", "right", "inverted"}
_SWAY_TRANSFORMS = {"normal", "90", "180", "270", "flipped", "flipped-90", "flipped-180", "flipped-270"}


def _number(value, minimum, maximum):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value)
            and minimum <= value <= maximum
        )
    except (OverflowError, TypeError):
        return False


def _fmt(value):
    return format(value, ".9g")


@dataclass(frozen=True)
class DisplayMode:
    output: str
    width: int
    height: int
    refresh: float
    identifier: str
    scale: float = 1.0
    current: bool = False

    def __post_init__(self):
        if not isinstance(self.output, str) or not _NAME.fullmatch(self.output):
            raise ValueError("Invalid monitor identifier.")
        if not isinstance(self.identifier, str) or not _MODE_ID.fullmatch(self.identifier):
            raise ValueError("Invalid native display mode identifier.")
        if type(self.width) is not int or not 1 <= self.width <= 32768:
            raise ValueError("Invalid display width.")
        if type(self.height) is not int or not 1 <= self.height <= 32768:
            raise ValueError("Invalid display height.")
        if not _number(self.refresh, 0.1, 1000):
            raise ValueError("Invalid refresh rate.")
        if not _number(self.scale, 0.25, 8):
            raise ValueError("Invalid display scale.")
        if type(self.current) is not bool:
            raise ValueError("Invalid current display mode flag.")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        required = {"output", "width", "height", "refresh", "identifier"}
        if not isinstance(value, dict) or not required <= value.keys():
            raise ValueError("Invalid display mode record.")
        if value.keys() - (required | {"scale", "current"}):
            raise ValueError("Unknown display mode fields.")
        return cls(**value)

    def _key(self):
        # The current flag is presentation state, not an executable parameter.
        return self.output, self.width, self.height, self.refresh, self.identifier, self.scale


@dataclass
class _Snapshot:
    backend: str
    outputs: Dict[str, dict]
    modes: List[DisplayMode]
    framebuffer: Optional[Tuple[int, int]] = None


def _xrandr_snapshot(query, verbose):
    """Parse separate query and verbose replies; refuse an incomplete layout."""
    outputs = {}
    mode_lines = {}
    framebuffer = None
    output = None
    for line in query.splitlines():
        screen = re.match(r"^Screen \d+: .*current (\d+) x (\d+),", line)
        if screen:
            framebuffer = int(screen[1]), int(screen[2])
        header = _OUTPUT.match(line)
        if header:
            name, connection, rest = header.groups()
            if not _NAME.fullmatch(name) or name in outputs:
                raise ValueError("Unsupported or duplicate RandR output identifier.")
            geometry = _GEOMETRY.search(rest)
            output = name
            outputs[name] = {
                "active": bool(geometry), "connected": connection == "connected",
                "x": int(geometry[3].lstrip("+")) if geometry else 0,
                "y": int(geometry[4].lstrip("+")) if geometry else 0,
                "primary": "primary" in rest.split(), "mode": None,
                "rotation": "normal", "reflect": "normal", "scale": 1.0, "crtc": None,
                "transform": _IDENTITY, "filter": "", "panning": "0x0+0+0/0x0+0+0/0/0/0/0",
            }
            mode_lines[name] = []
        elif line and not line[0].isspace() and not screen:
            raise ValueError("Unsupported RandR output record.")
        elif output and re.match(r"^\s+\S+\s+\d", line):
            fields = line.split()
            rates = []
            for field in fields[1:]:
                if field in {"*", "+", "*+", "i"} and rates:
                    rates[-1] += field
                elif _RATE.fullmatch(field):
                    rates.append(field)
                else:
                    break
            else:
                if rates:
                    mode_lines[output].append([fields[0]] + rates)

    blocks = {}
    output = None
    for line in verbose.splitlines():
        header = _OUTPUT.match(line)
        if header:
            output = header[1]
            if output not in outputs or output in blocks:
                raise ValueError("The monitor list changed during discovery.")
            blocks[output] = [line]
        elif line and not line[0].isspace() and not line.startswith("Screen "):
            raise ValueError("Unsupported verbose RandR output record.")
        elif output:
            blocks[output].append(line)
    if not outputs or set(blocks) != set(outputs) or not framebuffer:
        raise ValueError("A complete RandR output snapshot could not be read.")

    modes = []
    for name, state in outputs.items():
        lines = blocks[name]
        geometry = _GEOMETRY.search(lines[0])
        if ("primary" in lines[0].split()) != state["primary"] or bool(geometry) != state["active"] or (
            geometry and (int(geometry[3].lstrip("+")), int(geometry[4].lstrip("+"))) != (state["x"], state["y"])
        ):
            raise ValueError("The monitor layout changed during discovery.")
        # In verbose output the current rotation/reflection precedes the list
        # of supported rotations. Older nonverbose output omits this field.
        tail = lines[0]
        xid = re.search(r"\(0x[0-9a-fA-F]+\)(.*?)\(", tail)
        if state["active"] and xid:
            current = xid[1].strip().split()
            if not current or current[0] not in _ROTATIONS:
                raise ValueError("Unsupported monitor rotation.")
            state["rotation"] = current[0]
            reflection = " ".join(current[1:]).lower()
            if reflection not in {"", "x axis", "y axis", "x and y axis"}:
                raise ValueError("Unsupported monitor reflection.")
            state["reflect"] = {"": "normal", "x axis": "x", "y axis": "y", "x and y axis": "xy"}[reflection]
        transform_found = False
        native_modes = []
        current_native_ids = []
        native = None
        native_width = None
        panning = {}
        for index, line in enumerate(lines[1:], 1):
            if "Transform:" in line:
                rows = [line.split("Transform:", 1)[1]] + lines[index + 1:index + 3]
                try:
                    matrix = tuple(float(item) for row in rows for item in row.split())
                except ValueError:
                    raise ValueError("Invalid RandR transform.")
                if len(matrix) != 9 or not all(_number(item, -32768, 32768) for item in matrix):
                    raise ValueError("Invalid RandR transform.")
                # RandR stores transforms as 16.16 fixed point, while xrandr
                # prints six decimal places. Recover the original fixed value
                # before sending it back, avoiding a one-LSB truncation drift.
                matrix = tuple(round(item * 65536) / 65536 for item in matrix)
                state["transform"] = matrix
                transform_found = True
                # Scale is informational; the full transform is preserved.
                if _number(abs(matrix[0]), 0.25, 8) and math.isclose(abs(matrix[0]), abs(matrix[4])):
                    state["scale"] = abs(matrix[0])
            if line.strip().startswith("filter:"):
                value = line.split(":", 1)[1].strip()
                if value not in {"", "bilinear", "nearest"}:
                    raise ValueError("This RandR transform filter cannot be restored safely.")
                state["filter"] = value
            crtc = re.match(r"^\s*CRTC:\s*(\d+)\s*$", line)
            if crtc:
                state["crtc"] = int(crtc[1])
            for label in ("Panning", "Tracking", "Border"):
                if line.strip().startswith(label + ":"):
                    panning[label] = line.split(":", 1)[1].strip()
            mode_header = re.match(r"^\s+(\S+) \((0x[0-9a-fA-F]+)\)", line)
            if mode_header:
                native = {"name": mode_header[1], "id": mode_header[2], "current": "*current" in line.split()}
                native_width = None
                if native["current"]:
                    current_native_ids.append(native["id"])
            width = re.search(r"h: width\s+(\d+)", line)
            height = re.search(r"v: height\s+(\d+)", line)
            if width:
                native_width = int(width[1])
            clock = re.search(r"clock\s+(\d+(?:\.\d+)?)Hz", line)
            if height and native is not None and native_width and clock:
                native.update(width=native_width, height=int(height[1]), refresh=float(clock[1]))
                native_modes.append(native)
                native = None
        if state["active"] and (not transform_found or state["crtc"] is None):
            raise ValueError("RandR did not provide the transform and controller needed for safe recovery.")
        if state["active"] and not state["filter"] and state["transform"] != _IDENTITY:
            raise ValueError("This RandR transform cannot be fully restored by xrandr.")
        if panning:
            area = panning.get("Panning", "0x0")
            tracking = panning.get("Tracking", "0x0+0+0")
            border = panning.get("Border", "0/0/0/0")
            area_pattern = r"\d+x\d+(?:[+-]-?\d+[+-]-?\d+)?"
            if not re.fullmatch(area_pattern, area) or not re.fullmatch(area_pattern, tracking):
                raise ValueError("Unsupported RandR panning geometry.")
            if not re.fullmatch(r"-?\d+/-?\d+/-?\d+/-?\d+", border):
                raise ValueError("Unsupported RandR panning borders.")
            if "+" not in area and "-" not in area:
                area += "+0+0"
            if "+" not in tracking and "-" not in tracking:
                tracking += "+0+0"
            state["panning"] = area + "/" + tracking + "/" + border
        used_modes = set()
        for fields in mode_lines[name]:
            for value in fields[1:]:
                match = _RATE.fullmatch(value)
                is_current = "*" in match[2]
                candidates = [item for item in native_modes if item["id"] not in used_modes
                              and item["name"] == fields[0] and item["refresh"] == float(match[1])
                              and item["current"] == is_current]
                if not candidates:
                    raise ValueError("An advertised RandR mode changed during discovery.")
                native = candidates[0]
                used_modes.add(native["id"])
                mode = DisplayMode(name, native["width"], native["height"], native["refresh"], native["id"],
                                   state["scale"], is_current)
                modes.append(mode)
                if mode.current:
                    if state["mode"] is not None:
                        raise ValueError("Ambiguous current RandR mode.")
                    state["mode"] = mode
        if state["active"] and state["mode"] is None:
            raise ValueError("The current RandR mode could not be read.")
        if state["active"] and current_native_ids != [state["mode"].identifier]:
            raise ValueError("The active RandR mode changed during discovery.")
        state["raw"] = tuple(lines)
    return _Snapshot("xrandr", outputs, modes, framebuffer)


def _sway_snapshot(reply):
    raw_outputs = json.loads(reply)
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raise ValueError("Sway did not return a monitor list.")
    outputs = {}
    modes = []
    for raw in raw_outputs:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ValueError("Invalid Sway monitor record.")
        name = raw["name"]
        if not _NAME.fullmatch(name) or name in outputs or type(raw.get("active")) is not bool:
            raise ValueError("Unsupported or duplicate Sway monitor identifier.")
        active = raw["active"]
        state = {"active": active, "mode": None, "raw": copy.deepcopy(raw)}
        if active:
            rect = raw.get("rect", {})
            if not isinstance(rect, dict) or any(type(rect.get(key)) is not int for key in ("x", "y")):
                raise ValueError("Sway did not provide the monitor position.")
            if not _number(raw.get("scale"), 0.25, 8) or raw.get("transform") not in _SWAY_TRANSFORMS:
                raise ValueError("Unsupported Sway scale or transform.")
            state.update(x=rect["x"], y=rect["y"], scale=raw["scale"], transform=raw["transform"],
                         power=raw.get("power", raw.get("dpms", True)))
            if type(state["power"]) is not bool:
                raise ValueError("Invalid Sway monitor power state.")
        else:
            state.update(x=0, y=0, scale=1.0, transform="normal", power=False)
        advertised = raw.get("modes", [])
        if not isinstance(advertised, list):
            raise ValueError("Invalid Sway display modes.")
        current = raw.get("current_mode")
        for value in advertised:
            if not isinstance(value, dict) or type(value.get("refresh")) is not int:
                raise ValueError("Invalid Sway mode record.")
            width, height, refresh = value.get("width"), value.get("height"), value["refresh"] / 1000
            identifier = "{}x{}@{}Hz".format(width, height, _fmt(refresh))
            matches_current = isinstance(current, dict) and all(
                value.get(key) == current.get(key) for key in ("width", "height", "refresh")
            )
            mode = DisplayMode(name, width, height, refresh, identifier, state["scale"],
                               active and matches_current)
            modes.append(mode)
            if mode.current:
                if state["mode"] is not None:
                    raise ValueError("Ambiguous current Sway mode.")
                state["mode"] = mode
        if active and state["mode"] is None:
            raise ValueError("The active Sway mode is not in its advertised mode list.")
        outputs[name] = state
    return _Snapshot("sway", outputs, modes)


def _signature(state):
    if not state["active"]:
        return (False,)
    mode = state["mode"]
    return (
        True, mode.identifier, mode.width, mode.height, round(mode.refresh, 3),
        state["x"], state["y"], round(state["scale"], 6), state["transform"],
        state.get("rotation"), state.get("reflect"), state.get("filter"),
        state.get("panning"), state.get("primary"), state.get("power"),
        state.get("crtc"),
    )


class DisplayChange:
    """One provisional change. All state transitions are owned by the service."""

    def __init__(self, service, before, mode, deadline, is_current):
        self.token = uuid.uuid4().hex
        self.status = "pending"
        self._service = service
        self._before = before
        self._mode = mode
        self._deadline = deadline
        self._is_current = is_current
        self._timer = None
        self._message = ""
        self.audit_sink = _sink.get()

    def audit(self, phase):
        if self.audit_sink:
            try:
                self.audit_sink(phase, self._message)
            except (OSError, ValueError):
                pass

    def confirm(self):
        return self._service._finish(self, keep=True)

    @property
    def remaining_seconds(self):
        return max(0.0, self._deadline - self._service._clock()) if self.status == "pending" else 0.0

    def revert(self):
        return self._service._finish(self, keep=False)

    def close(self):
        if self.status == "pending":
            return self.revert()
        return self.status != "failed", self._message


class DisplayService:
    def __init__(self, runner=None, which=shutil.which, environ=None, *, timer_factory=None, clock=None):
        self.environ = dict(os.environ if environ is None else environ)
        self._runner = runner or self._run
        self._which = which
        self._timer_factory = timer_factory or threading.Timer
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._pending = None
        self._closed = False
        self._failed_recovery = ""

    def _run(self, argv, timeout=8):
        env = dict(self.environ, LC_ALL="C")
        try:
            code, stdout, stderr = run_bounded(argv, timeout, 1_000_000, env=env)
        except subprocess.TimeoutExpired:
            return False, 'Monitor command timed out; check the current monitor state.'
        except OSError as exc:
            return False, str(exc)
        return code == 0, (stdout + stderr).strip()

    def _call(self, argv):
        try:
            ok, text = self._runner(list(argv), timeout=8)
            record_command(argv, bool(ok))
            return bool(ok), str(text or "")
        except Exception as exc:
            return False, str(exc)

    def _backend(self):
        wayland = (
            self.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(self.environ.get("WAYLAND_DISPLAY")) or bool(self.environ.get("SWAYSOCK"))
        )
        if wayland:
            if self.environ.get("SWAYSOCK") and self._which("swaymsg"):
                return "sway", ""
            return "", "This Wayland compositor is not supported yet. Native Sway IPC is required."
        if self.environ.get("DISPLAY") and self._which("xrandr"):
            return "xrandr", ""
        return "", "Monitor configuration requires an X11 display with xrandr, or a Sway session."

    def _discover(self):
        backend, error = self._backend()
        if error:
            return None, error
        try:
            if backend == "sway":
                ok, text = self._call(["swaymsg", "-r", "-t", "get_outputs"])
                if not ok:
                    return None, text or "Sway monitor discovery failed."
                return _sway_snapshot(text), ""
            ok, query = self._call(["xrandr", "--query"])
            if not ok:
                return None, query or "RandR monitor discovery failed."
            ok, verbose = self._call(["xrandr", "--verbose"])
            if not ok:
                return None, verbose or "RandR monitor snapshot failed."
            return _xrandr_snapshot(query, verbose), ""
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return None, "Cannot safely read the display configuration: " + str(exc)

    def list_modes(self):
        with self._lock:
            if self._closed:
                return [], "The display service is closed."
            snapshot, error = self._discover()
            return (snapshot.modes, "") if snapshot else ([], error)

    @staticmethod
    def _allowed(callback):
        if callback is None:
            return True
        try:
            return bool(callback())
        except Exception:
            return False

    def _sway_command(self, command):
        ok, text = self._call(["swaymsg", "-r", "-t", "command", command])
        if not ok:
            return False, text or "Sway rejected the display command."
        try:
            replies = json.loads(text)
            if not isinstance(replies, list) or not replies:
                raise ValueError("Missing Sway command reply.")
            if any(not isinstance(reply, dict) or reply.get("success") is not True for reply in replies):
                errors = [str(reply.get("error", "Command failed")) for reply in replies if isinstance(reply, dict)]
                return False, "; ".join(errors) or "Sway rejected the display command."
        except (ValueError, TypeError):
            return False, "Invalid Sway command reply."
        return True, ""

    def _apply(self, snapshot, mode):
        if snapshot.backend == "sway":
            return self._sway_command("output {} mode {}".format(mode.output, mode.identifier))
        return self._call(["xrandr", "--output", mode.output, "--mode", mode.identifier,
                           "--rate", _fmt(mode.refresh), "--crtc", str(snapshot.outputs[mode.output]["crtc"])])

    def _verify(self, before, mode=None):
        current, error = self._discover()
        if current is None:
            return False, error
        if current.backend != before.backend or current.outputs.keys() != before.outputs.keys():
            return False, "The monitor set changed."
        for name, saved in before.outputs.items():
            expected = dict(saved)
            if mode is not None and name == mode.output:
                expected["mode"] = mode
            if _signature(current.outputs[name]) != _signature(expected):
                return False, "The active configuration for {} does not match the requested state.".format(name)
        if mode is None and current.framebuffer != before.framebuffer:
            return False, "The RandR framebuffer was not restored."
        return True, ""

    def _restore(self, before):
        if before.backend == "sway":
            errors = []
            for name, saved in before.outputs.items():
                if saved["active"]:
                    mode = saved["mode"]
                    command = "output {} enable mode {} pos {} {} scale {} transform {} power {}".format(
                        name, mode.identifier, saved["x"], saved["y"], _fmt(saved["scale"]),
                        saved["transform"], "on" if saved["power"] else "off",
                    )
                else:
                    command = "output {} disable".format(name)
                ok, text = self._sway_command(command)
                if not ok:
                    errors.append(text)
            if errors:
                return False, "; ".join(errors)
        else:
            argv = ["xrandr", "--fb", "{}x{}".format(*before.framebuffer)]
            # --noprimary is a global override in xrandr, even when a later
            # --primary appears. Use it only when the snapshot had no primary.
            if not any(state["primary"] for state in before.outputs.values()):
                argv.append("--noprimary")
            for name, saved in before.outputs.items():
                argv.extend(["--output", name])
                if not saved["active"]:
                    argv.append("--off")
                    continue
                mode = saved["mode"]
                argv.extend([
                    "--mode", mode.identifier, "--rate", _fmt(mode.refresh),
                    "--crtc", str(saved["crtc"]),
                    "--pos", "{}x{}".format(saved["x"], saved["y"]),
                    "--rotate", saved["rotation"], "--reflect", saved["reflect"],
                    "--transform", ("none" if saved["transform"] == _IDENTITY and not saved["filter"]
                                    else ",".join(format(item, ".17g") for item in saved["transform"])),
                    "--panning", saved["panning"],
                ])
                # --filter is global to all outputs marked changes_filter in
                # xrandr. Bilinear is already the default of --transform;
                # explicitly marking only nearest avoids changing a peer's
                # different filter during the full-layout restore.
                if saved["filter"] == "nearest":
                    argv.extend(["--filter", saved["filter"]])
                if saved["primary"]:
                    argv.append("--primary")
            ok, text = self._call(argv)
            if not ok:
                return False, text or "RandR could not restore the saved layout."
        return self._verify(before)

    def _rollback(self, change):
        token = _sink.set(change.audit_sink)
        try:
            return self._rollback_impl(change)
        finally:
            _sink.reset(token)

    def _rollback_impl(self, change):
        if change._timer is not None:
            change._timer.cancel()
        ok, text = self._restore(change._before)
        change.status = "reverted" if ok else "failed"
        change._message = (
            "The previous display configuration was restored." if ok else
            "The previous display configuration could not be fully restored: " + text
        )
        if not ok:
            self._failed_recovery = change._message
        change.audit('reverted' if ok else 'recovery_failed')
        if self._pending is change:
            self._pending = None
        return ok, change._message

    def apply_mode(self, mode, timeout=15, is_current=None):
        with self._lock:
            if self._closed:
                return None, "The display service is closed."
            if self._failed_recovery:
                return None, self._failed_recovery + " Check the monitors in the desktop settings and restart the assistant before retrying."
            if not isinstance(mode, DisplayMode) or not _number(timeout, 1, 120):
                return None, "Invalid monitor mode or confirmation timeout."
            if not self._allowed(is_current):
                return None, "The display request was cancelled."
            if self._pending is not None:
                ok, text = self._rollback(self._pending)
                if not ok:
                    return None, text
            before, error = self._discover()
            if before is None:
                return None, error
            selected = next((item for item in before.modes if item._key() == mode._key()), None)
            if selected is None:
                return None, "This mode is no longer available. Query the monitor options again."
            if not before.outputs[mode.output]["active"]:
                return None, "Enable this monitor in the desktop settings before changing its mode."
            if not self._allowed(is_current):
                return None, "The display request was cancelled."
            change = DisplayChange(self, before, selected, self._clock() + timeout, is_current)
            self._pending = change
            try:
                change._timer = self._timer_factory(timeout, lambda: self._finish(change, keep=False))
                # A CLI process must remain alive until its unconfirmed change
                # has been restored, even when stdin closes or the GUI stops.
                change._timer.daemon = False
                change._timer.start()
            except Exception as exc:
                if change._timer is not None:
                    change._timer.cancel()
                self._pending = None
                change.status = "failed"
                return None, "Could not start the display recovery timer: " + str(exc)
            if not self._allowed(is_current):
                change._timer.cancel()
                change.status = "reverted"
                self._pending = None
                return None, "The display request was cancelled."
            ok, error = self._apply(before, selected)
            if ok:
                ok, error = self._verify(before, selected)
            if ok and not self._allowed(is_current):
                ok, error = False, "The display request was cancelled."
            if ok and self._clock() >= change._deadline:
                ok, error = False, "The display confirmation period expired."
            if not ok:
                _, rollback_text = self._rollback(change)
                return None, (error or "The display mode could not be applied.") + " " + rollback_text
            return change, ""

    def _finish(self, change, keep):
        token = _sink.set(change.audit_sink)
        try:
            return self._finish_impl(change, keep)
        finally:
            _sink.reset(token)

    def _finish_impl(self, change, keep):
        with self._lock:
            if change.status != "pending":
                return (change.status == ("kept" if keep else "reverted")), change._message
            if self._pending is not change:
                return False, "This display change is no longer current."
            if not keep:
                return self._rollback(change)
            if self._clock() >= change._deadline or not self._allowed(change._is_current):
                _, text = self._rollback(change)
                return False, "The confirmation period expired or the request was cancelled. " + text
            ok, error = self._verify(change._before, change._mode)
            if not ok:
                _, text = self._rollback(change)
                return False, error + " " + text
            # Discovery can be slow: check the deadline again while holding the
            # same lock, so a late reply cannot defeat the watchdog.
            if self._clock() >= change._deadline or not self._allowed(change._is_current):
                _, text = self._rollback(change)
                return False, "The confirmation period expired or the request was cancelled. " + text
            change._timer.cancel()
            change.status = "kept"
            change._message = "The display configuration was kept."
            change.audit('confirmed')
            self._pending = None
            return True, change._message

    def close(self):
        with self._lock:
            self._closed = True
            if self._pending is not None:
                return self._rollback(self._pending)
            if self._failed_recovery:
                return False, self._failed_recovery
            return True, ""
