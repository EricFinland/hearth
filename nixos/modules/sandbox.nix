# sandbox.nix: the least-privilege systemd profile every agent service merges in.
#
# Threat model (brief, expanded in docs/ARCHITECTURE.md):
# An agent is semi-trusted code that runs LLM-driven tool calls. We assume it
# may behave badly, either through a prompt injection or a bug. The sandbox
# limits the blast radius: a misbehaving agent should not read host secrets,
# write outside its working directories, gain new privileges, or tamper with
# the rest of the system. It does NOT defend against a kernel exploit or a
# compromised Nix store. Network isolation is intentionally NOT enabled yet
# because most agents need outbound network; see the roadmap.
{ config, lib, pkgs, ... }:
{
  options.hearth.sandbox.enable = lib.mkEnableOption "hearth sandbox profile" // {
    default = true;
  };

  config = lib.mkIf config.hearth.sandbox.enable {
    # bubblewrap is installed for future per-call sandboxing inside agents.
    # TODO: wire bubblewrap into the agent launcher for nested isolation of
    # individual tool calls. Tracked in docs/ROADMAP.md.
    environment.systemPackages = [ pkgs.bubblewrap ];

    # hearthSandboxProfile is a reusable serviceConfig fragment. Any agent
    # service can merge it into its own serviceConfig, for example:
    #
    #   systemd.services.my-agent.serviceConfig =
    #     config.hearth.sandbox.profile // { ExecStart = "..."; };
    #
    # It enforces least privilege through systemd isolation primitives.
    hearth.sandbox.profile = {
      DynamicUser = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ReadWritePaths = [ "/var/lib/hearth/agents" "/var/lib/hearth/runs" ];
      NoNewPrivileges = true;
      PrivateTmp = true;
      # Agents need outbound network for model APIs and tool calls. Network
      # isolation per agent is a roadmap item (see docs/ROADMAP.md Day 4).
      PrivateNetwork = false;
      RestrictNamespaces = true;
      SystemCallFilter = [ "@system-service" "~@privileged" "~@mount" ];
      CapabilityBoundingSet = "";
    };
  };

  # Expose the profile as a read-only option so other modules can reference it.
  options.hearth.sandbox.profile = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    description = "Reusable least-privilege serviceConfig fragment for agent services.";
  };
}
