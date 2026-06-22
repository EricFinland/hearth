# desktop.nix: KDE Plasma 6 on X11 with auto-login, for hosts with a screen.
# Gated behind hearth.desktop.enable. The icy theme, wallpaper, panel, and
# shortcuts are declared per-user via plasma-manager (see nixos/home/operator.nix).
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.desktop;
in
{
  options.hearth.desktop = {
    enable = lib.mkEnableOption "the hearth KDE Plasma desktop";
    autoLoginUser = lib.mkOption {
      type = lib.types.str;
      default = "operator";
      description = "User auto-logged into the Plasma session at boot.";
    };
  };

  config = lib.mkIf cfg.enable {
    services.xserver.enable = true;
    services.displayManager.sddm.enable = true;
    services.desktopManager.plasma6.enable = true;

    services.displayManager.autoLogin = {
      enable = true;
      user = cfg.autoLoginUser;
    };
    services.displayManager.defaultSession = "plasmax11";

    # When the graphical desktop is on, SDDM owns the seat. Drop any console
    # (getty) auto-login so it does not fight the display manager for the screen
    # or launch the text dashboard behind the KDE session. The dashboard is
    # still available over SSH.
    services.getty.autologinUser = lib.mkForce null;

    # Google Chrome is proprietary (unfree); allow it for the desktop.
    nixpkgs.config.allowUnfree = true;

    environment.systemPackages = with pkgs; [
      firefox
      google-chrome
      kdePackages.konsole
      kdePackages.dolphin
      kdePackages.kate
      conky
    ];
  };
}
