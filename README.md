# PyVNCs

Simple command line multiplatform Python VNC Server.

This is a simple command line VNC server, aimed at quick remote support situations.

This VNC Server is proven to work on:
- Linux (X11 and Wayland)
- Mac OS
- Windows (7 & onwards)

Supported encodings:
- raw
- zlib
- hextile (RFC 6143 §7.7.4)
- tight

## Authentication

- VNC Password Auth
- VeNCrypt with TLSPlain support (`user:password` format in config)

## Wayland Support (Linux)

PyVNCs supports screen capture on Wayland via two methods:

### Portal-based capture (recommended)
Uses `xdg-desktop-portal` ScreenCast + PipeWire. Works on GNOME, KDE, and other compositors that expose the portal.

**System dependencies:**
```bash
# Ubuntu/Debian
sudo apt install gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
                 gst-plugins-good-1.0 gst-plugins-bad-1.0 \
                 pipewire xdg-desktop-portal xdg-desktop-portal-gnome

# Fedora
sudo dnf install gstreamer1-plugins-base gstreamer1-plugins-good \
                 gstreamer1-plugins-bad pipewire xdg-desktop-portal \
                 xdg-desktop-portal-gnome
```

**Python dependencies:** `PyGObject`, `dbus-python`

### PipeWire direct capture (fallback)
Uses libpipewire via cffi. Activated automatically when portal capture is unavailable.

**System dependencies:** `libpipewire-0.3-dev`, `libspa-0.2-dev`

**Environment variables:**
| Variable | Values | Default |
|---|---|---|
| `PYVNCS_WAYLAND_CAPTURE` | `monitors`, `windows` | `monitors` |

## Clipboard

Server-side clipboard controller with X11 (`xclip`) integration.

## Adaptive Rate Limiting

Bandwidth estimation with adaptive update throttling for smoother connections.

## Requirements

```
pydes, pynput, numpy, Pillow-PIL, elevate
# Wayland only: PyGObject, dbus-python, cryptography, cffi
```

## FAQ:

Q: Why a VNC server on python?

A: Because python is fun!
