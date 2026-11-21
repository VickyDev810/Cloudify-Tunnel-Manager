"""Temporary SSH access through a Cloudflare quick tunnel.

    machine A (host)                          machine B (remote)
    cloudify -ssh host                        cloudify -ssh remote
      ├─ one-time keys + password               ├─ asks for access code + password
      ├─ SSH server on 127.0.0.1                ├─ decrypts code, pins host key
      └─ quick tunnel *.trycloudflare.com  <──  └─ ssh via `cloudflared access ssh`

Kept separate from the tunnel manager on purpose: everything lives in this
package (credentials, server, host, remote, cli) and only `cloudify/cli.py`
hooks the commands in. See README.md in this folder.
"""
