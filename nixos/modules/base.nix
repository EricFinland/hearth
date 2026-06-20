# base.nix: locale, the hearth service user, SSH hardening, base packages, bootloader.
{ pkgs, ... }:
{
  # Locale and timezone. America/New_York is a reasonable default for the
  # maintainer. Change i18n and time.timeZone if you deploy elsewhere.
  i18n.defaultLocale = "en_US.UTF-8";
  time.timeZone = "America/New_York";

  # Non-root service user that owns the hearth working tree under /var/lib/hearth.
  # Agents and services run with reduced privilege; see modules/sandbox.nix for
  # the per-service isolation profile.
  users.users.hearth = {
    isSystemUser = true;
    group = "hearth";
    home = "/var/lib/hearth";
    createHome = true;
    description = "hearth service account";
    shell = pkgs.bashInteractive;
  };
  users.groups.hearth = { };

  # SSH: key-based only. No passwords, no direct root login.
  services.openssh = {
    enable = true;
    settings = {
      PasswordAuthentication = false;
      PermitRootLogin = "no";
    };
  };

  # Base tooling available on every hearth host.
  environment.systemPackages = with pkgs; [
    git
    htop
    tmux
    curl
    wget
    jq
    ripgrep
    fd
    bat
    tree
    nvme-cli
    pciutils
    usbutils
  ];

  # Bootloader: systemd-boot on EFI. Matches the Proxmox OVMF/UEFI VM profile.
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # Firewall on by default. Per-port and per-interface rules live in
  # modules/networking.nix.
  networking.firewall.enable = true;
}
