# sandbox.nix: the least-privilege systemd profile every agent service merges in.
#
# Threat model (brief, expanded in docs/ARCHITECTURE.md):
# An agent is semi-trusted code that runs LLM-driven tool calls. We assume it
# may behave badly through a prompt injection or a bug. The profile below limits
# the blast radius. Be precise about what it does and does not do:
#
#  - It BLOCKS writes anywhere except the allow list (ProtectSystem=strict makes
#    the whole filesystem read-only except ReadWritePaths).
#  - It BLOCKS reads of user home directories and /root (ProtectHome=true).
#  - It BLOCKS reads of the hearth secrets directory, which is mode 0700 owned
#    by the hearth user, because the agent runs as a different DynamicUser id.
#  - It does NOT hide world-readable files like /etc/passwd. ProtectSystem makes
#    them read-only, not invisible. That is fine: /etc/passwd holds no secrets.
#    Truly jailing the visible filesystem (bind-mount allow list) is a roadmap
#    item; see docs/ROADMAP.md.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.sandbox;

  hearthSandboxProfile = {
    DynamicUser = true;
    # Put the ephemeral agent user in the hearth group so it can write the
    # group-writable (2770) agents and runs directories, and nothing else.
    SupplementaryGroups = [ "hearth" ];
    # Create files group-writable (0660/0770) so the shared audit database in
    # the setgid runs directory is writable by every hearth-group agent. The
    # secrets directory stays 0700 (owner only), so agents still cannot read it.
    UMask = "0007";
    ProtectSystem = "strict";
    ProtectHome = true;
    ReadWritePaths = [ "/var/lib/hearth/agents" "/var/lib/hearth/runs" ];
    NoNewPrivileges = true;
    PrivateTmp = true;
    # Agents need outbound network for model APIs and tool calls. Per-agent
    # network isolation is a roadmap item (see docs/ROADMAP.md Day 4).
    PrivateNetwork = false;
    RestrictNamespaces = true;
    SystemCallFilter = [ "@system-service" "~@privileged" "~@mount" ];
    CapabilityBoundingSet = "";
  };
in
{
  options.hearth.sandbox = {
    enable = lib.mkEnableOption "hearth sandbox profile" // { default = true; };

    profile = lib.mkOption {
      type = lib.types.attrs;
      internal = true;
      readOnly = true;
      description = "Reusable least-privilege serviceConfig fragment for agent services.";
    };
  };

  config = {
    # The profile is plain data, always available for other modules to merge in
    # (for example modules/agents.nix uses it for the demo agent).
    hearth.sandbox.profile = hearthSandboxProfile;

    # bubblewrap is installed for future per-call sandboxing inside agents.
    # TODO: wire bubblewrap into the agent launcher for nested isolation of
    # individual tool calls. Tracked in docs/ROADMAP.md.
    environment.systemPackages = lib.mkIf cfg.enable [ pkgs.bubblewrap ];

    # A runnable proof of the sandbox. It runs under the same profile as a real
    # agent and probes the boundaries, logging the result of each attempt.
    # Manual start, then read the journal:
    #   sudo systemctl start hearth-sandbox-selftest
    #   journalctl -u hearth-sandbox-selftest
    systemd.services.hearth-sandbox-selftest = lib.mkIf cfg.enable {
      description = "hearth sandbox self-test (probes the isolation boundaries)";
      serviceConfig = hearthSandboxProfile // { Type = "oneshot"; };
      script = ''
        echo "[selftest] running as uid=$(id -u) groups=$(id -Gn)"

        echo "[selftest] WRITE outside allow list (expect denied):"
        if echo probe > /etc/hearth-probe 2>/dev/null; then
          echo "  UNEXPECTED: wrote /etc/hearth-probe"
        else
          echo "  OK: write to /etc denied"
        fi
        if echo probe > /var/lib/hearth/models/probe 2>/dev/null; then
          echo "  UNEXPECTED: wrote /var/lib/hearth/models/probe"
        else
          echo "  OK: write to /var/lib/hearth/models denied"
        fi

        echo "[selftest] WRITE inside allow list (expect allowed):"
        if echo probe > /var/lib/hearth/agents/probe 2>/dev/null; then
          echo "  OK: wrote /var/lib/hearth/agents/probe"
          rm -f /var/lib/hearth/agents/probe
        else
          echo "  UNEXPECTED: could not write /var/lib/hearth/agents"
        fi

        echo "[selftest] READ /root (expect denied via ProtectHome):"
        if cat /root/.bash_history 2>/dev/null; then
          echo "  UNEXPECTED: read /root"
        else
          echo "  OK: /root not readable"
        fi

        echo "[selftest] READ hearth secrets (expect denied: 0700, other uid):"
        if cat /var/lib/hearth/secrets/* 2>/dev/null; then
          echo "  UNEXPECTED: read a secret"
        else
          echo "  OK: secrets directory not readable"
        fi

        echo "[selftest] done"
      '';
    };
  };
}
