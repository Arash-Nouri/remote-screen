#!/bin/bash
# macOS: copy the app to ~/Library/Application Support/remote-screen, set up its .venv, start it now and at every login (LaunchAgent).
# ./install.sh --remove undoes it. (A program started at login may not read ~/Documents, so it runs from that copy.)
set -euo pipefail
cd "$(dirname "$0")"
SRC="$(pwd)"; APP="$HOME/Library/Application Support/remote-screen"; LABEL=local.remote-screen
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [[ "${1:-}" == "--remove" ]]; then rm -f "$PLIST"; echo "Removed (settings kept in $APP)."; exit 0; fi
mkdir -p "$APP"
cp server.py requirements.txt "$APP/"; rm -rf "$APP/static"; cp -R static "$APP/static"
# settings made by an older version lived beside the code
[[ -f config.json && ! -f "$APP/config.json" ]] && mv config.json "$APP/config.json" && chmod 600 "$APP/config.json"
cd "$APP"
[[ -x .venv/bin/python ]] || /usr/bin/python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
.venv/bin/python server.py --url >/dev/null   # makes config.json + the key on first run
# "Remote Screen.app" (no Dock icon) runs it, so macOS asks for and lists permissions under that name.
# Built only when missing or its source changed: a rebuilt app counts as new to macOS, which forgets what was allowed.
APPB="$APP/Remote Screen.app"
if [[ ! -x "$APPB/Contents/MacOS/RemoteScreen" ]] || ! cmp -s "$SRC/mac/launcher.c" "$APPB/Contents/Resources/launcher.c"; then
  rm -rf "$APPB"; mkdir -p "$APPB/Contents/MacOS" "$APPB/Contents/Resources"
  clang -O2 -o "$APPB/Contents/MacOS/RemoteScreen" "$SRC/mac/launcher.c"
  cp "$SRC/mac/launcher.c" "$APPB/Contents/Resources/"
  cat > "$APPB/Contents/Info.plist" <<IP
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>local.remote-screen</string>
  <key>CFBundleName</key><string>Remote Screen</string>
  <key>CFBundleDisplayName</key><string>Remote Screen</string>
  <key>CFBundleExecutable</key><string>RemoteScreen</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Listen plays this computer's microphone in your viewer.</string>
</dict></plist>
IP
  codesign --force --sign - "$APPB"
fi
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APPB/Contents/MacOS/RemoteScreen</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardErrorPath</key><string>$APP/launchd.log</string>
</dict></plist>
PL
for i in 1 2 3 4 5; do launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null && break; sleep 1; done   # right after bootout it can say "Input/output error"
echo "Running. Viewer: $(.venv/bin/python server.py --url)"
echo "On first start macOS asks for Screen Recording, Accessibility (Control) and Microphone for Remote Screen."
echo "Turned one down? The viewer shows what's missing, with buttons to ask again."
