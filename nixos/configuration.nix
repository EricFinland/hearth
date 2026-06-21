# Top-level hearth configuration. Imports every module and the shared defaults.
# The concrete host (nixos/hosts/workstation.nix) imports this file.
{ ... }:
{
  imports = [
    ./modules/base.nix
    ./modules/admin.nix
    ./modules/llm.nix
    ./modules/agents.nix
    ./modules/sandbox.nix
    ./modules/observability.nix
    ./modules/networking.nix
    ./modules/shell.nix
    ./modules/mcp.nix
    ./modules/gpu-nvidia.nix
    ./modules/mapui.nix
    ./modules/desktop.nix
    ./modules/spawn.nix
  ];

  # The NixOS release this configuration was written against. Do not change this
  # casually. It controls stateful defaults (database layouts, etc.) and is not
  # the same as the nixpkgs version you build with.
  system.stateVersion = "24.11";
}
