# operator.nix: per-user home-manager config for the hearth desktop.
# Declares the icy KDE theme, wallpaper, top panel, the Meta+A command-center
# toggle, and a conky desktop readout. Imported by the blade host via
# home-manager.users.operator. plasma-manager option names are pinned to the
# revision in flake.lock.
{ config, lib, pkgs, plasma-manager, ... }:
let
  toggle = pkgs.writeShellApplication {
    name = "hearth-command-toggle";
    runtimeInputs = [ pkgs.firefox pkgs.procps ];
    text = builtins.readFile ../modules/desktop-assets/hearth-command-toggle.sh;
  };
in
{
  # homeModules is the current name; homeManagerModules is the deprecated alias.
  imports = [ plasma-manager.homeModules.plasma-manager ];

  home.stateVersion = "24.11";
  home.packages = [ toggle pkgs.conky ];

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

  xdg.configFile."conky/hearth.conf".text = ''
    conky.config = {
      own_window = true, own_window_type = 'desktop', own_window_transparent = true,
      alignment = 'bottom_left', gap_x = 30, gap_y = 40, update_interval = 2,
      default_color = 'A0C8FF', font = 'monospace:size=10',
    };
    conky.text = [[
    hearth  ''${nodename}
    OS      NixOS  ''${kernel}
    Up      ''${uptime}
    CPU     ''${cpu}%   RAM ''${mem}/''${memmax}
    Disk    ''${fs_used /} / ''${fs_size /}
    ]];
  '';

  xdg.desktopEntries.hearth = {
    name = "hearth";
    comment = "Local LLM and agent cockpit";
    exec = "${pkgs.firefox}/bin/firefox --new-window --class hearth-app --app=http://localhost:8770/command";
    terminal = false;
    categories = [ "Utility" "Development" ];
  };

  systemd.user.services.hearth-conky = {
    Unit.Description = "hearth conky desktop readout";
    Install.WantedBy = [ "graphical-session.target" ];
    Service.ExecStart = "${pkgs.conky}/bin/conky -c %h/.config/conky/hearth.conf";
  };
}
