# Install on macOS

Works on Intel and Apple Silicon Macs. The same `install.sh` as on Linux is used; on macOS it installs
everything through [Homebrew](https://brew.sh).

## 1. Homebrew

If `brew --version` doesn't work yet, install Homebrew first (it asks for your password once):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

At the end Homebrew prints two `eval "$(/opt/homebrew/bin/brew shellenv)"` lines (Apple Silicon); run them,
or open a new terminal, so that `brew` is on your PATH.

## 2. Cloudify

```bash
curl -fsSL https://raw.githubusercontent.com/VickyDev810/Cloudify-Tunnel-Manager/main/install.sh | bash
```

The installer:

1. backs up and replaces an existing installation, including Cloudify 1.x ([Updating](updating.md));
2. installs what is missing with Homebrew: Python 3 (only if the system one is older than 3.8), pipx and git;
3. installs or upgrades **cloudflared** with Homebrew (`brew install cloudflared`);
4. installs Cloudify with pipx, on the Python pipx itself runs on (its own environment; nothing is installed
   into the system Python);
5. adds `~/.local/bin` to your shell's PATH (zsh is the default shell on macOS).

Open a new terminal window, then check:

```bash
cloudify --version
cloudflared --version
```

All installer options (`--update`, `--branch`, `--local`, `--uninstall`, ...) are listed in
[Install on Linux](install-linux.md#options); they work the same on macOS.

If macOS asks to install the **Command Line Tools** (needed for `git`), accept, wait for it to finish and run
the installer again. If `git` is unavailable you can also install from a downloaded copy:
download the ZIP from GitHub, unpack it and run `bash install.sh --local <unpacked-folder>`.

## Auto-start on macOS

Tunnels with auto-start get a **LaunchAgent** (`~/Library/LaunchAgents/com.user.cloudflare.tunnel.<name>.plist`):
launchd starts them when you log in and restarts them if they exit with an error.

```bash
cloudify tunnel autostart web --status
launchctl list | grep cloudflare                                   # loaded agents
tail -f ~/Library/Logs/cloudflared-tunnel-web.error.log            # cloudflared writes its log here
```

Tunnels started by hand (`cloudify tunnel start web` without auto-start) log to `~/.cloudflared/logs/web.log`.

LaunchAgents run only while your user is logged in. For a Mac that runs without anyone logged in (a Mac mini
server), turn on **automatic login** (System Settings → Users & Groups → Automatically log in as), or run
Cloudify in [Docker](docker.md).

## Run the dashboard as a service

To keep the dashboard running after login, create a LaunchAgent for `cloudify serve` (replace `YOU` with
your user name, `whoami`). launchd starts programs with a minimal PATH, so the plist sets one that includes
Homebrew's folder, where `cloudflared` lives:

```bash
cat > ~/Library/LaunchAgents/com.cloudify.dashboard.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.cloudify.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/YOU/.local/bin/cloudify</string>
    <string>serve</string>
    <string>--secure</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/YOU/Library/Logs/cloudify-dashboard.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/YOU/Library/Logs/cloudify-dashboard.log</string>
</dict>
</plist>
EOF
launchctl load -w ~/Library/LaunchAgents/com.cloudify.dashboard.plist
```

Open `http://localhost:8765`. Stop it with `launchctl unload -w ~/Library/LaunchAgents/com.cloudify.dashboard.plist`.
`--secure` keeps the dashboard on this Mac. Without it, the dashboard is open to your network: it listens on every
interface, over plain HTTP unless you give it a certificate. Read [Security](security.md) first.

## Notes

* **Apple Silicon vs Intel:** Homebrew lives in `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel;
  the installer handles both. Cloudify 1.x installed an Intel-only cloudflared into `/usr/local/bin`; the
  installer replaces it on Intel Macs and tells you how to remove it on Apple Silicon.
* **Firewall:** tunnels only make outbound connections, so the macOS firewall needs no changes. If it asks
  whether Python may accept incoming connections when you run `cloudify serve`, allow it only if you want
  to open the dashboard from other devices.
* **Sleep:** a sleeping Mac disconnects its tunnels; they reconnect on wake. Prevent sleep on a server
  (System Settings → Energy, or `caffeinate -s`).
* **SSH sharing** (`cloudify -ssh host`) gives the remote a real terminal on macOS. Remote Login (the macOS
  SSH server) does not need to be turned on.

## Next steps

* [Getting started](getting-started.md)
* [Updating and uninstalling](updating.md)
* [Troubleshooting](troubleshooting.md)
