# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Report them privately through GitHub:
**Security → Report a vulnerability** on this repository (GitHub's private vulnerability reporting).

Include what you found, the affected version (`cloudify --version`), how to reproduce it with dummy data,
and what an attacker could do. You will get an answer as soon as possible; fixes are released as a new
version and mentioned in the release notes.

## Supported versions

Only the latest release receives security fixes. Update with
`install.sh --update` ([docs/updating.md](docs/updating.md)) or rebuild the Docker image.

## Using Cloudify safely

See [docs/security.md](docs/security.md): exposing the dashboard, users and sessions, secrets on disk,
replica files, SSH sharing, the installer and Docker.
