# cloudify/ssh: temporary SSH access through Cloudflare

Share a shell on one machine with someone on another machine, without open
ports, firewall changes, a Cloudflare account or editing `~/.ssh`.

```
machine A (host)                               machine B (remote)
$ cloudify -ssh host                           $ cloudify -ssh remote
  Access code   cfyssh1.Xk3...                   Access code: cfyssh1.Xk3...
  Password      k7mx-2qpr-9tzw-h4cn              Password: ****
                                                 ai@machine-a:~$
  Or run this one-line command there:
    cloudify -ssh remote --code cfyssh1.Xk3... --password k7mx-2qpr-9tzw-h4cn
```

The host always prints both ways to connect: the access code and the password to type in, and a
one-line command that contains both. Typing them in (sent over separate channels) is the safer way;
the one-line command is quicker. Which one to use is up to you.

## Usage

| Command | What it does |
| --- | --- |
| `cloudify -ssh host` | Share this machine. Prints an access code, a password and a one-line remote command. Ctrl+C stops sharing. |
| `cloudify -ssh host --once` | Stop sharing when the first session ends |
| `cloudify -ssh host --ttl 15` | Stop sharing after 15 minutes (default 60, max 1440) |
| `cloudify -ssh host --save-code code.txt` | Also write the access code to a file (mode 600) |
| `cloudify -ssh remote` | Connect; asks for the access code and the password |
| `cloudify -ssh remote --code-file code.txt` | Read the code from a file |
| `cloudify -ssh remote --code CODE --password PW` | Non-interactive: the one-line command the host prints |
| `cloudify -ssh remote -- uptime` | Run one command instead of opening a shell |
| `cloudify -ssh remote --sftp` | Open an SFTP session (file transfer) |

`cloudify ssh host` / `cloudify ssh remote` are the same commands.

**Several remotes** can be connected at the same time; each gets its own shell, and the host shows
`● remote connected (session 3 · 2 active)` / `○ remote disconnected (1 active)`.

**Stopping is immediate:** Ctrl+C (or `kill`, closing the terminal, `--ttl`, `--once`) disconnects every
remote, ends their shells and commands, and stops the tunnel within about a second; a second Ctrl+C
quits instantly. Remotes see "the host stopped sharing".
Requirements: the host needs `cloudflared`; the remote needs `cloudflared` and the OpenSSH client (`ssh`).

## How it works

1. **Host** generates one-time credentials: a client key pair (Ed25519), a host key (Ed25519) and a
   password (16 random characters, ~79 bits, shown as `xxxx-xxxx-xxxx-xxxx`).
2. It starts a small SSH server (asyncssh) on `127.0.0.1` at a random port and a Cloudflare
   **quick tunnel** to it (`cloudflared tunnel --url ssh://127.0.0.1:PORT`), which gets a random
   `*.trycloudflare.com` address. It waits until that address is published (checked with Cloudflare's
   DNS-over-HTTPS so the local DNS cache isn't poisoned), then prints the code.
3. The **access code** contains the address, the client *private* key and the host public key,
   compressed and encrypted with the password (scrypt + AES-256-GCM).
4. **Remote** decrypts the code, writes the key and a `known_hosts` entry pinning the host key to a
   private temporary folder (deleted afterwards), and runs
   `ssh -o ProxyCommand="cloudflared access ssh --hostname %h" ...` with strict host key checking.

## Security model

* **Both the code and the password are needed.** The host's server accepts *only* the session key and
  never accepts passwords (no password, keyboard-interactive or host-based login), so the password can't be
  guessed over the network. Without the password the code can't be decrypted: offline guessing of a
  79-bit password through scrypt is infeasible.
* **Send them separately** (e.g. code by chat, password by phone) so one leaked channel isn't enough.
* **No man-in-the-middle:** the host key is pinned from the code; the remote never asks "trust this host?".
* **Short-lived:** everything is generated per session and kept in memory on the host. Ctrl+C, `kill`,
  closing the terminal, `--ttl` or `--once` stop the server *and* the tunnel; the address and the code
  stop working. Nothing is added to `~/.ssh/authorized_keys` or system files.
* **Limited:** listens only on `127.0.0.1`; port, agent and X11 forwarding are refused. The remote
  gets a shell and SFTP/SCP **as the user who ran `cloudify -ssh host`**, the same access as that user.
  Don't share as root.
* Every connection and disconnection is printed on the host.
* The **one-line command** holds the code **and** the password: quicker, but whoever sees that line can
  connect, so send it only over a channel you trust, or prefer short sessions (`--once`, `--ttl`). It also
  lands in the remote's shell history. It does *not* stay in the remote's process list: `cloudify -ssh
  remote` restarts itself at once with the password moved to its environment (readable only by that user
  and root), so other users can't see it with `ps` during the session.
* If a remote disappears (network loss, closed laptop), its shell or command is ended right away, as sshd
  would do; nothing keeps running on the host.
* `CLOUDIFY_SSH_PASSWORD` exists for scripts and tests; people should type the password.

## Limitations

* The host shell gets a real terminal (PTY) on Linux and macOS. On Windows hosts the shell runs without
  a PTY (commands and SFTP work; full-screen programs don't).
* Quick tunnels are meant for temporary use (Cloudflare's TryCloudflare service), not production.

## Code layout (extend here)

| File | Purpose |
| --- | --- |
| `credentials.py` | Key/password generation; access code encryption (`make_access_code`, `open_access_code`) |
| `server.py` | `SessionServer`: the asyncssh server, PTY shell, command execution, SFTP/SCP |
| `host.py` | `run_host()`: server + quick tunnel + printing + stop conditions |
| `remote.py` | `connect()`: decrypt, temporary key/known_hosts, run `ssh`/`sftp` via `cloudflared access ssh` |
| `cli.py` | `cloudify ssh host|remote` and the `-ssh` flag |

The only hook into the rest of Cloudify is in `cloudify/cli.py` (registering `ssh_group` and translating
`-ssh MODE ...` to `ssh MODE ...`); it reuses `cloudify.core.quick_tunnel` to start the tunnel.
Ideas that fit here later: port forwarding with an explicit allow-list, read-only/restricted shells,
multiple named users per session, session recording, named tunnels on your own domain instead of
trycloudflare.com.

Tests: `tests/test_ssh.py` (local end-to-end without Cloudflare, plus an opt-in real-tunnel test).
