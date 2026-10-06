import gi
import logging

from .i18n import _

# Configurar logger
logger = logging.getLogger(__name__)

try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import Gtk, GdkPixbuf, AppIndicator3
except (ImportError, ValueError):
    AppIndicator3 = None
    # Fallback for sistemas sem AppIndicator3
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gtk, GdkPixbuf


def toggle_on_click_enabled(config_manager, default=True) -> bool:
    """Pure decision: whether a tray icon click should toggle the window.

    Kept module-level (and GTK-free in spirit) so tests and future platform
    backends can reuse it without instantiating the GTK-dependent class.
    """
    try:
        return bool(config_manager.get("app.tray_toggle_on_click", default))
    except Exception:
        return default


class TrayIcon:
    """System tray icon for the application.

    With ``app.tray_toggle_on_click`` enabled (the default), clicking the
    icon toggles the main window like a drawer: the first click shows it,
    the next click hides it. The menu remains available separately.
    """

    def __init__(self, app, config_manager, main_window):
        self.app = app
        self.config = config_manager
        self.main_window = main_window

        logger.info("Initialize system tray icon")

        # Guards update_expert_mode() against re-entering the "toggled"
        # handler while we are programmatically syncing the checkbox.
        self._syncing = False

        self._create_tray_icon()

    def _create_tray_icon(self):
        """Create system tray icon"""
        try:
            from .desktop_icons import ICON_NAME, icon_path
            # Try AppIndicator3 (Ubuntu).
            # NB: the class is `Indicator` (`IndicatorApp` does not exist and
            # raised AttributeError, silently forcing the StatusIcon fallback).
            self.indicator = AppIndicator3.Indicator.new(
                "linux-ai-assistant",
                ICON_NAME,
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS
            )
            path = icon_path()
            if path is not None:
                self.indicator.set_icon_theme_path(str(path.parent))
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            self.indicator.set_attention_icon("dialog-information")

            # AppIndicator3 exposes no left-click signal: any click opens the
            # menu. secondary_activate (middle click) toggles the drawer when
            # the user enabled tray_toggle_on_click.
            if self._toggle_on_click_enabled():
                self.indicator.connect("secondary_activate", self.on_tray_clicked)

            # Create menu
            self._create_menu()
            self.indicator.set_menu(self.menu)

            logger.info("AppIndicator3 created successfully")

        except (ImportError, AttributeError) as e:
            logger.warning(f"AppIndicator3 not available: {e}. Using StatusIcon.")
            # Fallback for Gtk.StatusIcon (works on most systems)
            self._create_status_icon()

    def _create_status_icon(self):
        """Create status icon (fallback)"""
        try:
            self.status_icon = Gtk.StatusIcon()

            from .desktop_icons import ICON_NAME, configure_application_icon
            icon_theme = configure_application_icon()
            icon = icon_theme.load_icon(ICON_NAME, 48, 0)
            if icon:
                self.status_icon.set_from_pixbuf(icon)
            else:
                # Create simple icon
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 48)
                    pixbuf.fill(0x4CAF50FF)  # Green
                    self.status_icon.set_from_pixbuf(pixbuf)
                except Exception as e:
                    logger.error(f"Error creating icon: {e}")

            self.status_icon.set_tooltip_text("Linux AI Assistant")
            self.status_icon.connect("activate", self.on_tray_clicked)
            self.status_icon.connect("popup-menu", self.on_tray_menu)

            # Create menu
            self._create_menu()

            logger.info("StatusIcon created successfully")

        except Exception as e:
            # Do not re-raise: without a tray icon the app still works via
            # the window/float button, and raising here aborted startup.
            logger.error(f"Error creating StatusIcon: {e}")
            self.status_icon = None

    def _create_menu(self):
        """Create system tray icon menu"""
        self.menu = Gtk.Menu()

        # Item to show/hide window
        self.toggle_item = Gtk.MenuItem(label=_("Show Window"))
        self.toggle_item.connect("activate", self.on_toggle_window)
        self.menu.append(self.toggle_item)

        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())

        # Item for expert mode
        self.expert_item = Gtk.CheckMenuItem(label=_("Expert Mode"))
        self.expert_item.connect("toggled", self.on_toggle_expert_mode)
        self.menu.append(self.expert_item)

        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())

        # Item to configure
        config_item = Gtk.MenuItem(label=_("Settings"))
        config_item.connect("activate", self.on_config_clicked)
        self.menu.append(config_item)

        # History item
        history_item = Gtk.MenuItem(label=_("Conversation History"))
        history_item.connect("activate", self.on_history_clicked)
        self.menu.append(history_item)

        # Statistics item
        stats_item = Gtk.MenuItem(label=_("Statistics"))
        stats_item.connect("activate", self.on_stats_clicked)
        self.menu.append(stats_item)

        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())

        # Item to quit
        quit_item = Gtk.MenuItem(label=_("Quit"))
        quit_item.connect("activate", self.on_quit_clicked)
        self.menu.append(quit_item)

        self.menu.show_all()
        logger.debug("System tray menu created")

    def _toggle_on_click_enabled(self) -> bool:
        """Whether clicking the tray icon should toggle the window."""
        return toggle_on_click_enabled(self.config)

    def on_tray_clicked(self, icon, *args):
        """Handler for a tray icon click: toggle the drawer when enabled."""
        if not self._toggle_on_click_enabled():
            return
        self.on_toggle_window(None)

    def on_tray_menu(self, icon, button, time):
        """Handler for system tray icon menu"""
        if getattr(self, 'status_icon', None) is not None:
            self.menu.popup_at_pointer(None)  # Popup at cursor
            logger.debug("System tray menu shown")

    def on_toggle_window(self, item):
        """Toggle window visibility"""
        # Delegar no ponto único da MainWindow (que sincroniza o label da
        # tray e o estado por onde quer que o toggle tenha origem).
        self.main_window.toggle_visibility()
        logger.debug("Window toggled")

    def on_toggle_expert_mode(self, item):
        """Toggle expert mode"""
        if self._syncing:
            return  # programmatic set_active() from update_expert_mode()
        self.main_window.on_expert_mode_toggled(None)
        logger.debug(f"Expert mode toggled: {self.main_window.expert_mode}")

    def on_config_clicked(self, item):
        """Open settings window"""
        self.main_window._show_config_dialog()
        logger.debug("Settings dialog opened")

    def on_history_clicked(self, item):
        """Open history"""
        self.main_window._show_history_dialog()
        logger.debug("History dialog opened")

    def on_stats_clicked(self, item):
        """Open statistics"""
        self.main_window._show_stats_dialog()
        logger.debug("Statistics dialog opened")

    def on_quit_clicked(self, item):
        """Quit the application"""
        logger.info("Quit through the system tray menu")
        self.app.quit()

    def update_expert_mode(self, enabled: bool):
        """Update the expert mode state in the menu"""
        if not hasattr(self, 'expert_item'):
            return
        if self.expert_item.get_active() == enabled:
            return
        # set_active() fires "toggled"; the flag stops the handler from
        # toggling the main window back (infinite ping-pong).
        self._syncing = True
        try:
            self.expert_item.set_active(enabled)
        finally:
            self._syncing = False
        logger.debug(f"Expert mode menu updated: {enabled}")

    def update_toggle_label(self, visible: bool):
        """Update the toggle label in the menu"""
        if hasattr(self, 'toggle_item'):
            self.toggle_item.set_label(_("Hide Window") if visible else _("Show Window"))
