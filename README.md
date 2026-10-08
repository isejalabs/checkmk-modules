# checkmk-modules

Checkmk extension packages (MKP) for the isejalabs homelab: special agents and check plugins, built and released from here.

## Packages

| Package | Purpose | Status |
| --- | --- | --- |
| [`rustfs_quota`](packages/rustfs_quota) | Per-bucket quota and usage of the RustFS backup buckets: a special agent querying the RustFS admin API and a check plugin with one service per bucket, with levels, trend and time until full | implemented, not released yet; see [isejalabs/homelab#1529](https://github.com/isejalabs/homelab/issues/1529) |

## How it fits together

- The Checkmk objects around a package (Password Store entries, the API-only host, the special-agent rules) are managed with Terraform: [`checkmk-password`](https://github.com/isejalabs/terraform-modules/tree/main/modules/checkmk-password) and [`checkmk-rustfs-monitoring`](https://github.com/isejalabs/terraform-modules/tree/main/modules/checkmk-rustfs-monitoring) in `terraform-modules`, instantiated in [`isejalabs/homelab`](https://github.com/isejalabs/homelab). The decision and the reasoning are in ADR 0015 there.
- The released MKP is installed on the Checkmk site through Salt.

## Development

`scripts/mkp.sh test|build <package>` tests and builds a package in a throwaway Checkmk Raw container (needs docker); see `AGENTS.md` for the conventions. Contributions follow the shared conventions in [`isejalabs/commons`](https://github.com/isejalabs/commons) (included as the `.commons` submodule: run `git submodule update --init` after cloning).

## License

[MIT](LICENSE)
