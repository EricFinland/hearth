# hearth Demo (Day 7)

## Audience

Personal portfolio. Potential employers or collaborators who want to see a
working, security-first local agent platform, not a slide deck.

## What to show

1. Boot the Proxmox VM and ssh in.
2. Run `hearth-status` and walk through the output: Ollama active, Tailscale
   state, recent runs.
3. Start an agent run (real command TBD once the agent launcher lands on Day 3).
4. Run `hearth-runs` and show the run's tokens, cost, latency, and any error
   recorded in the SQLite store.
5. Prove the sandbox: start a sandboxed agent process and show it cannot read
   /etc/passwd or anything outside its allowed paths.

## Commands that will be run

```
# system overview
hearth-status

# the most recent agent runs with cost and latency
hearth-runs

# prove the sandbox blocks reads outside the allow list.
# Run inside the agent's sandboxed unit context; ProtectSystem=strict plus
# ProtectHome should make this fail rather than print the file.
systemd-run --pty \
  -p DynamicUser=yes \
  -p ProtectSystem=strict \
  -p ProtectHome=yes \
  -p NoNewPrivileges=yes \
  cat /etc/passwd
# expected: permission denied / no such file, not the contents of /etc/passwd

# confirm Ollama is serving locally
curl -s http://localhost:11434/api/tags | jq .
```

## Recording tool

asciinema. Record the terminal session with `asciinema rec hearth-demo.cast`,
then either upload it or convert to a gif for the README.

## What needs to be filled in after Day 4 and Day 5

- The exact agent launch command (depends on the agent framework chosen on Day 3).
- A real run in the audit store so `hearth-runs` shows non-empty output.
- The TUI dashboard walkthrough (Day 5), replacing the plain `hearth-status`
  text output in step 2.
- Confirmed sandbox denial output captured on the real VM, not assumed.
- TBD: final runtime numbers (model load time, tokens per second on the 1660 Ti).
  Do not publish these until measured on the actual hardware.
