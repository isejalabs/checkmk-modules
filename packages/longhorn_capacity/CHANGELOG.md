# Changelog

All _notable_ changes to this package will be documented in this file, following the [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. It won't include each and every change and commit (e.g. ci or documentation changes), but only the notable changes, in a human-readable format that commit messages sometimes do not provide.

This package tries to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Hence, a change to the version number X.Y.Z will indicate a

- **X+1: breaking changes** in **major** version numbers,
- **Y+1: feature updates** (and possbily bug fixes) in **minor** version numbers, and
- **Z+1: bug fixes** only in **patch** version numbers.

<!--
> [!NOTE]
> [!TIP]
> [!IMPORTANT]
> [!WARNING]
> [!CAUTION]
-->

<!---
## [Unreleased Template]
### Changed

### Added

### Removed

### Fixed

## [X.Y.Z] - YYYY-MM-DD
-->

## [Unreleased]

### Added

### Changed

### Removed

### Fixed

## [0.1.0] - 2026-10-16

### Added

- Special agent `agent_longhorn_capacity`: queries `GET <endpoint>/v1/nodes` once (the Longhorn API has no
  authentication of its own) and writes one JSON reading per node/disk into the section `longhorn_capacity`,
  or a single reading with the reason if the whole request failed.
- Check plugin `longhorn_capacity`: three services per disk (`Longhorn disk <node> <path>`,
  `... scheduled`, `... health`). The two capacity services are built on Checkmk's filesystem helper so the
  usual used-space levels, growth trend and time until full apply (configured through the ruleset
  "Filesystems (used space and growth)"), keeping physical usage and Longhorn's own scheduled/allocated
  usage as distinct services on purpose. The health service reports CRIT/WARN/UNKNOWN from Longhorn's disk and
  node readiness/scheduling conditions. A failed reading is UNKNOWN, never zero or OK.
- Ruleset `special_agents:longhorn_capacity` and server-side call implementing the rule contract v0: `endpoint`.
- Unit tests (the client against a stub server, the check including levels, scheduled-vs-physical divergence
  and health states), and `scripts/mkp.sh` to test and build the package in a throwaway Checkmk Raw container.
