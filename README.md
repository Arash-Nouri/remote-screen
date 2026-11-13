# remote-screen

A computer's screen and microphone in a browser, from another device. One small Python server per computer
(Windows or macOS) that starts at login and keeps running. Its page shows the screen live. **Control** sends your
mouse and keyboard to it, and **Listen** plays its microphone.

## Install

| | |
|---|---|
| macOS | `./install.sh`: copies it to `~/Library/Application Support/remote-screen` (a program started at login may not read `~/Documents`), makes `.venv` there, and adds a LaunchAgent (`local.remote-screen`) that starts it at login and restarts it if it stops. On first start it asks macOS for Screen Recording, Accessibility (for Control) and Microphone. Settings and log live in that folder. |
| Windows | `powershell -ExecutionPolicy Bypass -File install.ps1`: same, as a scheduled task `RemoteScreen` that runs at logon in your desktop session (a service couldn't see the screen). |

Running the installer again restarts the server with the new code. `./install.sh --remove` or `install.ps1 -Remove` stops it and takes it off login.

The first run makes `config.json` (git-ignored) with a random **access key**. `python server.py --url` prints the
viewer address with the key in it, and `--new-key` replaces the key. Every connection needs the key. Without it the page asks once, then the browser keeps it.

## Permissions (macOS)

Without Screen Recording the picture shows only the desktop background and menu bar, not the windows. The viewer
shows a bar listing what's missing on that computer. Each **Ask for …** button shows macOS's prompt again, or opens
the right place in System Settings if it was turned down before (macOS only prompts once). **Restart it** makes
the server restart so it picks up a permission that was just allowed.

## Settings (`config.json`)

Defaults are in `config.example.json`:

| key | |
|---|---|
| `host`, `port` | where it listens; `127.0.0.1:8760` by default, so only this computer reaches it. To use it from elsewhere, put an HTTPS reverse proxy in front, or set `host`. |
| `name` | shown in the viewer (default: the computer's name) |
| `fps`, `quality`, `max_width` | live view: frames a second (unchanged frames aren't sent), JPEG quality, width it's scaled down to |
| `control`, `mic` | allow mouse + keyboard / Listen |
| `frame_ancestors` | pages allowed to embed the viewer (CSP `frame-ancestors`) |

Restart after a change: run the installer again.

## Embedding

`/?view=assistant#key=<key>` is the compact viewer for another page's iframe. The parent page can talk to it:

- `{type:'desktop-hello'}` → it reports `{type:'desktop-state', connected, screens}` (screens = how many monitors) now, on every change and every 3 s.
- `{type:'desktop-shot', id, edge, mon}` (mon optional: 1, 2…; default the one shown) → it replies `{type:'desktop-shot', id, url}`: a fresh full-resolution JPEG data URL,
  longest side ≤ `edge`. Or `{…, error}`.

Also `GET /shot?edge=N&mon=M` with header `X-Key: <key>` returns one JPEG, and `GET /screens` (same header) returns `{name, screens: [{w, h}]}`. Both answer other pages' fetches (CORS), so a page that knows the key can take screenshots without opening the viewer.

## Limits

- It runs in your login session, so it sees what you'd see: a locked screen shows up as locked or black, and
  it can't type on the Windows sign-in or UAC screens.
- The mouse pointer itself isn't in the picture on Windows. With Control on, your own pointer is where it is.
- Keys go by physical key position; on a Mac viewing Windows, ⌘ is the Windows key (use Ctrl for shortcuts).

## Files

`server.py` (server, capture, input, mic), `static/` (the viewer page), `install.sh`, `install.ps1`, `requirements.txt`, `mac/launcher.c` (the small macOS app that starts it).
