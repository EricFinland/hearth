# shell.nix: login experience, the hearth-status command, and shell tooling.
{ pkgs, ... }:
let
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

    echo "Run 'hearth-runs' for full agent run history"
  '';
in
{
  environment.systemPackages = with pkgs; [
    hearthStatus
    starship
    fzf
    zoxide
    btop
  ];

  programs.bash.interactiveShellInit = ''
    echo "hearth ready. Run 'hearth-status' for a system overview."
  '';
}
