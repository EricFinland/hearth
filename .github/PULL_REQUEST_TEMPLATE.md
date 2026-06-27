# What this changes

<!-- A short description of the change and why it matters. -->

## Checklist

- [ ] `nix flake check --no-build` passes (or the CI eval job is green)
- [ ] Any changed `agent/*.py` modules keep their `--self-test` green (and gained an assertion if behavior changed)
- [ ] No new third-party dependencies under `agent/`, `webui/`, or `dashboard/` (standard library only)
- [ ] No secrets, tokens, or private keys in the diff
- [ ] No em dashes in committed files
