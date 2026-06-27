---
title: Contributing
description: How to contribute to hearth, and the checks to run before you open a pull request.
---

Contributions are welcome, especially ones that deepen one of hearth's three
guarantees: agents sandboxed by default, every run audited, and the whole system
reproducible from boot. The canonical guide is
[CONTRIBUTING.md](https://github.com/EricFinland/hearth/blob/main/CONTRIBUTING.md);
this page is the overview.

## Ground rules

- **Keep the agent runtime dependency-light.** Everything under `agent/`,
  `webui/`, and `dashboard/` is Python 3 standard library only (the dashboard's
  optional TUI is the one lazy-imported exception). Do not add third-party Python
  packages to those paths.
- **Match the surrounding style.** Clear names, comments that explain *why*, no
  needless cleverness.
- **No em dashes** in committed files (code, docs, commit messages). Use periods,
  commas, or parentheses.

## Before you open a pull request

1. **Validate that the system evaluates:**

   ```sh
   nix flake check --no-build
   ```

   No Nix locally? The GitHub Actions `eval` job runs this on every push; just
   keep it green.

2. **Run the module self-tests.** The agent modules carry their own checks via an
   in-module `--self-test` (no pytest, no network, no Ollama):

   ```sh
   for m in agent/*.py; do python3 "$m" --self-test 2>/dev/null; done
   ```

   Anything you change under `agent/` should keep its self-test green and,
   ideally, add an assertion for the behavior you added.

3. **Keep commits clean and atomic.** One logical change per commit, an imperative
   message (`feat: add X`, `fix: Y`).

## Project layout

| Path | What lives there |
| --- | --- |
| `flake.nix`, `nixos/` | The declarative system: host configs and modules. |
| `agent/` | The agent loop, permission engine, swarm, marathon, self-evolve, memory. |
| `webui/` | The cockpit and map backend and static pages. |
| `dashboard/`, `tui/` | The boot dashboard. |
| `site/` | This documentation website (Astro Starlight). |
| `docs/` | In-repo reference docs. |

## Bugs, ideas, and security

Open a GitHub issue for bugs and feature requests. For anything
security-sensitive, follow the [Security model](/hearth/project/security/) and the
repository's SECURITY.md instead of filing a public issue.

By contributing, you agree your contributions are licensed under the repository's
MIT License.
