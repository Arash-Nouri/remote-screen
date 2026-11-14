# remote-screen

A computer's screen and microphone in a browser, from another device. One small Python server per computer
(Windows or macOS) that starts at login and keeps running. Its page shows the screen live. **Control** sends your
mouse and keyboard to it, and **Listen** plays its microphone. Copy and paste work both ways: text copied there lands on your clipboard, and pasting (⌘V / Ctrl+V) types in your own clipboard's text.

## Install

On a new Mac, first get the code:

```bash
xcode-select --install                     # git, python3, clang (install.sh needs them); click Install
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
pbcopy < ~/.ssh/id_ed25519.pub             # paste at https://github.com/settings/ssh/new
ssh -T git@github.com                      # answer "yes"
mkdir -p ~/Documents && cd ~/Documents
git clone git@github.com:Arash-Nouri/remote-screen.git && cd remote-screen
```

Then run the installer:

| | |
|---|---|
| macOS | `./install.sh`: copies it to `~/Library/Application Support/remote-screen` (a program started at login may not read `~/Documents`), makes `.venv` there, and adds a LaunchAgent (`local.remote-screen`) that starts it at login and restarts it if it stops. On first start it asks macOS for Screen Recording, Accessibility (for Control) and Microphone. Settings and log live in that folder. |
| Windows | `powershell -ExecutionPolicy Bypass -File install.ps1`: same, as a scheduled task `RemoteScreen` that runs at logon in your desktop session (a service couldn't see the screen). |

Running the installer again restarts the server with the new code. `./install.sh --remove` or `install.ps1 -Remove` stops it and takes it off login.

The first run makes `config.json` (git-ignored) with a random **access key**. `python server.py --url` prints the
viewer address with the key in it, and `--new-key` replaces the key. Every connection needs the key. Without it the page asks once, then the browser keeps it.

## Join a group

Computers that share one key form a **group**. One of them is the **directory**: the others check in with it every
minute, and pages that know the key (such as an assistant's Screen panel) list every computer in the group from its
`GET /devices`. A new computer shows up there once it has joined. To join it:

1. **Give it an address the others can reach.** It listens on `127.0.0.1` only, so put an HTTPS proxy in front.
   With Tailscale (same account as the other computers):
   ```bash
   tailscale serve --bg --https=8443 http://127.0.0.1:8760
   tailscale status            # this computer's name; its address is https://<name>.<tailnet>.ts.net:8443/
   ```
   (Mac: if `tailscale` isn't found, use `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.)
2. **Join**, with the directory's address and the group's key (the `key` in the directory computer's
   `config.json`; never put it in a file in this repo):
   ```bash
   # macOS
   "$HOME/Library/Application Support/remote-screen/.venv/bin/python" server.py \
       --join <directory address> <group key> --public-url <this computer's address from step 1>
   ./install.sh                # restarts it with the new settings
   ```
   On Windows: `.venv\Scripts\python.exe server.py --join … --public-url …`, then run `install.ps1` again.
   Joining replaces this computer's own key with the group's, so its old viewer links stop working.
3. **Check** within a minute: `curl -s -H "X-Key: <group key>" <directory address>devices` lists this computer.
   If not, look at `remote-screen.log` (macOS: in `~/Library/Application Support/remote-screen/`) for
   "directory unreachable" or "directory said …".

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
| `url`, `directory` | this computer's address as the others reach it, and the group's directory (empty = none, or this one is it); set by `--join` / `--public-url`, see Join a group |
| `fps`, `quality`, `max_width` | live view: frames a second (unchanged frames aren't sent), JPEG quality, width it's scaled down to |
| `control`, `mic` | allow mouse + keyboard / Listen |
| `clipboard` | copy/paste text both ways with the viewer (on by default; only what's copied while a viewer is connected is sent; paste also needs `control`) |
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

- Copied text reaches your clipboard only while the viewer has focus (browsers' rule); otherwise a **Copy** button
  appears in its bar. Paste uses the browser's paste event; where there's none (Ctrl+V on a Mac) the browser asks once
  to read the clipboard. An embedding page's iframe needs `allow="clipboard-read; clipboard-write"`. Text only, no images or files.

## Files

`server.py` (server, capture, input, mic), `static/` (the viewer page), `install.sh`, `install.ps1`, `requirements.txt`, `mac/launcher.c` (the small macOS app that starts it).
