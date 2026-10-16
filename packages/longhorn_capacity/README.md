# longhorn_capacity

Checkmk special agent and check plugin for **Longhorn disk capacity and health**: per node and disk, physical
usage, Longhorn's own scheduled/allocated usage (kept distinct from physical use), and a disk/node health
service.

See the [Changelog](CHANGELOG.md). The Checkmk objects around this package (the API-only host, the rule) are
managed with Terraform, see [ADR 0015 in isejalabs/homelab](https://github.com/isejalabs/homelab/blob/main/docs/decisions/0015-checkmk-configuration-as-code.md), [issue #1438](https://github.com/isejalabs/homelab/issues/1438) and the Terraform module [`checkmk-longhorn-monitoring`](https://github.com/isejalabs/terraform-modules/tree/main/modules/checkmk-longhorn-monitoring).

## How it works

- **Special agent** (`libexec/agent_longhorn_capacity`): calls `GET <endpoint>/v1/nodes` once and writes the
  section `longhorn_capacity`, one JSON object per node/disk. `<endpoint>` is the environment's existing
  Longhorn UI route (for example `http://longhorn.dev.iseja.net`) — the `longhorn-frontend` service proxies
  `/v1/*` straight through to `longhorn-backend` and carries no authentication of its own (same as opening the
  dashboard in a browser), so there is no identity to configure. One failed call loses every node/disk at once
  (there is no fixed, known item list the way there is for RustFS buckets), so a totally failed request writes
  a single reading with no node/disk identity instead of one failure per item.
- **Check** (`agent_based/longhorn_capacity.py`): three services per disk, item `"<node> <path>"`:
  - **"Longhorn disk `<node> <path>`"** — physical usage (`storageMaximum` as size, `storageAvailable` as free
    space): the real bytes-on-disk number.
  - **"Longhorn disk `<node> <path>` scheduled"** — Longhorn's own allocation bookkeeping: schedulable capacity
    (`storageMaximum - storageReserved`) against bytes already committed to replicas (`storageScheduled`). This
    can run high from thin provisioning well before physical usage does — it is a different number on purpose,
    not a duplicate of the first service.
  - **"Longhorn disk `<node> <path>` health"** — Longhorn's `conditions.Ready`/`Schedulable` and
    `allowScheduling` for the disk, plus the node's own `Ready` condition. CRIT if the node or disk is not
    Ready, WARN if Ready but not schedulable, UNKNOWN if Longhorn reported no condition status at all.
  - The two capacity services are modelled as filesystems, so Checkmk's own filesystem helper provides the
    used-space levels, growth trend and time until full through the existing ruleset **"Filesystems (used
    space and growth)"**, with the service's item as the discriminator — the same approach `rustfs_quota` uses.
- **Failures are never zero or OK.** A reading that could not be obtained makes every affected service UNKNOWN
  with the reason; a disk that vanishes from the output shows as not found, the same vanishing-service behavior
  any Checkmk agent section gives a missing item.

## Rule contract (v0)

The rule for the ruleset `special_agents:longhorn_capacity` has exactly this parameter, which the Terraform
module `checkmk-longhorn-monitoring` writes:

| Parameter | Value |
| --- | --- |
| `endpoint` | Longhorn base URL without a trailing slash, for example the environment's existing Longhorn UI route `http://longhorn.dev.iseja.net` |

Put the rule on a host **without a Checkmk agent and without an IP address** (`tag_agent = special-agents`,
`no-ip`). On a host with the normal agent a special-agent rule replaces its agent connection.

## Development

`scripts/mkp.sh` tests and builds the package inside a throwaway Checkmk Raw container (needs docker; the image
is about 3 GB):

```sh
scripts/mkp.sh test  longhorn_capacity   # unit tests with the site's Python
scripts/mkp.sh build longhorn_capacity   # dist/longhorn_capacity-<version>.mkp
```

The client tests (parsing, the stub-server client) also run with a plain
`PYTHONPATH=src python3 -m unittest discover -s tests -t .` from this folder; the check tests need a Checkmk
site's Python and are skipped there.

## Installing

Install a released `.mkp` on the Checkmk site with `mkp add <file>` and `mkp enable longhorn_capacity` (then
`omd restart apache` to load the ruleset); remove it with `mkp disable` and `mkp remove`. On the homelab this is
done through Salt with a pinned version. The package requires Checkmk 2.4.0 or newer.
