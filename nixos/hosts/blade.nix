# blade.nix: the physical Razer Blade 15 host (bare metal, not a VM).
# i7-10750H, RTX 2060 Mobile, 16 GB RAM, Samsung NVMe, WiFi only.
{ ... }:
{
  imports = [
    ../configuration.nix
    ./blade-hardware.nix
  ];

  networking.hostName = "hearth-blade";

  # This laptop has no wired connection in use; it is on WiFi. NetworkManager
  # manages the connection. The actual WiFi profile (with its passphrase) is
  # copied into the installed system at install time rather than committed here,
  # so the secret stays out of the repo. NetworkManager auto-connects from that
  # profile on boot, which is what keeps SSH reachable after a reboot.
  networking.networkmanager.enable = true;

  # Phase 1: the LLM stack (Ollama + CUDA) and the NVIDIA driver are off, so the
  # first boot is guaranteed to come up on the network. After SSH is confirmed
  # post-reboot, enable both and `nixos-rebuild switch`:
  #   hearth.llm.enable = true;   hearth.gpu.enable = true;
  hearth.llm.enable = false;
  hearth.gpu.enable = false;

  # The operator admin key, so SSH works on the very first boot.
  hearth.adminKeys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP+Zx34aRKZqWY/dgPc0ARSYB4Gz1PRsV5uibn3ul4tB ericc@vonHallerPatcher"
  ];
}
