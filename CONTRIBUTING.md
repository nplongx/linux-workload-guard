# Contributing

## Scope

Keep the project generic. Application-specific behavior belongs in host integration/configuration, not in the core router.

## Development

Requirements:

- Linux with cgroup v2
- Python 3
- systemd user manager
- POSIX shell

Run the local checks:

```bash
./test.sh
```

Also validate systemd units when available:

```bash
systemd-analyze --user --no-pager verify systemd/*.service systemd/*.timer systemd/*.slice
```

Do not test by modifying a user's production cgroups unless explicitly intended.

## Pull requests

Include:

- what changed
- why it changed
- test results
- compatibility impact

Avoid hard-coded usernames, home directories, distribution-specific paths, or application names in the core.
