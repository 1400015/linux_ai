import os
import getpass
import shutil
import shlex
import subprocess
import platform
import time
from typing import Dict, List, Tuple, Any
from pathlib import Path
import logging

# Configurar logger
logger = logging.getLogger(__name__)# Commands whose last argument is a file path. For these, the path goes
# through `allowed_edit_dirs` just like `read_file`/`write_file`.
_FILE_ARG_COMMANDS = {"cat", "head", "tail", "less", "more", "file", "stat"}


class SystemUtils:
    """Utilities to interact with the Linux system"""
    
    def __init__(self, config_manager):
        self.config = config_manager
        self.allowed_commands = set(config_manager.get("permissions.allowed_commands", []))
        self.allowed_edit_dirs = set(config_manager.get("permissions.allowed_edit_dirs", []))
        self._detect_environment()
    
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
        """Validate path arguments of file-reading commands.

        Without this, an allowlisted `cat` would bypass `allowed_edit_dirs`
        entirely (e.g. `cat /etc/shadow`). Only arguments that look like
        absolute or home-relative paths are checked, so options such as
        `-n` or `grep`-style patterns keep working.
        """
        cmd_base = cmd_parts[0]
        if cmd_base not in _FILE_ARG_COMMANDS:
            return True

        for arg in cmd_parts[1:]:
            if arg.startswith("-") or arg == "/dev/stdin":
                continue
            if not (arg.startswith("/") or arg.startswith("~")):
                continue
            if not self._validate_path(arg):
                logger.warning(
                    f"Argument outside allowed directories for '{cmd_base}': {arg}"
                )
                return False
        return True

    
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
    
    def execute_command(self, command: str, timeout: int = 10) -> Tuple[bool, str]:
        """
        Run a command on the system with sanitization.

        Args:
            command: Command to run.
            timeout: Timeout in seconds.

        Returns:
            Tuple (success, output).
        """
        if not command or not isinstance(command, str):
            logger.error("Invalid command")
            return False, "Invalid command"
        
        # Sanitize command
        try:
            cmd_parts = self._sanitize_command(command)
            if not cmd_parts:
                return False, "Invalid command after sanitization"
            
            cmd_base = cmd_parts[0]
        except Exception as e:
            logger.error(f"Error sanitizing command: {e}")
            return False, f"Error processing command: {e}"
        
        # Check if the command is allowed
        if cmd_base not in self.allowed_commands:
            logger.warning(f"Command not allowed: {cmd_base}")
            return False, f"Command not allowed: {cmd_base}"
        
        # Commands that read files also respect allowed_edit_dirs
        if not self._validate_file_args(cmd_parts):
            return False, f"Path not allowed: {cmd_base}"

        try:
            logger.info(f"Running command: {' '.join(cmd_parts)}")
            
            result = subprocess.run(
                cmd_parts,  # Use list instead of shell=True for security
                capture_output=True,
                text=True,
                timeout=timeout
            )
            
            if result.returncode == 0:
                logger.debug(f"Command executed successfully: {cmd_base}")
                return True, result.stdout
            else:
                logger.warning(f"Command failed ({result.returncode}): {cmd_base}")
                return False, result.stderr or result.stdout
                
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout running command: {command}")
            return False, f"Timeout running command: {command}"
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
                for i, line in enumerate(f):
                    if i >= max_lines:
                        lines.append(f"\n... (more than {max_lines} lines omitted)")
                        break
                    if len(line) > max_line_chars:
                        line = line[:max_line_chars] + "... (line truncated)\n"
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
            
            # Check permissions
            if not os.access(file_path.parent, os.W_OK):
                return False, f"No permission to write in: {file_path.parent}"
            
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
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
    
    def capture_screen(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capture the screen with support for Wayland and X11.

        Args:
            output_path: Path to save the capture (optional).

        Returns:
            Tuple (success, image_path).
        """
        try:
            if self.is_wayland:
                # Try using grim for Wayland
                if subprocess.run(['which', 'grim'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['grim', '-o', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Screen captured with grim: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['grim', '-o', tmp.name], 
                                                  capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Screen temporarily captured: {tmp.name}")
                                return True, tmp.name
                
                # Try using slurp for Wayland (interactive)
                if subprocess.run(['which', 'slurp'], capture_output=True).returncode == 0:
                    logger.warning("slurp available but requires interaction. Use grim for automatic capture.")
            
            # Try using scrot for X11
            if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                if output_path:
                    result = subprocess.run(['scrot', output_path], 
                                          capture_output=True, timeout=10)
                    if result.returncode == 0:
                        logger.info(f"Screen captured with scrot: {output_path}")
                        return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        result = subprocess.run(['scrot', tmp.name], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Screen temporarily captured: {tmp.name}")
                            return True, tmp.name
            
            # Try using gnome-screenshot
            if subprocess.run(['which', 'gnome-screenshot'], capture_output=True).returncode == 0:
                if output_path:
                    result = subprocess.run(['gnome-screenshot', '-f', output_path], 
                                          capture_output=True, timeout=10)
                    if result.returncode == 0:
                        logger.info(f"Screen captured with gnome-screenshot: {output_path}")
                        return True, output_path
                else:
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                        result = subprocess.run(['gnome-screenshot', '-f', tmp.name], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Screen temporarily captured: {tmp.name}")
                            return True, tmp.name
            
            logger.error("No screen capture utility found")
            return False, "No screen capture utility found (install scrot, grim or gnome-screenshot)"
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout capturing screen")
            return False, "Timeout capturing screen"
        except Exception as e:
            logger.error(f"Error capturing screen: {e}")
            return False, f"Error capturing screen: {e}"
    
    def capture_active_window(self, output_path: str = None) -> Tuple[bool, str]:
        """
        Capture the active window with support for Wayland and X11.

        Args:
            output_path: Path to save the capture.

        Returns:
            Tuple (success, image_path).
        """
        try:
            if self.is_wayland:
                # Try using slurp for Wayland
                if subprocess.run(['which', 'slurp'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['slurp', '-o', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Active window captured with slurp: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['slurp', '-o', tmp.name], 
                                                  capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Active window temporarily captured: {tmp.name}")
                                return True, tmp.name
            else:
                # Use scrot for X11
                if subprocess.run(['which', 'scrot'], capture_output=True).returncode == 0:
                    if output_path:
                        result = subprocess.run(['scrot', '-u', output_path], 
                                              capture_output=True, timeout=10)
                        if result.returncode == 0:
                            logger.info(f"Active window captured with scrot: {output_path}")
                            return True, output_path
                    else:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                            result = subprocess.run(['scrot', '-u', tmp.name], 
                                              capture_output=True, timeout=10)
                            if result.returncode == 0:
                                logger.info(f"Active window temporarily captured: {tmp.name}")
                                return True, tmp.name
            
            logger.error("No window capture utility found")
            return False, "No window capture utility found (install scrot or slurp)"
            
        except subprocess.TimeoutExpired:
            logger.error("Timeout capturing active window")
            return False, "Timeout capturing active window"
        except Exception as e:
            logger.error(f"Error capturing active window: {e}")
            return False, f"Error capturing active window: {e}"
    
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
                text = pytesseract.image_to_string(img, lang=lang)
                
                # If no text is obtained, try with English only
                if not text.strip():
                    logger.info("No text detected. Trying with English...")
                    text = pytesseract.image_to_string(img, lang='eng')
                
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
        
        return sorted(processes, key=lambda x: float(x['cpu'].replace('%', '')), reverse=True)
    
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

            # Arguments as a list: `search_term` is never interpreted by the shell.
            proc = subprocess.Popen(
                ["find", root, "-type", "f", "-name", f"*{search_term}*"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        results.append(line)
                        if len(results) >= max_results:
                            break
            finally:
                # `find` keeps running on large trees: always terminate
                # and close the pipe to avoid exhausting file descriptors.
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                if proc.stdout is not None:
                    proc.stdout.close()

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

