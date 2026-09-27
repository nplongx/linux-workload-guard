# OpenClaw CPU Guard

Linux/systemd CPU guard for OpenClaw child workloads on CPU-limited machines.

## What it does

- Caps the OpenClaw gateway at 150% CPU.
- Gives heavy work a separate `terminal-heavy.slice` capped at 400% CPU.
- Auto-routes known heavy OpenClaw child commands.
- Auto-routes unknown OpenClaw descendants after sustained CPU >= 70% for 8 seconds.
- Excludes the gateway, router, ChatGPT automation Chrome, ChromeDriver and crashpad.
- Caps ChatGPT automation Chrome at 200% CPU.
- Provides `run-task` for explicit heavy terminal work.

## Install

```bash
./install.sh
```

Installs user-level systemd units and helpers, enables the router and Chrome budget timer, and restarts an active OpenClaw gateway so the CPU quota applies.

## Usage

```bash
run-task npm run build
run-task cargo test
run-task pytest
run-task docker build .
```

Or enter the heavy slice:

```bash
run-task
```

Normal commands run normally.

## Detection

The router samples OpenClaw descendants every 2 seconds. Known build/test/compile/inference commands are routed immediately. Unknown commands are routed after four consecutive samples at or above the CPU threshold: 4 x 2 seconds = 8 seconds.

The sustained threshold avoids catching short-lived CPU spikes.

## Configuration

Environment variables:

- `OPENCLAW_CPU_GUARD_SAMPLE_SEC` — default `2`
- `OPENCLAW_CPU_GUARD_SUSTAINED_SAMPLES` — default `4`
- `OPENCLAW_CPU_GUARD_CPU_THRESHOLD` — default `70`
- `OPENCLAW_CPU_GUARD_MAX_ANCESTRY` — default `32`

The router resolves the target cgroups through `systemctl --user`, so it does not depend on a hard-coded numeric UID or home directory.

## Verify

```bash
systemctl --user status openclaw-heavy-task-router.service
systemctl --user status terminal-heavy.slice
systemctl --user show openclaw-gateway.service -p CPUQuotaPerSecUSec -p ControlGroup
systemctl --user show terminal-heavy.slice -p CPUQuotaPerSecUSec -p ControlGroup
journalctl --user -u openclaw-heavy-task-router.service -n 50 --no-pager
```

Expected quotas:

- gateway: `150000 100000`
- heavy slice: `400000 100000`
- ChatGPT Chrome slice: `200000 100000`

## Uninstall

```bash
./uninstall.sh
```

Only files installed by this project are removed. OpenClaw data/projects are not deleted.

## Testing

```bash
./test.sh
```

Tests cover Python syntax and detection/exclusion logic. A real end-to-end routing test requires an OpenClaw child process and is deliberately not faked.

## License

MIT
