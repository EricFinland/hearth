# hearth Roadmap

This is the working task list. Check items off as you go.

## Day 1: Foundation (de-risk the loop)

- [ ] flake.nix evaluates, `nix flake check` passes
- [ ] base.nix builds a minimal bootable NixOS config
- [ ] produce an image and boot it as a Proxmox VM
- [ ] confirm ssh in and `nixos-rebuild switch` works against the repo

## Day 2: LLM layer

- [ ] Ollama running as a systemd service on boot
- [ ] declarative model manifest: list models in config, they get pulled on activation
- [ ] GPU passthrough working for the 1660 Ti, verify a model runs on the GPU

## Day 3: Agent runtime

- [ ] agent framework installed and launchable
- [ ] defined agent home layout under /var/lib/hearth
- [ ] secrets handling working (no plaintext keys in the repo)

## Day 4: Sandbox and observability (the differentiators)

- [ ] agents run under least privilege, prove an agent cannot read outside its allowed paths
- [ ] every agent run logs cost, tokens, latency, errors to the local store
- [ ] one query that shows the last N runs with their stats

## Day 5: Shell and dashboard

- [ ] login boots into a TUI showing system state, running agents, model status, recent runs, spend

## Day 6: Packaging

- [ ] build-image.sh produces a clean distributable image
- [ ] bootstrap.sh applies the config to a fresh NixOS host in one command
- [ ] polish first-boot experience

## Day 7: Demo and decision

- [ ] record a short demo, write DEMO.md
- [ ] clean the README
- [ ] decide: ship publicly or keep private
