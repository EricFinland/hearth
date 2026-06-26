# blade.nix: the physical Razer Blade 15 host (bare metal, not a VM).
# i7-10750H, RTX 2060 Mobile, 16 GB RAM, Samsung NVMe, WiFi only.
{ plasma-manager, ... }:
{
  imports = [
    ../configuration.nix
    ./blade-hardware.nix
  ];

  networking.hostName = "hearth-blade";

  # This laptop has a screen. Auto-login the console so the laptop boots
  # straight into the hearth dashboard (the bash login hook in modules/shell.nix
  # launches it) instead of sitting at a blank login prompt. SSH still needs the
  # key. Press q in the dashboard to drop to a shell.
  services.getty.autologinUser = "operator";

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

  # This laptop has a screen, so turn on the hearth KDE Plasma desktop and
  # the operator's per-user theming/panel/conky/command-toggle (plasma-manager).
  hearth.desktop.enable = true;

  # Always-on self-improvement: the box continuously proposes, implements, and
  # validates small changes to its own config on isolated branches (never the
  # live system) and learns from each one. Paced so it does not peg the GPU.
  hearth.grow.enable = true;

  home-manager.useGlobalPkgs = true;
  home-manager.useUserPackages = true;
  home-manager.users.operator = import ../home/operator.nix;
  home-manager.extraSpecialArgs = { inherit plasma-manager; };

  # This laptop is used as an always-on box reached over the network, so it must
  # never suspend: suspend drops WiFi and SSH. Disable all sleep targets and
  # ignore the lid switch.
  systemd.targets.sleep.enable = false;
  systemd.targets.suspend.enable = false;
  systemd.targets.hibernate.enable = false;
  systemd.targets.hybrid-sleep.enable = false;
  services.logind.settings.Login = {
    HandleLidSwitch = "ignore";
    HandleLidSwitchExternalPower = "ignore";
  };

  # The operator admin key, so SSH works on the very first boot.
  hearth.adminKeys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP+Zx34aRKZqWY/dgPc0ARSYB4Gz1PRsV5uibn3ul4tB ericc@vonHallerPatcher"
  ];
}
