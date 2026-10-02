# Native Void Linux package

The template builds published snapshot `be865168e74f42f858bc69f412522df84b3e53d1`
(application 1.3.0, package revision 2). That snapshot already contains the
security fixes, sessions, assistance modes and Wayland window fixes, so the
recipe no longer carries a patch. The archive checksum was verified against
the GitHub tarball. Copy the whole package directory, including this README,
when moving the recipe. After a newer commit is published, update `_commit`
and `checksum` together.

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
