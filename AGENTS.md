# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, and others reading `AGENTS.md`) when working with code in this repository. `CLAUDE.md` is a symlink to this file.

**Before relying on the import below**: `.commons` is a git submodule, not a regular directory — a plain `git clone` of this repo leaves it empty, and the `@`-import then silently pulls in nothing, with no error either way. Run `git submodule status .commons` first; a leading `-` on the printed commit hash means it's uninitialized. If so, run `git submodule update --init .commons` before trusting anything below to reflect the imported file's actual content.

@.commons/agents/AGENTS.common.md

## About

Checkmk extension packages (MKP) for the isejalabs homelab: special agents and check plugins written in Python against the Checkmk plugin APIs, built and released from here. The monitoring itself (the Checkmk site on `monitoring2`) and its configuration live elsewhere: the Checkmk objects (Password Store entries, hosts, rules) are managed with Terraform in [isejalabs/terraform-modules](https://github.com/isejalabs/terraform-modules) and [isejalabs/homelab](https://github.com/isejalabs/homelab) (see ADR 0015 there), and the host itself is managed with Salt. This repo only builds the plugins that run inside that Checkmk site.

The first package is the RustFS bucket quota monitoring, tracked in [isejalabs/homelab#1529](https://github.com/isejalabs/homelab/issues/1529). It has to implement the rule contract that the `checkmk-rustfs-monitoring` Terraform module writes: a rule for the ruleset `special_agents:rustfs_quota` with `endpoint`, `access_key`, `secret_key` (a Password Store reference) and `buckets`.

## Layout and conventions

- One folder per package (an MKP), versioned independently, tagged `<package>-v<semver>`, each with its own `CHANGELOG.md` in [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format (same convention as `terraform-modules`). The built MKP is attached to the GitHub Release.
- A package lives in `packages/<name>/` with its plugin tree in `src/cmk_addons/plugins/<name>/`, its unit tests in `tests/` (stdlib `unittest`, so they run with the site's Python), a `<name>.manifest` (the MKP manifest) and a README and CHANGELOG. `scripts/mkp.sh test|build <name>` tests and builds a package inside a throwaway Checkmk Raw container; CI runs the tests the same way.
- A package mirrors the layout under `~/local/lib/python3/cmk_addons/plugins/<family>/` of a Checkmk 2.4 site: `libexec/` (the special agent executable), `rulesets/`, `server_side_calls/`, `agent_based/`, `graphing/`.
- Python 3.12, the version of the Checkmk 2.4 site. Do not rely on libraries beyond what the site bundles; as of 2.4.0p37 that includes `requests`, `urllib3`, `boto3`/`botocore` and `pydantic`, but check the target version before depending on any of them, and prefer the standard library.
- A special agent must never receive a secret on its command line or log one. A rule passes a Password Store reference (`<id>:<path>`), which the agent resolves itself with `cmk.utils.password_store.lookup`; an unresolved reference or a failed request is reported as UNKNOWN or an agent error, never as a zero value.
- Unit tests with pytest; a live check against a throwaway container (`checkmk/check-mk-raw:2.4.0-latest`, Raw edition like the production site) before a release.
- Installing a released MKP on `monitoring2` is done through Salt with a pinned version, not by hand.
