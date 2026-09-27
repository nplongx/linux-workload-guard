# Changelog

## [0.1.1] - 2026-09-27

Configuration and installation hardening.

### Changed

- `workload-router.service` automatically loads the host config file when present.
- Installer creates the host config from the example only when no config exists.
- Existing host configuration is never overwritten by upgrades.
- Tests cover the config wiring.

## [0.1.0] - 2026-09-27

First public release.

### Added

- Generic systemd/cgroup v2 workload CPU guard.
- Protected and heavy workload slices.
- Sustained CPU based automatic routing.
- Command-pattern based routing for common build/test/inference workloads.
- Configurable parent/target units and exclusion patterns.
- Optional browser automation CPU budget.
- `run-workload` helper for explicit heavy workloads.
- `workload-guard` status and diagnostics CLI.
- Shell/Python tests and GitHub Actions CI.
- Contributor and security documentation.
