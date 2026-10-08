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

## [0.1.0] - 2026-10-08

### Added

- Special agent `agent_rustfs_quota`: for each configured bucket it queries `GET /rustfs/admin/v3/quota-stats/<bucket>` with an AWS Signature Version 4 signed request (standard library only) and writes one JSON reading per bucket into the section `rustfs_quota`, or the reason why none could be obtained. The secret key is a Password Store reference that the agent resolves itself, so it is never on a command line or in a log.
- Check plugin `rustfs_quota`: one service `RustFS bucket <name>` per bucket, built on Checkmk's filesystem helper so the usual used-space levels, growth trend and time until the quota is full apply (configured through the ruleset "Filesystems (used space and growth)" with the bucket name as the item). A failed reading is UNKNOWN, a bucket without quota reports usage only.
- Ruleset `special_agents:rustfs_quota` and server-side call implementing the rule contract v0: `endpoint`, `access_key`, `secret_key` (Password Store reference), `buckets`.
- Unit tests (SigV4 against the published AWS test vector and cross-checked against botocore, the client against a stub server, the check including trend and time left), and `scripts/mkp.sh` to test and build the package in a throwaway Checkmk Raw container.
