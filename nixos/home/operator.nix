# operator.nix: per-user home-manager config for the hearth desktop.
# Declares the icy KDE theme, wallpaper, top panel, the Meta+A command-center
# toggle, and a conky desktop readout. Imported by the blade host via
# home-manager.users.operator. plasma-manager option names are pinned to the
# revision in flake.lock.
{ config, lib, pkgs, plasma-manager, ... }:
let
  toggle = pkgs.writeShellApplication {
    name = "hearth-command-toggle";
    runtimeInputs = [ pkgs.google-chrome pkgs.procps ];
    text = builtins.readFile ../modules/desktop-assets/hearth-command-toggle.sh;
  };
  # Idempotent "open" launcher used by the desktop icon, the login autostart, and
  # the app-menu entry (the Meta+A toggle stays for closing it).
  hearthOpen = pkgs.writeShellApplication {
    name = "hearth-open";
    runtimeInputs = [ pkgs.google-chrome pkgs.procps ];
    text = builtins.readFile ../modules/desktop-assets/hearth-open.sh;
  };
in
{
  # homeModules is the current name; homeManagerModules is the deprecated alias.
  imports = [ plasma-manager.homeModules.plasma-manager ];

  home.stateVersion = "24.11";
  home.packages = [ toggle hearthOpen pkgs.conky ];

  programs.plasma = {
    enable = true;

    workspace = {
      colorScheme = "BreezeDark";
      theme = "breeze-dark";
      wallpaper = "${../../webui/static/assets/wallpaper.jpg}";
    };

    panels = [{
      location = "top";
      height = 28;
      widgets = [
        "org.kde.plasma.kickoff"
        "org.kde.plasma.pager"
        "org.kde.plasma.panelspacer"
        # The system-monitor sensor widget MUST use the structured form rather
        # than the plain string: plasma-manager's panel module probes
        # widget.config for this widget name, and a plain string leaves config
        # null, which crashes eval. The structured form gives it a real config.
        { systemMonitor = { }; }
        "org.kde.plasma.systemtray"
        "org.kde.plasma.digitalclock"
      ];
    }];

    hotkeys.commands."hearth-command" = {
      name = "Toggle hearth command center";
      key = "Meta+A";
      command = "${toggle}/bin/hearth-command-toggle";
    };

    # No idle screen lock: the box auto-logs in and is physically controlled,
    # so locking on idle just gets in the way.
    configFile.kscreenlockerrc.Daemon = {
      Autolock = false;
      LockOnResume = false;
    };
  };

  # The conky desktop dashboard config lives as a plain file so conky's own
  # ${...} variables do not collide with Nix string interpolation.
  xdg.configFile."conky/hearth.conf".source = ../modules/desktop-assets/conky.conf;

  xdg.desktopEntries.hearth = {
    name = "hearth";
    comment = "Open the hearth AI cockpit";
    exec = "${hearthOpen}/bin/hearth-open";
    icon = "applications-science";
    terminal = false;
    categories = [ "Utility" "Development" ];
  };

  # A clickable icon on the desktop itself (Plasma 6 shows ~/Desktop entries) and
  # a login autostart, so the cockpit is one double-click away or already open.
  # Both point at the idempotent hearth-open launcher. (Meta+A still toggles it,
  # but the icon does not depend on the hotkey registering.)
  home.file."Desktop/hearth.desktop" = {
    executable = true;
    text = ''
      [Desktop Entry]
      Type=Application
      Name=hearth
      Comment=Open the hearth AI cockpit
      Exec=${hearthOpen}/bin/hearth-open
      Icon=applications-science
      Terminal=false
      Categories=Utility;Development;
    '';
  };
  home.file.".config/autostart/hearth.desktop".text = ''
    [Desktop Entry]
    Type=Application
    Name=hearth cockpit
    Comment=Open the hearth cockpit on login
    Exec=${hearthOpen}/bin/hearth-open
    Icon=applications-science
    Terminal=false
    X-KDE-autostart-after=panel
    X-GNOME-Autostart-enabled=true
  '';

  systemd.user.services.hearth-conky = {
    Unit.Description = "hearth conky desktop readout";
    Install.WantedBy = [ "graphical-session.target" ];
    Service.ExecStart = "${pkgs.conky}/bin/conky -c %h/.config/conky/hearth.conf";
  };
}
