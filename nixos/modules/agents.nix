# agents.nix: the /var/lib/hearth directory layout, agent runtimes, sops-nix stub.
{ config, lib, pkgs, ... }:
{
  options.hearth.agents.enable = lib.mkEnableOption "hearth agent runtime" // {
    default = true;
  };

  config = lib.mkIf config.hearth.agents.enable {
    # Directory layout under /var/lib/hearth, created on boot by tmpfiles.
    # Format: "d  <path>  <mode>  <user>  <group>  <age>"
    systemd.tmpfiles.rules = [
      "d /var/lib/hearth         0750 hearth hearth -"
      "d /var/lib/hearth/agents  0750 hearth hearth -" # agent working directories
      "d /var/lib/hearth/models  0750 hearth hearth -" # model storage (see llm.nix)
      "d /var/lib/hearth/logs    0750 hearth hearth -" # agent run logs
      "d /var/lib/hearth/secrets 0700 hearth hearth -" # decrypted sops-nix secrets land here
      "d /var/lib/hearth/runs    0750 hearth hearth -" # per-run audit records (see observability.nix)
    ];

    # sops-nix integration stub.
    # This placeholder marks the integration point. Real secret definitions go
    # through the sops-nix module (sops.secrets.<name>), and decrypted values
    # should be targeted at /var/lib/hearth/secrets with mode 0700.
    # See docs/DECISIONS.md (ADR-003) for the key setup walkthrough.
    environment.etc."hearth/sops.yaml".text = ''
      # Placeholder sops configuration for hearth.
      # Replace with a real .sops.yaml creation rule set. See docs/DECISIONS.md.
      # Example structure:
      #   creation_rules:
      #     - path_regex: secrets/.*\.yaml$
      #       age: <your-age-public-key>
    '';

    # Base agent runtimes. Python (with uv for fast, reproducible installs) and
    # Node.js LTS cover the common agent frameworks.
    environment.systemPackages = with pkgs; [
      python3
      uv
      nodejs_20
    ];
  };
}
