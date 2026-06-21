# hearth

An opinionated, reproducible, security-first Linux system where local LLMs and agents run sandboxed by default, every agent run is audited, and system state is legible from boot.

**📖 Documentation: https://ericfinland.github.io/hearth/**

## What this is / what it is not

hearth is a declarative NixOS configuration for running local language models and autonomous agents. It is not a custom Linux kernel or a remastered distro. It is a single flake.nix that Nix builds reproducibly and deploys to any NixOS host or Proxmox VM.

## Why

Most people running local agents are flying blind, running them with full system privileges and no record of what they did. hearth makes agent activity legible and contained at the OS level. Every agent run is sandboxed via systemd isolation primitives, and every run records its token count, cost, latency, and any errors to a local SQLite database. You can query the last 20 runs in one command.

## Quickstart

```
# clone
git clone https://github.com/YOUR_USERNAME/hearth
cd hearth

# validate the flake (first run fetches inputs, takes a few minutes)
nix flake check

# build a Proxmox-compatible image
bash scripts/build-image.sh

# apply to an existing NixOS host
bash scripts/bootstrap.sh
```

## Documentation

Full documentation lives at **https://ericfinland.github.io/hearth/**.

In-repo sources:

- [Roadmap](docs/ROADMAP.md): the day-by-day build plan.
- [Architecture](docs/ARCHITECTURE.md): system diagram, module responsibilities, and the threat model.

## License

MIT. See [LICENSE](LICENSE).
