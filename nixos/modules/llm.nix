# llm.nix: Ollama service, CUDA acceleration, declarative model manifest.
# The whole stack is gated behind hearth.llm.enable so a minimal image can be
# built without compiling CUDA (see flake.nix workstation-minimal).
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.llm;
in
{
  options.hearth.llm = {
    enable = lib.mkEnableOption "the hearth LLM stack (Ollama with CUDA)" // {
      default = true;
    };

    models = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "llama3.2:3b" "mistral:7b" ];
      description = ''
        Models to pull on activation. Each string is passed verbatim to
        `ollama pull`. The hearth-model-pull oneshot service iterates this list.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # CUDA packages are unfree. Required for the GTX 1660 Ti acceleration below.
    nixpkgs.config.allowUnfree = true;

    services.ollama = {
      enable = true;
      # Use the CUDA build of Ollama for NVIDIA GPU inference. Current nixpkgs
      # removed services.ollama.acceleration; you pick the package variant
      # instead (ollama-cuda, ollama-rocm, ollama-vulkan, or plain ollama).
      package = pkgs.ollama-cuda;
      # Models live in ollama's own managed state directory (/var/lib/ollama).
      # Pointing this at /var/lib/hearth/models fails: the hardened ollama unit
      # runs as the ollama user and cannot write that hearth-owned path. Unifying
      # storage under /var/lib/hearth would need a ReadWritePaths override plus
      # ownership handoff to the ollama user; left as a future refinement.
    };

    # GPU passthrough note:
    # The host Proxmox node must pass the GTX 1660 Ti through to this VM over
    # PCIe before CUDA can see it. That is configured on the Proxmox side, not
    # here. See: https://pve.proxmox.com/wiki/PCI_Passthrough
    # Inside the VM, `nvidia-smi` should list the card once passthrough and the
    # NVIDIA driver are in place.

    # Pull the declared models once Ollama is up. Idempotent: `ollama pull`
    # is a no-op for models already present.
    systemd.services.hearth-model-pull = {
      description = "Pull the declared hearth LLM models";
      after = [ "ollama.service" "network-online.target" ];
      wants = [ "ollama.service" "network-online.target" ];
      wantedBy = [ "multi-user.target" ];
      # No OLLAMA_MODELS here: `ollama pull` talks to the running server, which
      # owns where models are stored. The pull just needs the server reachable.
      # HOME must be set or the ollama CLI panics ("$HOME is not defined").
      environment.HOME = "/root";
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        set -uo pipefail
        # Ollama reports active before its socket is ready, so `after` is not
        # enough. Wait until it actually answers (up to ~60s) before pulling.
        for _ in $(seq 1 60); do
          ${config.services.ollama.package}/bin/ollama list >/dev/null 2>&1 && break
          sleep 1
        done
        ${lib.concatMapStringsSep "\n"
          (m: ''echo "hearth: pulling ${m}"; ${config.services.ollama.package}/bin/ollama pull ${lib.escapeShellArg m} || echo "hearth: pull failed for ${m}, continuing"'')
          cfg.models}
      '';
    };
  };
}
