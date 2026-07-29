# PyVNCs

Simple command line multiplatform Python VNC Server.

This is a simple command line VNC server, aimed at quick remote support situations.

This VNC Server is proven to work on:
- Linux (X11 and Wayland)
- Mac OS
- Windows (7 & onwards)

## Usage

```bash
python vncserver.py -P <password> [options]
```

### Examples

```bash
# Basic VNC server with password auth
python vncserver.py -P secret

# Listen on specific address/port
python vncserver.py -P secret -l 192.168.1.100 -p 5900

# No authentication (local network only!)
python vncserver.py -P unused -A 1

# TLS encrypted channel (TigerVNC/Remmina compatible)
python vncserver.py -P secret -A 18

# VeNCrypt with user:pass
python vncserver.py -A 19 -P admin:secret

# VeNCrypt with multiple users from file
python vncserver.py -A 19 -U users.txt

# Apple Remote Desktop compatible (macOS clients)
python vncserver.py -A 30 -P admin:secret

# Unix Login (plaintext user/pass)
python vncserver.py -A 129 -P admin:secret

# Performance tuning: tight encoding, low compression, 30fps
python vncserver.py -P secret -e tight,raw -z 1 -q 40 -f 30

# Quiet mode (only errors), disable cursor
python vncserver.py -P secret -L error --no-cursor

# Half resolution for slow links
python vncserver.py -P secret -s 0.5

# Full example: TLS + tight + tuned
python vncserver.py -P secret -A 18 -e tight,raw -z 1 -q 50 -f 25 -L info
```

## Command Line Options

| Flag | Description | Default |
|------|-------------|---------|
| `-P` | Password (required). Format: `pass`, `user:pass`, or `u1:p1;u2:p2` | — |
| `-l` | Listen address | `0.0.0.0` |
| `-p` | Listen port | `5901` |
| `-A` | Auth type (see below) | `2` |
| `-e` | Whitelist encodings (comma-separated) | all |
| `-E` | Blacklist encodings (comma-separated) | none |
| `-z` | Zlib compression level 0-9 | `1` |
| `-q` | JPEG quality 1-100 (tight encoding) | `50` |
| `-f` | Max frames per second | `20` |
| `-L` | Log level: debug, info, warning, error | `debug` |
| `-s` | Framebuffer scale factor | `1.0` |
| `-t` | Desktop name / window title | `pyvncs` |
| `-8` | Enable 8-bit color dithering | off |
| `--no-cursor` | Disable cursor pseudo-encoding | off |
| `-u` | Username (VeNCrypt, alternative to `user:pass`) | — |
| `-U` | File with `user:pass` per line (VeNCrypt) | — |
| `-C` | TLS certificate PEM file | auto-generated |
| `-O` | Redirect output to file | — |

## Authentication

| `-A` | Type | Description |
|------|------|-------------|
| 1 | None | No authentication |
| 2 | VNC | Classic DES challenge-response |
| 18 | TLS | Anonymous TLS + VNC auth (TigerVNC, Remmina) |
| 19 | VeNCrypt | Multi-subtype (see below) |
| 30 | Apple ARD | Diffie-Hellman + AES (macOS Screen Sharing, Screens) |
| 129 | Unix Login | Plaintext user/pass |

### VeNCrypt subtypes (`-A 19`)

Negotiated automatically with the client:

| Subtype | Channel | Authentication |
|---------|---------|----------------|
| 250 TLSNone | TLS | none |
| 251 TLSVnc | TLS | VNC DES |
| 252 TLSPlain | TLS | user/pass |
| 253 X509None | TLS + client cert | none |
| 254 X509Vnc | TLS + client cert | VNC DES |
| 255 X509Plain | TLS + client cert | user/pass |
| 256 Plain | plaintext | user/pass |

### Users file format (`-U`)

```
# comments are ignored
admin:s3cret
viewer:readonly123
```

## Encodings

| Encoding | Description |
|----------|-------------|
| tight | JPEG + zlib + palette/gradient filters (default) |
| zrle | Zlib Run-Length Encoding |
| hextile | RFC 6143 §7.7.4 |
| zlib | Zlib compressed raw |
| raw | Uncompressed pixels |

Use `-e tight,raw` to whitelist or `-E hextile,zlib` to blacklist.
Raw is always available as fallback per RFB spec.

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

## Requirements

```
pydes, pynput, numpy, Pillow-PIL, elevate
# Wayland only: PyGObject, dbus-python, cryptography, cffi
# Apple ARD auth: cryptography
```

## FAQ

Q: Why a VNC server on python?

A: Because python is fun!
