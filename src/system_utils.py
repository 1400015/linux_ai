import os
import json
import getpass
import shutil
import shlex
import subprocess
import platform
import time
from typing import Dict, List, Tuple, Any, Optional
from pathlib import Path
import logging

# Configurar logger
logger = logging.getLogger(__name__)

from .command_policy import validate_arguments
from .process_output import run_bounded

# Maximum bytes retained from a diagnostic process.
MAX_COMMAND_OUTPUT = 1_000_000


def _cpu_value(entry: Dict[str, str]) -> float:
    """Sort key for the process list.

    `ps` sometimes prints "?" in the %CPU column (kernel threads, some
    locales); the old inline `float(...)` raised ValueError outside the
    try/except and broke the whole listing.
    """
    try:
        return float(str(entry.get("cpu", "0")).replace("%", ""))
    except (TypeError, ValueError):
        return 0.0


class SystemUtils:
    """Utilities to interact with the Linux system"""

    def __init__(self, config_manager):
        self.config = config_manager
        self._detect_environment()

    def _permission_set(self, key: str) -> set:
        """Read a permission list from the config, ignoring bad values.

        A string here (hand-edited config, or a bad environment override) used
        to become a set of single characters, silently breaking the allowlist.
        """
        value = self.config.get(key, [])
        if not isinstance(value, (list, tuple, set)):
            logger.warning("Ignoring non-list value for %s", key)
            return set()
        return set(value)

    @property
    def allowed_commands(self) -> set:
        """Commands the user may run.

        Re-read on every access so editing `permissions.allowed_commands`
        (or the matching env var) applies without restarting the app.
        """
        return self._permission_set("permissions.allowed_commands")

    @property
    def allowed_edit_dirs(self) -> set:
        """Directories the assistant may read/write (re-read on access)."""
        return self._permission_set("permissions.allowed_edit_dirs")

    def _detect_environment(self):
        """Detect the environment (X11 vs Wayland) and other system properties"""
        self.is_wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        self.is_x11 = os.environ.get("DISPLAY") is not None
        self.is_root = os.geteuid() == 0
        self.username = getpass.getuser()

        logger.info(f"Environment detected: {'Wayland' if self.is_wayland else 'X11'}")
        logger.info(f"User: {self.username}, Root: {self.is_root}")

    def _sanitize_command(self, command: str) -> List[str]:
        """Sanitize command to prevent command injection"""
        try:
            # Use shlex for safe splitting
            return shlex.split(command)
        except ValueError as e:
            logger.error(f"Invalid command to sanitize: {command} - {e}")
            return []

    def _validate_path(self, path: str) -> bool:
        """Validate if a path is inside one of the allowed directories.

        Both sides are resolved with `realpath` so that relative components
        ("/etc/../root") and symlinks cannot escape, and compared with
        `commonpath` so that a sibling sharing a name prefix
        ("/etcfoo" vs "/etc") is not accepted.
        """
        try:
            real = os.path.realpath(os.path.expanduser(path))

            for allowed_dir in self.allowed_edit_dirs:
                allowed_real = os.path.realpath(os.path.expanduser(allowed_dir))
                try:
                    if os.path.commonpath([real, allowed_real]) == allowed_real:
                        return True
                except ValueError:
                    # Paths on different roots (e.g. separate mounts)
                    continue

            logger.warning(f"Path not allowed: {path}")
            return False
        except Exception as e:
            logger.error(f"Error validating path: {e}")
            return False

    def _validate_file_args(self, cmd_parts: List[str]) -> bool:
        """Validate filename operands and all supported dangerous flag forms."""
        return validate_arguments(cmd_parts, self._validate_path)

    def get_system_info(self) -> Dict[str, str]:
        """Get system information"""
        info = {}

        try:
            # Basic information
            info["os"] = platform.system()
            info["release"] = platform.release()
            info["version"] = platform.version()
            info["machine"] = platform.machine()
            info["processor"] = platform.processor()
            info["architecture"] = platform.architecture()[0]

            # Detect if it is Void Linux or d77void
            info["is_void"] = False
            info["is_d77void"] = False
            try:
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if line.startswith('ID='):
                            info["distro_id"] = line.split('=')[1].strip().strip('"')
                            if info["distro_id"] == "void":
                                info["is_void"] = True
                        elif line.startswith('D77VOID_VERSION='):
                            info["is_d77void"] = True
            except Exception as e:
                logger.warning(f"Erro a read /etc/os-release: {e}")

            # Memory
            try:
                import psutil
                mem = psutil.virtual_memory()
                info["memory_total"] = f"{mem.total / (1024**3):.2f} GB"
                info["memory_used"] = f"{mem.used / (1024**3):.2f} GB"
                info["memory_percent"] = f"{mem.percent}%"
                info["memory_available"] = f"{mem.available / (1024**3):.2f} GB"
            except ImportError:
                # Fallback without psutil
                try:
                    with open('/proc/meminfo', 'r') as f:
                        for line in f:
                            if line.startswith('MemTotal:'):
                                total_kb = int(line.split()[1])
                                info["memory_total"] = f"{total_kb / (1024**2):.2f} GB"
                            elif line.startswith('MemFree:'):
                                free_kb = int(line.split()[1])
                                info["memory_available"] = f"{free_kb / (1024**2):.2f} GB"
                except Exception as e:
                    logger.warning(f"Could not get memory info: {e}")

            # CPU
            try:
                import psutil
                info["cpu_cores"] = str(psutil.cpu_count(logical=True))
                info["cpu_physical_cores"] = str(psutil.cpu_count(logical=False))
                # interval=None: non-blocking sample. The old interval=1
                # stalled the caller (GTK main loop) for a full second.
                info["cpu_usage"] = f"{psutil.cpu_percent(interval=None)}%"
                _freq = psutil.cpu_freq()
                info["cpu_freq"] = f"{_freq.current:.2f} MHz" if _freq is not None and hasattr(_freq, 'current') else "N/A"
            except ImportError:
                try:
                    with open('/proc/cpuinfo', 'r') as f:
                        cores = 0
                        for line in f:
                            if line.startswith('processor'):
                                cores += 1
                        info["cpu_cores"] = str(cores)
                except Exception as e:
                    logger.warning(f"Could not get CPU info: {e}")

            # Disk
            try:
                import psutil
                disk = psutil.disk_usage('/')
                info["disk_total"] = f"{disk.total / (1024**3):.2f} GB"
                info["disk_used"] = f"{disk.used / (1024**3):.2f} GB"
                info["disk_percent"] = f"{disk.percent}%"
                info["disk_free"] = f"{disk.free / (1024**3):.2f} GB"
            except ImportError:
                try:
                    stat = os.statvfs('/')
                    info["disk_total"] = f"{(stat.f_blocks * stat.f_frsize) / (1024**3):.2f} GB"
                    info["disk_free"] = f"{(stat.f_bavail * stat.f_frsize) / (1024**3):.2f} GB"
                except Exception as e:
                    logger.warning(f"Could not get disk info: {e}")

            # Uptime
            try:
                import psutil
                uptime_seconds = int(time.time() - psutil.boot_time())
            except ImportError:
                try:
                    with open('/proc/uptime', 'r') as f:
                        uptime_seconds = int(float(f.readline().split()[0]))
                except Exception as e:
                    logger.warning(f"Could not get uptime: {e}")
                    uptime_seconds = 0

            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            info["uptime"] = f"{days}d {hours}h {minutes}m {seconds}s"
            info["uptime_seconds"] = str(uptime_seconds)

            # User
            try:
                info["username"] = os.getlogin()
            except OSError:
                # getlogin() fails without a controlling terminal
                # (cron, systemd services, containers).
                info["username"] = os.environ.get("USER", "unknown")

            info["hostname"] = platform.node()
            # `is_root` is computed in _detect_environment(); expose it here so
            # `cli.py system-info` shows the real value instead of False.
            info["is_root"] = str(self.is_root)

            # Graphical environment
            info["is_wayland"] = str(self.is_wayland)
            info["is_x11"] = str(self.is_x11)
            info["display"] = os.environ.get("DISPLAY", "N/A")
            info["xdg_session_type"] = os.environ.get("XDG_SESSION_TYPE", "N/A")

            # Linux distribution
            try:
                with open('/etc/os-release', 'r') as f:
                    for line in f:
                        if line.startswith('PRETTY_NAME='):
                            info["distro"] = line.split('=')[1].strip().strip('"')
                            break
                    else:
                        info["distro"] = "Unknown"
            except OSError:
                info["distro"] = "Unknown"

            return info
        except Exception as e:
            logger.error(f"Error getting system info: {e}")
            return {"error": str(e)}

    def execute_command(self, command, timeout: int = 10) -> Tuple[bool, str]:
        """
        Run a command on the system with sanitization.

        Args:
            command: Command to run — string (dividida com shlex) OU lista
                argv já dividida (o CLI passa `args.command`, que é a lista
                pós-shell; juntar com espaços e re-dividir com shlex partia
                argumentos que contêm espaços).
            timeout: Timeout in seconds.

        Returns:
            Tuple (success, output).
        """
        if isinstance(command, (list, tuple)):
            cmd_parts = [str(part) for part in command if str(part).strip()]
            if not cmd_parts:
                logger.error("Invalid command (empty argv)")
                return False, "Invalid command"
        elif isinstance(command, str) and command.strip():
            # Sanitize command
            try:
                cmd_parts = self._sanitize_command(command)
                if not cmd_parts:
                    return False, "Invalid command after sanitization"
            except Exception as e:
                logger.error(f"Error sanitizing command: {e}")
                return False, f"Error processing command: {e}"
        else:
            logger.error("Invalid command")
            return False, "Invalid command"

        cmd_base = cmd_parts[0]

        # Check if the command is allowed
        if cmd_base not in self.allowed_commands:
            logger.warning(f"Command not allowed: {cmd_base}")
            return False, f"Command not allowed: {cmd_base}"

        # Flags perigosas + caminhos absolutos fora da sandbox (TODOS os
        # comandos da allowlist, ver _validate_file_args)
        if not self._validate_file_args(cmd_parts):
            return False, f"Path not allowed: {cmd_base}"

        try:
            logger.info(f"Running command: {' '.join(cmd_parts)}")

            returncode, stdout, stderr = run_bounded(cmd_parts, timeout, MAX_COMMAND_OUTPUT)
            if returncode == 0:
                return True, stdout
            return False, stderr or stdout

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout running command: {cmd_base}")
            return False, f"Timeout running command: {cmd_base}"
        except Exception as e:
            logger.error(f"Error running command: {e}")
            return False, f"Error running command: {e}"

    def read_file(self, filepath: str, max_lines: int = 100) -> Tuple[bool, str]:
        """
        Read a file from the system.

        Args:
            filepath: Path of the file.
            max_lines: Maximum number of lines to read.

        Returns:
            Tuple (success, content).
        """
        if not filepath or not isinstance(filepath, str):
            return False, "Invalid path"

        # Validate path
        if not self._validate_path(filepath):
            logger.warning(f"Access denied to file: {filepath}")
            return False, f"Access denied to file: {filepath}"

        try:
            file_path = Path(filepath)
            if not file_path.exists():
                return False, f"File not found: {filepath}"

            # Check if it is a directory
            if file_path.is_dir():
                return False, f"It is a directory: {filepath}"

            # Check permissions
            if not os.access(filepath, os.R_OK):
                return False, f"No permission to read: {filepath}"

            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = []
                # Cap each line: a single-line file (minified JSON, a log
                # without newlines) could otherwise be read whole into RAM.
                max_line_chars = 10000
                for i in range(max_lines + 1):
                    line = f.readline(max_line_chars + 1)
                    if not line:
                        break
                    if i >= max_lines:
                        lines.append(f"\n... (more than {max_lines} lines omitted)")
                        break
                    if len(line) > max_line_chars and not line.endswith('\n'):
                        line = line[:max_line_chars] + "... (line truncated)\n"
                        while True:
                            rest = f.readline(max_line_chars)
                            if not rest or rest.endswith('\n'):
                                break
                    lines.append(line)

            return True, ''.join(lines)
        except PermissionError:
            logger.error(f"Permission denied to read: {filepath}")
            return False, f"Permission denied to read: {filepath}"
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return False, f"Error reading file: {e}"

    def write_file(self, filepath: str, content: str, append: bool = False) -> Tuple[bool, str]:
        """
        Write to a file on the system.

        Args:
            filepath: Path of the file.
            content: Content to write.
            append: If True, appends to the file; otherwise, replaces it.

        Returns:
            Tuple (success, message).
        """
        if not filepath or not isinstance(filepath, str):
            return False, "Invalid path"

        if not content or not isinstance(content, str):
            return False, "Invalid content"

        # Validate path
        if not self._validate_path(filepath):
            logger.warning(f"Access denied to file: {filepath}")
            return False, f"Access denied to file: {filepath}"

        try:
            file_path = Path(filepath)

            # Create the parent directories first: os.access() on a
            # non-existent directory reports "not writable" even when we are
            # able to create it, so the old check blocked writes to any new
            # nested path.
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return False, f"Error creating directory {file_path.parent}: {e}"

            # Check permissions
            if not os.access(file_path.parent, os.W_OK):
                return False, f"No permission to write in: {file_path.parent}"

            mode = 'a' if append else 'w'
            with open(file_path, mode, encoding='utf-8') as f:
                f.write(content)

            logger.info(f"File {filepath} {'updated' if not append else 'appended'} successfully")
            return True, f"File {filepath} {'updated' if not append else 'appended'} successfully"
        except PermissionError:
            logger.error(f"Permission denied to write: {filepath}")
            return False, f"Permission denied to write: {filepath}"
        except Exception as e:
            logger.error(f"Error writing file: {e}")
            return False, f"Error writing file: {e}"

    @staticmethod
    def _which(name: str) -> Optional[str]:
        """shutil.which com timeout — sem isso `which` podia bloquear indefinidamente."""
        try:
            return shutil.which(name)
        except Exception:
            return None

    def _wayland_focused_geometry(self) -> Optional[str]:
        """Geometria "x,y WxH" da janela focada (sway/wlroots), se possível."""
        if self._which("swaymsg") is None:
            return None
        try:
            result = subprocess.run(
                ["swaymsg", "-t", "get_tree", "-r"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            tree = json.loads(result.stdout)

            def _find_focused(node):
                if node.get("focused"):
                    return node
                for child in node.get("nodes", []) + node.get("floating_nodes", []):
                    found = _find_focused(child)
                    if found:
                        return found
                return None

            focused = _find_focused(tree)
            if not focused:
                return None
            rect = focused.get("rect") or {}
            x, y = rect.get("x"), rect.get("y")
            w, h = rect.get("width"), rect.get("height")
            if None in (x, y, w, h) or w <= 0 or h <= 0:
                return None
            return f"{x},{y} {w}x{h}"
        except Exception as e:
            logger.warning(f"Could not get focused window geometry: {e}")
            return None

    def _wayland_selected_geometry(self) -> Optional[str]:
        """Geometry "x,y WxH" picked interactively with slurp, if available.

        Fallback for compositors that do not expose the focused window
        (e.g. dwl-based ones): the user drags over the area to capture.
        """
        slurp = self._which("slurp")
        if slurp is None:
            return None
        try:
            result = subprocess.run(
                [slurp], capture_output=True, text=True, timeout=60,
            )
            # slurp exits non-zero when the selection is cancelled (Esc).
            if result.returncode != 0:
                logger.warning(
                    f"slurp exited with {result.returncode}: "
                    f"{result.stderr.strip()!r}"
                )
                return None
            geometry = result.stdout.strip()
            return geometry or None
        except Exception as e:
            logger.warning(f"Could not get selection from slurp: {e}")
            return None

    def capture_screen(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capture the screen with support for Wayland and X11.

        Args:
            output_path: Path to save the capture (optional).

        Returns:
            Tuple (success, image_path).
        """
        temp_path = None
        try:
            if output_path is None:
                import tempfile
                fd, temp_path = tempfile.mkstemp(suffix='.png')
                os.close(fd)
                output_path = temp_path

            # Wayland: grim. NB: a flag antiga `grim -o <ficheiro>` está
            # ERRADA — `-o/--output` escolhe o MONITOR, não o ficheiro; a
            # captura primária em Wayland falhava sempre e caía no scrot
            # (que não funciona em Wayland).
            grim = self._which("grim")
            if self.is_wayland and grim:
                result = subprocess.run([grim, output_path],
                                        capture_output=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"Screen captured with grim: {output_path}")
                    return True, output_path
                logger.warning(f"grim failed: {result.stderr!r}")

            # X11: scrot
            scrot = self._which("scrot")
            if scrot and not self.is_wayland:
                result = subprocess.run([scrot, output_path],
                                        capture_output=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"Screen captured with scrot: {output_path}")
                    return True, output_path

            # gnome-screenshot (último recurso, ambos os servidores gráficos)
            gnome = self._which("gnome-screenshot")
            if gnome:
                result = subprocess.run([gnome, "-f", output_path],
                                        capture_output=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"Screen captured with gnome-screenshot: {output_path}")
                    return True, output_path

            logger.error("No screen capture utility found")
            return False, "No screen capture utility found (install scrot, grim or gnome-screenshot)"

        except subprocess.TimeoutExpired:
            logger.error("Timeout capturing screen")
            return False, "Timeout capturing screen"
        except Exception as e:
            logger.error(f"Error capturing screen: {e}")
            return False, f"Error capturing screen: {e}"
        finally:
            # Falha => apagar o temporário (antes, cada captura falhada em
            # Wayland deixava um PNG vazio para trás). No sucesso o ficheiro
            # fica para o chamador (OCR) apagar.
            if temp_path is not None and os.path.exists(temp_path):
                if os.path.getsize(temp_path) == 0:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    def capture_active_window(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capture the active window with support for Wayland and X11.

        Args:
            output_path: Path to save the capture.

        Returns:
            Tuple (success, image_path).
        """
        temp_path = None
        try:
            if output_path is None:
                import tempfile
                fd, temp_path = tempfile.mkstemp(suffix='.png')
                os.close(fd)
                output_path = temp_path

            if self.is_wayland:
                # slurp NÃO produz imagens: é um seletor interativo de
                # geometria que imprime coordenadas — o caminho antigo
                # tratava returncode 0 como "captura guardada" e nunca
                # havia PNG. Obter a geometria da janela focada e usar
                # `grim -g`.
                grim = self._which("grim")
                geometry = None
                if grim:
                    geometry = (self._wayland_focused_geometry()
                                or self._wayland_selected_geometry())
                if grim and geometry:
                    result = subprocess.run(
                        [grim, "-g", geometry, output_path],
                        capture_output=True, timeout=10,
                    )
                    if result.returncode == 0:
                        logger.info(
                            f"Active window captured with grim -g: {output_path}"
                        )
                        return True, output_path
                    logger.warning(f"grim -g failed: {result.stderr!r}")
                logger.error(
                    "Active-window capture on Wayland needs grim plus "
                    "swaymsg or slurp (selection may have been cancelled)"
                )
                return False, (
                    "Active window capture unavailable on Wayland "
                    "(install grim and slurp, or use a swaymsg-compatible "
                    "compositor; selection may have been cancelled)"
                )

            scrot = self._which("scrot")
            if scrot:
                result = subprocess.run([scrot, "-u", output_path],
                                        capture_output=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"Active window captured with scrot: {output_path}")
                    return True, output_path
                logger.warning(f"scrot -u failed: {result.stderr!r}")

            logger.error("No window capture utility found")
            return False, "No window capture utility found (install scrot)"

        except subprocess.TimeoutExpired:
            logger.error("Timeout capturing active window")
            return False, "Timeout capturing active window"
        except Exception as e:
            logger.error(f"Error capturing active window: {e}")
            return False, f"Error capturing active window: {e}"
        finally:
            if temp_path is not None and os.path.exists(temp_path):
                if os.path.getsize(temp_path) == 0:
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass

    def get_active_window_info(self) -> Dict[str, str]:
        """Get information about the active window."""
        info = {}

        try:
            if self.is_wayland:
                # Try using swaymsg for Wayland (Sway)
                if subprocess.run(['which', 'swaymsg'], capture_output=True).returncode == 0:
                    result = subprocess.run(['swaymsg', '-t', 'get_tree'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        try:
                            import json
                            tree = json.loads(result.stdout)
                            if tree and 'nodes' in tree:
                                # Find active window
                                for node in tree.get('nodes', []):
                                    if node.get('focused', False):
                                        info['title'] = node.get('name', 'Unknown')
                                        info['app_id'] = node.get('app_id', 'Unknown')
                                        break
                        except Exception as e:
                            logger.warning(f"Error parsing swaymsg: {e}")

                # Try using wl-focus for Wayland
                if subprocess.run(['which', 'wl-focus'], capture_output=True).returncode == 0:
                    result = subprocess.run(['wl-focus', '--get'],
                                          capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        info['window_id'] = result.stdout.strip()
            else:
                # Use xdotool for X11
                if subprocess.run(['which', 'xdotool'], capture_output=True).returncode == 0:
                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowname'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["title"] = result.stdout.strip()

                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowclass'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["class"] = result.stdout.strip()

                    result = subprocess.run(
                        ['xdotool', 'getactivewindow', 'getwindowgeometry'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["geometry"] = result.stdout.strip()

                # Use xprop to get more info
                if subprocess.run(['which', 'xprop'], capture_output=True).returncode == 0:
                    result = subprocess.run(
                        ['xprop', '-root', '_NET_ACTIVE_WINDOW'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        info["window_id"] = result.stdout.strip()

        except Exception as e:
            logger.warning(f"Error getting active window info: {e}")
            info["error"] = str(e)

        return info

    def extract_text_from_image(self, image_path: str, lang: str = "por+eng") -> Tuple[bool, str]:
        """
        Extract text from an image using OCR with fallback.

        Args:
            image_path: Path to the image.
            lang: Languages for OCR (e.g. "por+eng").

        Returns:
            Tuple (success, extracted_text).
        """
        try:
            import pytesseract
            from PIL import Image

            pytesseract.pytesseract.tesseract_cmd = (
                shutil.which("tesseract") or shutil.which("tesseract-ocr") or "tesseract"
            )

            # Check if file exists
            if not os.path.exists(image_path):
                return False, f"File not found: {image_path}"

            # Open image
            img = Image.open(image_path)

            # Extract text
            try:
                # timeout: pytesseract lança um subprocesso sem timeout
                # próprio — um tesseract bloqueado congelava a thread para
                # sempre (o fallback binário já usava timeout=30).
                text = pytesseract.image_to_string(img, lang=lang, timeout=30)

                # If no text is obtained, try with English only
                if not text.strip():
                    logger.info("No text detected. Trying with English...")
                    text = pytesseract.image_to_string(img, lang='eng', timeout=30)

                if text.strip():
                    logger.info(f"Text extracted successfully ({len(text)} characters)")
                    return True, text.strip()
                else:
                    logger.warning("No text detected in the image")
                    return True, ""

            except Exception as e:
                logger.error(f"pytesseract error: {e}")
                return False, f"OCR error: {e}"

        except ImportError:
            binary = shutil.which("tesseract") or shutil.which("tesseract-ocr")
            if not binary:
                return False, "Tesseract OCR is not installed"
            try:
                result = subprocess.run(
                    [binary, image_path, "stdout", "-l", lang],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0:
                    return False, result.stderr.strip()
                return True, result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired) as e:
                return False, f"OCR failed: {e}"
        except Exception as e:
            logger.error(f"Error extracting text: {e}")
            return False, f"Error extracting text: {e}"

    def get_process_list(self) -> List[Dict[str, str]]:
        """Get the list of running processes."""
        processes = []

        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
                try:
                    info = proc.info
                    processes.append({
                        "pid": str(info['pid']),
                        "name": info['name'] or "Unknown",
                        "user": info['username'] or "Unknown",
                        "cpu": f"{info['cpu_percent']:.1f}%",
                        "memory": f"{info['memory_percent']:.1f}%"
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            # Fallback without psutil
            try:
                result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    for line in result.stdout.split('\n')[1:]:  # Skip header
                        parts = line.split()
                        if len(parts) >= 11:
                            processes.append({
                                "pid": parts[1],
                                "name": parts[10],
                                "user": parts[0],
                                "cpu": parts[2],
                                "memory": parts[3]
                            })
            except Exception as e:
                logger.warning(f"Could not get process list: {e}")

        return sorted(processes, key=_cpu_value, reverse=True)

    def get_network_info(self) -> Dict[str, Any]:
        """Get network information."""
        info = {}

        try:
            import psutil
            # Network interfaces
            net_io = psutil.net_io_counters(pernic=True)
            info["interfaces"] = {}
            for interface, data in net_io.items():
                info["interfaces"][interface] = {
                    "bytes_sent": f"{data.bytes_sent / 1024:.2f} KB",
                    "bytes_recv": f"{data.bytes_recv / 1024:.2f} KB",
                    "packets_sent": data.packets_sent,
                    "packets_recv": data.packets_recv
                }

            # Connections
            connections = psutil.net_connections(kind='inet')
            info["connections"] = []
            for conn in connections:
                if conn.status == 'ESTABLISHED':
                    info["connections"].append({
                        "local_addr": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A",
                        "remote_addr": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A",
                        "status": conn.status,
                        "pid": conn.pid
                    })
        except ImportError:
            # Fallback without psutil
            try:
                result = subprocess.run(['ip', 'addr'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    info["interfaces"] = {"output": result.stdout}

                result = subprocess.run(['ss', '-tuln'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    info["connections"] = result.stdout.split('\n')
            except Exception as e:
                logger.warning(f"Could not get network info: {e}")
        except Exception as e:
            logger.error(f"Error getting network info: {e}")
            info["error"] = str(e)

        return info

    def search_files(self, search_term: str, search_path: str = "/", max_results: int = 20) -> List[str]:
        """
        Search for files on the system.

        Args:
            search_term: Term to search for.
            search_path: Directory to search in.
            max_results: Maximum number of results.

        Returns:
            List of file paths.
        """

        results = []

        if not search_term or not isinstance(search_term, str):
            return results

        try:
            max_results = max(1, int(max_results))
        except (TypeError, ValueError):
            max_results = 20

        try:
            root = os.path.realpath(os.path.expanduser(search_path))
            if not os.path.isdir(root):
                logger.warning(f"Invalid directory for search: {search_path}")
                return results

            # Escape find(1)'s own wildcards so a term containing `*`, `?`,
            # `[` or `\` is matched literally (the shell is already bypassed
            # by passing the arguments as a list).
            escaped_term = "".join(
                ("\\" + ch) if ch in "*?[\\" else ch for ch in search_term
            )
            # Arguments as a list: `search_term` is never interpreted by the shell.
            _, output, _ = run_bounded(
                ["find", root, "-type", "f", "-name", f"*{escaped_term}*", "-print0"],
                30, MAX_COMMAND_OUTPUT,
            )
            # Only complete NUL-terminated paths; discard a truncated last path
            # and the output-cap diagnostic rather than returning a fake file.
            results = [name for name in output.split("\0")[:-1] if name][:max_results]

            return results
        except FileNotFoundError:
            logger.warning("`find` is not installed; file search unavailable")
            return results
        except Exception as e:
            logger.error(f"Error searching files: {e}")
            return results

    def get_file_info(self, filepath: str) -> Dict[str, str]:
        """Get information about a file."""
        info = {}

        if not filepath or not isinstance(filepath, str):
            return {"error": "Invalid path"}

        try:
            file_path = Path(filepath)
            if not file_path.exists():
                return {"error": "File not found"}

            stat = file_path.stat()
            info["name"] = file_path.name
            info["path"] = str(file_path)
            info["size"] = f"{stat.st_size} bytes"
            info["type"] = "directory" if file_path.is_dir() else "file"
            info["modified"] = time.ctime(stat.st_mtime)
            info["created"] = time.ctime(stat.st_ctime)
            info["accessed"] = time.ctime(stat.st_atime)
            info["permissions"] = oct(stat.st_mode)[-3:]
            info["owner"] = str(stat.st_uid)
            info["group"] = str(stat.st_gid)

            # Additional information for files
            if file_path.is_file():
                try:
                    with open(file_path, 'rb') as f:
                        header = f.read(32)
                    info["header"] = header.hex()[:20] + "..."
                except OSError:
                    pass

        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            info["error"] = str(e)

        return info
