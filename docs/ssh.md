# SSH sharing

Give someone temporary shell access to a machine (a server behind NAT, a colleague's laptop, a Raspberry Pi)
with two commands. No open ports, no router or firewall changes, no Cloudflare account, no editing of
`~/.ssh`. It works through a temporary Cloudflare tunnel and ends the moment you press Ctrl+C.

| | Machine that shares (**host**) | Machine that connects (**remote**) |
| --- | --- | --- |
| Command | `cloudify -ssh host` | `cloudify -ssh remote` |
| Needs | Cloudify, cloudflared | Cloudify, cloudflared, the OpenSSH client (`ssh`) |
| An SSH server (sshd)? | Not needed: Cloudify brings its own | Not needed |

## 1. Share (on the host)

```console
$ cloudify -ssh host
Starting a temporary Cloudflare tunnel...

  SSH access to alice@build-server is ready (expires in 60 minutes)

  On the other machine run:   cloudify -ssh remote
  and enter:
    Access code   cfyssh1.eNpNj8tOwzAQRf...
    Password      k7mx-2qpr-9tzw-h4cn

  Or run this one-line command there (it includes the password, use it only if you want to):
    cloudify -ssh remote --code cfyssh1.eNpNj8tOwzAQRf... --password k7mx-2qpr-9tzw-h4cn

  Anyone with the code and the password gets a shell as this user until you stop sharing.
  Safest is to send them separately (e.g. code by chat, password by phone) rather than the
  one-line command. Several people can be connected at the same time.
  Host key: SHA256:...

Waiting for connections... (Ctrl+C to stop sharing)
[14:02:11] ● remote connected (session 1 · 1 active)
```

Options:

| Option | Effect |
| --- | --- |
| `--ttl 15` | Stop sharing after 15 minutes (default 60, maximum 1440). |
| `--once` | Stop sharing when the first session ends. |
| `--save-code code.txt` | Also write the access code to a file (readable only by you). |

Stop sharing with **Ctrl+C** (or closing the terminal, or `kill`): every connected remote is disconnected at
once, their shells and commands end, and the address and code stop working. A second Ctrl+C quits immediately.

## 2. Connect (on the remote)

Either type the two values (safer), or paste the one-line command the host printed (quicker). It's your choice.

```bash
cloudify -ssh remote                              # asks for the access code, then the password (hidden)
cloudify -ssh remote --code-file code.txt         # read the code from a file
cloudify -ssh remote --code cfyssh1.... --password xxxx-xxxx-xxxx-xxxx    # the one-line command
```

Instead of an interactive shell:

```bash
cloudify -ssh remote -- uptime                     # run one command and return
cloudify -ssh remote -- 'df -h && free -m'
cloudify -ssh remote --sftp                        # file transfer: put, get, ls, cd, exit
```

The shell runs **as the user who ran `cloudify -ssh host`**, starting in their home folder, with a real terminal
(colors, `vim`, `htop`, resizing) on Linux and macOS hosts. Several remotes can be connected at the same time;
the host prints each connection and disconnection.

## How safe is it?

* **The code and the password are both needed.** The access code contains the tunnel address and a one-time
  private key, encrypted with the password (scrypt + AES-256-GCM). The host only accepts that key; it never
  accepts passwords, so the password can't be guessed over the network.
* **Send them over two different channels** (code by chat, password by phone). The one-line command contains both,
  so anyone who sees it can connect; it also stays in the remote's shell history. It does not stay visible to
  other users of the remote in the process list: Cloudify moves the password out of its command line at start.
* **No man-in-the-middle:** the host's key is pinned by the code; the remote never asks you to trust a host.
* **Short-lived:** keys and password are new for every share and exist only in memory on the host. Nothing is
  added to `~/.ssh/authorized_keys`; no system SSH settings are touched.
* **Limited:** the server listens only on `127.0.0.1` behind the tunnel; port, agent and X11 forwarding are refused.
  At most 16 connections may be open at once, and one that hasn't authenticated within 20 seconds is dropped, so
  someone who finds the address can't flood the host. But the remote gets the same rights as your user: share as
  a normal user, never as root.

Internals and code layout: [`cloudify/ssh/README.md`](../cloudify/ssh/README.md).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `cloudflared not found` | Install it: re-run the installer, or see [Install on Linux](install-linux.md#manual-installation). Both machines need it. |
| `ssh: command not found` on the remote | Install the OpenSSH client: `sudo apt install openssh-client` (Debian/Ubuntu), `sudo dnf install openssh-clients` (Fedora/RHEL). macOS and Windows 10+ include it. |
| *Wrong password, or the access code was changed* | Check the password (letters and digits in groups of four). |
| *The access code is incomplete or damaged (copy the whole line)* | The code is long; copy all of it, without line breaks. |
| *This access code has expired; ask for a new one* | The host's `--ttl` ran out. Ask for a new code. |
| *Can't find ... in DNS. Is the host still sharing?* | The host stopped sharing (Ctrl+C, `--once`, `--ttl`), or its tunnel stopped. Ask for a new code. |
| *Could not start the tunnel* on the host | The machine can't reach Cloudflare (proxy or firewall blocking outbound 443/7844), or TryCloudflare is temporarily unavailable. Try again. |
| Connecting hangs for a while right after the code appears | The new address needs a moment to become reachable; the host already waits for that, but slow DNS resolvers can add a few seconds. |
| Full-screen programs misbehave | The host is on Windows (no terminal support there); commands and SFTP still work. |
