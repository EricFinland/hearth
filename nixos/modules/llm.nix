# llm.nix: Ollama service, CUDA acceleration, declarative model manifest.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.llm;
in
{
  options.hearth.llm.models = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ "llama3.2:3b" "mistral:7b" ];
    description = ''
      Models to pull on activation. Each string is passed verbatim to
      `ollama pull`. The hearth-model-pull oneshot service iterates this list.
    '';
  };

  config = {
    # CUDA packages are unfree. Required for the GTX 1660 Ti acceleration below.
    nixpkgs.config.allowUnfree = true;

    services.ollama = {
      enable = true;
      # Use the NVIDIA GPU for inference. See the passthrough note below.
      acceleration = "cuda";
    };

    # GPU passthrough note:
    # The host Proxmox node must pass the GTX 1660 Ti through to this VM over
    # PCIe before CUDA can see it. That is configured on the Proxmox side, not
    # here. See: https://pve.proxmox.com/wiki/PCI_Passthrough
    # Inside the VM, `nvidia-smi` should list the card once passthrough and the
    # NVIDIA driver are in place.

    # Store models on the hearth working tree, not the default state dir, so
    # model storage lives alongside the rest of /var/lib/hearth.
    systemd.services.ollama.environment.OLLAMA_MODELS = "/var/lib/hearth/models";

    # Pull the declared models once Ollama is up. Idempotent: `ollama pull`
    # is a no-op for models already present.
    systemd.services.hearth-model-pull = {
      description = "Pull the declared hearth LLM models";
      after = [ "ollama.service" "network-online.target" ];
      wants = [ "ollama.service" "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      environment.OLLAMA_MODELS = "/var/lib/hearth/models";
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        set -euo pipefail
        ${lib.concatMapStringsSep "\n"
          (m: ''echo "hearth: pulling ${m}"; ${pkgs.ollama}/bin/ollama pull ${lib.escapeShellArg m} || echo "hearth: pull failed for ${m}, continuing"'')
          cfg.models}
      '';
    };
  };
}
