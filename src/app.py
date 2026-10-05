#!/usr/bin/env python3
"""
Linux AI Assistant - Main application

A permanent AI assistant for Linux with a floating interface,
screen capture, expert mode and more.
"""

import sys
import signal
import logging
import argparse

try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    GTK_AVAILABLE = True
except (ImportError, ValueError) as _gtk_error:
    Gtk = None
    Gdk = None
    GLib = None
    GTK_AVAILABLE = False
    GTK_IMPORT_ERROR = _gtk_error

logger = logging.getLogger(__name__)

# NB: no sys.path hack here. The package must be imported as `src.app`
# (run.sh does `python -m src.app`); adding `src/` to sys.path allowed the
# same modules to be imported twice, as both `src.x` and `x`.
from .config_manager import ConfigManager
from .ai_client import AIClient
from .system_utils import SystemUtils

# `main_window`/`tray_icon` so devem ser importados quando o GTK existe: caso
# contrario `Gtk.init()` rebentaria com um AttributeError pouco claro.
if GTK_AVAILABLE:
    from .main_window import MainWindow
    from .tray_icon import TrayIcon

from . import i18n


class LinuxAIAssistant:
    """Main application"""

    def __init__(self):
        logger.info("Initializing Linux AI Assistant")

        # Idempotency flag for quit(): delete-event, the tray menu and
        # SIGINT/SIGTERM can all ask to quit, sometimes re-entrantly.
        self._quitting = False
        self.float_button_window = None
        self._show_requested = False
        self._global_shortcut = None
        self.global_shortcut_status = 'Global shortcut disabled'
        self.global_shortcut_status_changed = None

        try:
            self.config = ConfigManager()
            logger.info("Configuration loaded")

            self.ai_client = AIClient(self.config)
            logger.info("AI client initialized")

            self.system_utils = SystemUtils(self.config)
            logger.info("System utilities initialized")

            i18n.set_language_from_config(self.config)

            self.main_window = None
            self.tray_icon = None

        except Exception as e:
            logger.error(f"Error initializing application: {e}", exc_info=True)
            raise

    def run(self, show=False):
        """Start the application"""
        logger.info("Initializing GTK")

        try:
            # Initialize GTK. init_check() reports failure (e.g. no
            # DISPLAY) instead of aborting the process like init() does.
            initialized = Gtk.init_check()
            # PyGObject returns (ok, argv) on older versions, bool on newer
            if isinstance(initialized, tuple):
                initialized = initialized[0]
            if not initialized:
                logger.error("GTK could not be initialized (no display?)")
                return
            logger.info("GTK initialized")

            # Create main window
            logger.info("Creating main window")
            self.main_window = MainWindow(self, self.config, self.ai_client, self.system_utils)

            # Create system tray icon
            logger.info("Creating system tray icon")
            self.tray_icon = TrayIcon(self, self.config, self.main_window)

            # Create permanent floating button
            self._create_float_button()

            # Show window if auto_start is active
            if show or self._show_requested or self.config.get("app.auto_start", False):
                self.show_window()
                logger.info("Window shown (auto_start active)")
            else:
                logger.info("Window not shown (auto_start inactive). Use the system tray icon.")

            self.configure_global_shortcut(
                self.config.get('app.global_shortcut_enabled', False),
                self.config.get('app.global_shortcut', '<Ctrl><Alt>space'))

            # Start main loop
            logger.info("Starting GTK main loop")
            Gtk.main()

        except KeyboardInterrupt:
            logger.info("Received KeyboardInterrupt. Shutting down...")
            self.quit()
        except Exception as e:
            logger.error(f"Error in main loop: {e}", exc_info=True)
            self.quit()

    def quit(self):
        """Quit the application (safe to call more than once)"""
        if self._quitting:
            logger.debug("quit() already in progress; ignoring")
            return
        self._quitting = True
        logger.info("Terminating application")

        try:
            if self._global_shortcut is not None:
                try:
                    self._global_shortcut.close()
                except Exception as error:
                    logger.warning('Could not release global shortcut: %s', type(error).__name__)

            # O config é debounced 0,5 s e o timer é daemon (morre com o
            # processo): sem este flush, sair pela tray/SIGTERM perdia as
            # alterações dos últimos meio segundo (geometria, tema, keys).
            try:
                self.config.flush()
            except Exception as e:
                logger.warning(f"Could not flush config on quit: {e}")

            if self.main_window:
                # Drain pending history writes before tearing the window down
                try:
                    self.main_window.close_history_writer()
                except Exception as e:
                    logger.warning(f"Could not flush history on quit: {e}")
                self.main_window.destroy()
                logger.info("Main window destroyed")

            if self.float_button_window is not None:
                try:
                    self.float_button_window.destroy()
                except Exception as e:
                    logger.warning(f"Could not destroy float button: {e}")
                self.float_button_window = None

            if self.tray_icon:
                if getattr(self.tray_icon, 'indicator', None) is not None:
                    self.tray_icon.indicator.set_status(0)
                    logger.info("AppIndicator deactivated")
                elif getattr(self.tray_icon, 'status_icon', None) is not None:
                    self.tray_icon.status_icon.set_visible(False)
                    logger.info("StatusIcon deactivated")
        except Exception as e:
            logger.error(f"Error terminating application: {e}", exc_info=True)
        finally:
            # O fecho do main loop não pode depender do sucesso da destruição
            # dos widgets: sem `finally`, uma exceção a meio de quit() deixava
            # o processo vivo sem janela nem tray visíveis.
            try:
                logger.info("GTK main quit")
                Gtk.main_quit()
            except Exception as e:
                logger.error(f"Could not quit GTK main loop: {e}")

    def show_window(self):
        """Present the existing conversation without toggling it closed."""
        if self._quitting:
            return
        if self.main_window is None:
            self._show_requested = True
            return
        self.main_window.show_all()
        self.main_window.present()
        self.main_window.sync_visibility()

    def configure_global_shortcut(self, enabled, accelerator):
        """Apply the optional shortcut and return its current registration status."""
        from .global_shortcuts import GlobalShortcut
        if self._global_shortcut is None:
            self._global_shortcut = GlobalShortcut(self.show_window, self._shortcut_status_changed)
        return self._global_shortcut.configure(enabled, accelerator)

    def _shortcut_status_changed(self, status):
        self.global_shortcut_status = status
        callback = self.global_shortcut_status_changed
        if callback is not None:
            callback(status)

    def _create_float_button(self):
        """Permanent floating button to show/hide the main window"""
        button_window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        button_window.set_default_size(52, 52)
        button_window.set_decorated(False)
        button_window.set_skip_taskbar_hint(True)
        button_window.set_skip_pager_hint(True)
        button_window.set_keep_above(True)
        button_window.stick()
        button_window.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        button_window.set_opacity(0.75)

        def toggle_main_window(btn):
            # Ponto único do toggle: sincroniza o label da tray e o estado.
            self.main_window.toggle_visibility()

        button = Gtk.Button(label="✦")
        button.connect("clicked", toggle_main_window)
        button_window.add(button)

        edge = self.config.get("app.button_edge", "right")

        # Wayland ignores move(), so tiling compositors treat this 52x52
        # window as a normal one. A layer-shell surface avoids that. When
        # the protocol does not take effect, fall through to move().
        from . import dock
        if dock.apply_float_button(button_window, edge):
            button_window.show_all()
            self.float_button_window = button_window
            return
        display = Gdk.Display.get_default()
        monitor = display.get_monitor(0) if display is not None else None
        if monitor is not None:
            geometry = monitor.get_geometry()
            x0, y0 = geometry.x, geometry.y
            w, h = geometry.width, geometry.height
            if edge == "left":
                button_window.move(x0 + 12, y0 + h // 2 - 26)
            elif edge == "top":
                button_window.move(x0 + w // 2 - 26, y0 + 12)
            elif edge == "bottom":
                button_window.move(x0 + w // 2 - 26, y0 + h - 64)
            else:
                button_window.move(x0 + w - 64, y0 + h // 2 - 26)
        else:
            # No monitor (headless/RDP): skip positioning instead of
            # crashing on monitor.get_geometry().
            logger.warning("No monitor available; float button not positioned")

        button_window.show_all()
        self.float_button_window = button_window


def main(argv=None):
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Linux AI Assistant desktop application')
    parser.add_argument('--show', action='store_true',
                        help='Start or focus the existing assistant window (for desktop shortcuts)')
    args = parser.parse_args(argv)
    logger.info("Linux AI Assistant - Start")
    # File logging só no arranque real (não no import do pacote)
    from . import setup_file_logging
    setup_file_logging()

    if not GTK_AVAILABLE:
        message = (
            "GTK 3 is not available. Install the system bindings:\n"
            "  Debian/Ubuntu : sudo apt install python3-gi gir1.2-gtk-3.0\n"
            "  Fedora        : sudo dnf install python3-gobject gtk3\n"
            "  Arch          : sudo pacman -S python-gobject gtk3\n"
            "  Void          : sudo xbps-install python3-gobject gtk+3\n"
            "Then create the venv with --system-site-packages (see README).\n"
            "For headless use: python -m src.cli --help"
        )
        print(f"\nError: {GTK_IMPORT_ERROR}\n\n{message}", file=sys.stderr)
        return 1

    activation = None
    try:
        from .global_shortcuts import DesktopActivation
        # Register before loading configuration or creating any window. A second
        # invocation only sends an activation request to the original process.
        app_holder = []
        pending_show = []

        def show_existing():
            if app_holder:
                app_holder[0].show_window()
            else:
                pending_show.append(True)

        activation = DesktopActivation(show_existing)
        if not activation.register():
            activation.activate()
            return 0
        app = LinuxAIAssistant()
        app_holder.append(app)

        # Handle signals to quit correctly. `signal.signal` handlers only run
        # between Python bytecodes, which never happens while Gtk.main()
        # blocks in C - so SIGTERM would be deferred indefinitely.
        # GLib.unix_signal_add delivers the signal through the main loop,
        # where touching GTK is safe.
        def _quit_source(*_args):
            app.quit()
            return GLib.SOURCE_REMOVE
        try:
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, _quit_source)
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, _quit_source)
        except (AttributeError, TypeError, OSError):
            # Non-Unix GLib (or missing unix_signal_add): best effort.
            signal.signal(signal.SIGINT, lambda s, f: GLib.idle_add(app.quit))
            signal.signal(signal.SIGTERM, lambda s, f: GLib.idle_add(app.quit))
        logger.info("Signal handlers configured")

        app.run(show=args.show or bool(pending_show))

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1
    finally:
        if activation is not None:
            activation.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
