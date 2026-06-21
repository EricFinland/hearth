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

  # Phase 2 (enabled 2026-06-21, after the first boot was confirmed reachable):
  # the NVIDIA driver and the Ollama/CUDA stack are on. The first install booted
  # with these off so a driver problem could not strand the machine; now that
  # SSH is confirmed and rollback is available, they are turned on.
  hearth.llm.enable = true;
  hearth.gpu.enable = true;

  # The operator admin key, so SSH works on the very first boot.
  hearth.adminKeys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP+Zx34aRKZqWY/dgPc0ARSYB4Gz1PRsV5uibn3ul4tB ericc@vonHallerPatcher"
  ];
}
