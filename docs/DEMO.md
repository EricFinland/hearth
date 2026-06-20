# hearth Demo (Day 7)

## Audience

Personal portfolio. Potential employers or collaborators who want to see a
working, security-first local agent platform, not a slide deck.

## What to show

1. Boot the Proxmox VM and ssh in as `operator`.
2. Run `hearth-status` and walk through the output: Ollama active, Tailscale
   state, recent runs.
3. Start a real agent run and show the response.
4. Run `hearth-runs` and show the run's tokens and latency recorded in SQLite.
5. Run the sandbox self-test and show, line by line, what the isolation blocks.

## Commands that will be run

```
# system overview
hearth-status

# run a real agent against a local model (Ollama must be up and the model pulled)
hearth-agent --agent-name demo --model llama3.2:3b "Reply with a five word greeting."
# or, the packaged sandboxed+audited version:
sudo systemctl start hearth-demo-agent
journalctl -u hearth-demo-agent --no-pager

# the most recent agent runs with tokens and latency
hearth-runs

# prove the sandbox. This runs under the same profile as a real agent and
# reports the result of each boundary probe.
sudo systemctl start hearth-sandbox-selftest
journalctl -u hearth-sandbox-selftest --no-pager

# confirm Ollama is serving locally
curl -s http://localhost:11434/api/tags | jq .
```

## What the sandbox self-test actually proves

Be accurate about the claims. The self-test (modules/sandbox.nix) demonstrates:

- WRITE outside the allow list is denied. Writing to /etc or
  /var/lib/hearth/models fails (ProtectSystem=strict).
- WRITE inside the allow list works. /var/lib/hearth/agents is writable.
- READ of /root is denied (ProtectHome=true).
- READ of /var/lib/hearth/secrets is denied (mode 0700, owned by the hearth
  user, while the agent runs as a different DynamicUser id).

It does NOT claim to hide /etc/passwd. ProtectSystem makes the system read-only,
not invisible, and /etc/passwd is world-readable and holds no secrets. Hiding
the wider filesystem with a bind-mount allow list is a roadmap item, not a
current claim.

## Recording tool

asciinema. Record the terminal session with `asciinema rec hearth-demo.cast`,
then either upload it or convert to a gif for the README.

## What needs to be filled in after Day 4 and Day 5

- A real run in the audit store captured on the VM so `hearth-runs` shows
  non-empty output (the runner is built; this needs Ollama plus a pulled model).
- The TUI dashboard walkthrough (Day 5), replacing the plain `hearth-status`
  text output in step 2.
- The actual self-test journal output captured on the real VM, not assumed.
- TBD: runtime numbers (model load time, tokens per second on the 1660 Ti). Do
  not publish these until measured on the actual hardware.
