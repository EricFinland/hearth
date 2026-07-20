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

  # Standing missions: a timer checks the mission registry and dispatches any
  # that are due (the "works while you sleep" layer).
  hearth.schedule.enable = true;

  # The egress wall (v1.4): runs that declare allowed_hosts get per-run
  # nftables chains at the kernel, and blocked connections land in the audit
  # log via the watch bridge.
  hearth.egress.enable = true;

  # The governor (v1.5): a hard daily token budget across all runs. 500k
  # tokens is roomy for a day of missions on local models but stops a
  # runaway overnight loop dead.
  hearth.governor.enable = true;
  hearth.governor.dailyTokenCap = 500000;

  # A declarative standing mission (cron-as-flake, v1.5): a small daily pulse
  # that exercises the whole governed pipeline: capability manifest (only the
  # listed tools), scheduler, audit, and flight recorder.
  hearth.schedule.missions.daily-pulse = {
    schedule = "07:30";
    model = "llama3.2:3b";
    kind = "agent";
    prompt = "Write a short morning status note to pulse.md: today's date, one line about what a healthy hearth box looks like, and one improvement idea for a local agent system. Keep it under 10 lines.";
    tools = [ "write_file" "read_file" "list_tree" ];
  };

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
