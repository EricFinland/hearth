# shell.nix: login experience, the hearth-status and hearth-dashboard commands,
# and shell tooling.
{ pkgs, ... }:
let
  # Python with Textual for the dashboard. The dashboard source lives in
  # dashboard/hearth_dashboard.py and is standard library only except for
  # Textual, so this single dependency is all it needs.
  pythonEnv = pkgs.python3.withPackages (ps: [ ps.textual ]);

  hearthDashboard = pkgs.writeShellApplication {
    name = "hearth-dashboard";
    runtimeInputs = [ pythonEnv pkgs.systemd ];
    text = ''
      exec ${pythonEnv}/bin/python3 ${../../dashboard/hearth_dashboard.py} "$@"
    '';
  };

  hearthStatus = pkgs.writeShellScriptBin "hearth-status" ''
    echo "=== hearth system status ==="

    echo -n "ollama:    "
    systemctl is-active ollama 2>/dev/null || echo "unknown"

    echo -n "tailscale: "
    if command -v tailscale >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
      tailscale status --json 2>/dev/null | jq -r .BackendState 2>/dev/null \
        || echo "not ready"
    else
      echo "not ready"
    fi

    echo "--- recent runs ---"
    if command -v hearth-runs >/dev/null 2>&1; then
      hearth-runs | tail -n 5
    else
      echo "hearth-runs not on PATH"
    fi

    echo "Run 'hearth-runs' for full agent run history, 'hearth-dashboard' for the live view"
  '';
in
{
  environment.systemPackages = with pkgs; [
    hearthStatus
    hearthDashboard
    starship
    fzf
    zoxide
    btop
  ];

  programs.bash.interactiveShellInit = ''
    # On an interactive login shell with a real terminal, open the hearth
    # dashboard. It quits with 'q' and drops you back to the shell. Set
    # HEARTH_NO_DASHBOARD=1 to skip it (for example in automation).
    if [ -z "''${HEARTH_NO_DASHBOARD:-}" ] && [ -t 1 ] && shopt -q login_shell \
        && command -v hearth-dashboard >/dev/null 2>&1; then
      hearth-dashboard || true
    else
      echo "hearth ready. Run 'hearth-dashboard' for the dashboard, 'hearth-status' for text."
    fi
  '';
}
