# Native Void Linux package

The template builds upstream snapshot `fbaa4ad25c016ab20ab0f4294ba6913d71c3c228`
with the bundled `patches/review-corrections.patch` to produce version 1.2.0.
The patch carries the security fixes, conversation sessions, assistance modes
and bundled local diagnostic guides that are not yet in that published snapshot. Both the archive checksum and application of
the patch have been verified. Keep the patch beside the template when copying
the recipe. After these changes reach upstream, a future recipe can pin that
commit and remove the patch.

## Build

Run `xbps-src` as a regular user inside a `void-packages` checkout:

```sh
git clone https://github.com/void-linux/void-packages.git
cd void-packages
./xbps-src binary-bootstrap
cp -r /path/to/linux_ai/xbps-src/linux-ai-assistant srcpkgs/
./xbps-src pkg linux-ai-assistant
sudo xbps-install -R "$PWD/hostdir/binpkgs" linux-ai-assistant
```

Copy the entire package directory, not just the template.
Copying a recipe alone does not create or publish an installable binary package.

The Python console entry point is installed in `/usr/bin`, the desktop entry in
`/usr/share/applications`, and bundled themes in
`/usr/share/linux-ai-assistant/themes`. No files are installed in a user's home.
The application creates `~/.config/linux_ai_assistant/config.json` on first use.
API keys can be set in the settings dialog or in the `.env` next to that file.

The package uses Void's Python/GTK dependencies; there is no private virtual
environment or pip installation during package installation. OCR falls back to
the distro's `tesseract-ocr` executable when the optional pytesseract wrapper is
unavailable. AppIndicator and gtk-layer-shell are optional; Gtk.StatusIcon is
used when AppIndicator is unavailable. Wayland features still require separate
validation with the selected compositor.

For graphical session startup, use an XDG autostart entry or the window
manager's own startup configuration. Do not start the GUI as a root runit service.

See the [xbps-src manual](https://github.com/void-linux/void-packages/blob/master/Manual.md).
