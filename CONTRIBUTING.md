# Contributing to hearth

Thanks for your interest. hearth is a declarative NixOS system for running local
LLMs and sandboxed agents. Contributions that deepen one of its three
guarantees, agents sandboxed by default, every run audited, and the whole system
reproducible from boot, are especially welcome.

## Ground rules

- Keep the agent runtime dependency-light. Everything under `agent/`, `webui/`,
  and `dashboard/` is Python 3 standard library only (the dashboard's optional
  TUI is the one exception and is lazy-imported). Do not add third-party Python
  packages to those paths.
- Match the surrounding style: clear names, comments that explain *why*, no
  needless cleverness.
- No em dashes in committed files (code, docs, commit messages). Use periods,
  commas, or parentheses.

## Before you open a pull request

1. **Validate the system evaluates.** From the repo root:
   ```sh
   nix flake check --no-build
   ```
   If you do not have Nix locally, the GitHub Actions `eval` job runs this on
   every push; watch that it stays green.

2. **Run the module self-tests.** The agent modules carry their own checks via
   an in-module `--self-test` (no pytest, no network, no Ollama needed):
   ```sh
   for m in agent/*.py; do python3 "$m" --self-test 2>/dev/null; done
   ```
   Anything you change under `agent/` should keep its self-test green and, ideally,
   gain a new assertion for the behavior you added.

3. **Keep commits clean and atomic.** One logical change per commit, a clear
   message in the imperative mood (for example, `feat: add X`, `fix: Y`).

## Project layout

- `flake.nix`, `nixos/` ... the declarative system (host configs, modules).
- `agent/` ... the tool-using agent loop, permission engine, swarm, marathon,
  self-evolve, memory.
- `webui/` ... the cockpit/map backend and static pages.
- `dashboard/`, `tui/` ... the boot dashboard.
- `site/` ... the documentation website (Astro Starlight).
- `docs/` ... in-repo reference docs.

## Reporting bugs and ideas

Open an issue for bugs and feature requests. For anything security-sensitive,
follow [SECURITY.md](SECURITY.md) instead of filing a public issue.

By contributing, you agree that your contributions are licensed under the
repository's [MIT License](LICENSE).
