---
title: About
description: What hearth is, who builds it, and where to find it.
---

hearth is built by **Eric Catalano** as a security-first NixOS system for running
local language models and sandboxed agents on hardware you control.

## The project

Most people running local agents run them with full system privileges and no
record of what they did. hearth takes the opposite stance: agents are sandboxed
by default, every run is audited to a local database, and the whole system state
is defined in one flake and legible from boot.

It is a declarative NixOS configuration, not a custom kernel or a remastered
distro. See [What is hearth](/hearth/getting-started/what-is-hearth/) for the full
framing and [Architecture](/hearth/concepts/architecture/) for how it fits
together.

## Links

- **Source code:** [github.com/EricFinland/hearth](https://github.com/EricFinland/hearth)
- **Documentation:** [ericfinland.github.io/hearth](https://ericfinland.github.io/hearth/)
- **Author:** [Eric Catalano on GitHub](https://github.com/EricFinland)

## Status

hearth is under active development. The documentation marks what is built today
versus what is on the roadmap, so nothing here oversells. For the current state,
see [Project status](/hearth/project/status/) and the
[Roadmap](/hearth/project/roadmap/).

## License

hearth is released under the MIT License. You are free to use, modify, and
distribute it. See the [LICENSE](https://github.com/EricFinland/hearth/blob/main/LICENSE)
file in the repository for the full text.

## Contributing and contact

Issues and pull requests are welcome on
[the GitHub repository](https://github.com/EricFinland/hearth). See
[Contributing](/hearth/project/contributing/) for the ground rules and the checks
to run, and the [Security model](/hearth/project/security/) for how to report
anything security-sensitive. If you are evaluating hearth for your own homelab or
want to discuss the design, the repo is the best place to start a conversation.
