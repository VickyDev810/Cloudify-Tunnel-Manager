"""Commands: `cloudify ssh host|remote` (also `cloudify -ssh host|remote`)."""

import os
import sys

import click


@click.group("ssh")
def ssh_group():
    """Share this machine's SSH shell through a temporary tunnel, or connect to one"""


@ssh_group.command("host")
@click.option("--ttl", type=float, default=60, show_default=True, help="Minutes until sharing stops")
@click.option("--once", is_flag=True, help="Stop sharing when the first session ends")
@click.option("--save-code", type=click.Path(dir_okay=False), help="Also write the access code to this file")
@click.option("--local", is_flag=True, hidden=True)  # testing: no Cloudflare tunnel
def host_cmd(ttl, once, save_code, local):
    """Share this machine: prints an access code, a password and a one-line remote command"""
    from cloudify.ssh.host import run_host
    if ttl <= 0 or ttl > 24 * 60:
        raise click.BadParameter("use 1 to 1440 minutes", param_hint="--ttl")
    sys.exit(run_host(out=lambda m: click.echo(m), ttl_minutes=ttl, once=once, local=local,
                      save_code=save_code))


@ssh_group.command("remote", context_settings={"ignore_unknown_options": True})
@click.option("--code", help="Access code (asked for if not given)")
@click.option("--code-file", type=click.Path(exists=True, dir_okay=False), help="Read the access code from a file")
@click.option("--password", "password_opt", help="Password (asked for if not given; typing it is safer)")
@click.option("--sftp", is_flag=True, help="Open an SFTP session instead of a shell")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
def remote_cmd(code, code_file, password_opt, sftp, command):
    """Connect to a shared machine (asks for the access code and password)

    Add a COMMAND after `--` to run it instead of opening a shell, e.g.
    `cloudify ssh remote -- uptime`.
    """
    from cloudify.ssh.remote import RemoteError, connect
    if password_opt:
        _restart_without_password_in_argv(code, code_file, password_opt, sftp, command)
    if code_file:
        with open(code_file) as f:
            code = f.read(64 * 1024)  # a real code is under 1 KB; never read an arbitrarily large file
    if not code:
        code = click.prompt("Access code", prompt_suffix=": ")
    # CLOUDIFY_SSH_PASSWORD is for scripts/automation; people should type it. Removed from the
    # environment so ssh, cloudflared and the commands they start don't inherit it.
    password = (password_opt or os.environ.pop("CLOUDIFY_SSH_PASSWORD", None)
                or click.prompt("Password", hide_input=True, prompt_suffix=": "))
    try:
        sys.exit(connect(code, password, command=command, sftp=sftp))
    except RemoteError as e:
        raise click.ClickException(str(e))


def _restart_without_password_in_argv(code, code_file, password, sftp, command):
    """`--password` on the command line can be read by other users of this machine (`ps`) for
    as long as the session lasts. Start again at once with the password in the environment
    instead, which only this user and root can read. POSIX only; elsewhere carry on as is."""
    if os.name != "posix" or not sys.executable or os.environ.get("_CLOUDIFY_SSH_REEXEC"):
        return
    # `-m cloudify` would import a "cloudify" folder from the working directory first;
    # this drops the working directory from the import path before importing Cloudify.
    bootstrap = ("import sys; sys.path[:] = [p for p in sys.path if p not in ('', '.')]; "
                 "sys.argv[0] = 'cloudify'; from cloudify.cli import cli; cli()")
    argv = [sys.executable, "-c", bootstrap, "ssh", "remote"]
    if code:
        argv += ["--code", code]
    if code_file:
        argv += ["--code-file", code_file]
    if sftp:
        argv.append("--sftp")
    if command:
        argv += ["--", *command]
    env = dict(os.environ, CLOUDIFY_SSH_PASSWORD=password, _CLOUDIFY_SSH_REEXEC="1")
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execve(sys.executable, argv, env)
    except (OSError, ValueError):
        pass  # could not restart: connect anyway


def run_flag(ctx: click.Context, mode: str):
    """Handle `cloudify -ssh host|remote` (same as the subcommands with defaults)."""
    ctx.invoke(host_cmd if mode == "host" else remote_cmd)
