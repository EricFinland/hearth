# grow.nix: the always-on self-improvement daemon. hearth works on hearth.
#
# Runs the growth loop (agent/hearth_grow.py) as a long-lived service: each run
# does a batch of self-improvement cycles (recall lessons -> propose one small
# safe change -> implement + validate it on a branch with `nix flake check` ->
# record the lesson), then the service restarts after a pause and keeps going.
# This is "hearth constantly growing" without anyone driving it.
#
# Safety: the daemon evolves a DEDICATED repo copy (/var/lib/hearth/grow-repo),
# never the live deploy dir, and self-evolve only ever produces validated
# branches there. It NEVER merges or switches the live system. A human reviews
# the branches (see the growth ledger in the cockpit) and merges the good ones.
#
# Gated behind hearth.grow.enable (default OFF, opt-in). The blade turns it on.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.grow;
  growRepo = "/var/lib/hearth/grow-repo";

  # Seed the grow repo once from the live config so the daemon starts evolving
  # from the current system. The guard means we never clobber accumulated
  # branches/lessons on restart; reseeding is a deliberate manual `rm -rf`.
  seed = pkgs.writeShellScript "hearth-grow-seed" ''
    set -eu
    src="${cfg.sourceRepo}"
    if [ ! -e "${growRepo}/flake.nix" ] && [ -e "$src/flake.nix" ]; then
      ${pkgs.coreutils}/bin/mkdir -p "${growRepo}"
      ${pkgs.coreutils}/bin/cp -a "$src/." "${growRepo}/"
    fi
  '';
in
{
  options.hearth.grow = {
    enable = lib.mkEnableOption "the always-on self-improvement growth daemon";
    model = lib.mkOption {
      type = lib.types.str;
      default = "qwen2.5-coder";
      description = "Local model the growth loop uses to propose + implement improvements.";
    };
    sourceRepo = lib.mkOption {
      type = lib.types.str;
      default = "/home/operator/hearth-desktop";
      description = "Live config the grow repo is seeded from on first run.";
    };
    cyclesPerRun = lib.mkOption {
      type = lib.types.int;
      default = 12;
      description = "Self-improvement cycles per service run before it restarts.";
    };
    restartSec = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = "Pause between growth runs, so the box is not pegged continuously.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.tmpfiles.rules = [
      "d ${growRepo} 0750 operator users -"
    ];

    systemd.services.hearth-grow = {
      description = "hearth always-on self-improvement growth loop";
      # Needs the model up (Ollama) and the audit db initialized.
      after = [ "network.target" "hearth-audit-init.service" "ollama.service" ];
      wants = [ "ollama.service" ];
      wantedBy = [ "multi-user.target" ];
      # git for the self-evolve branch/commit. The nix flake check gate resolves
      # nix via the loop's absolute /run/current-system/sw/bin fallback, so the
      # system nix (and its daemon) is used rather than a second nix on PATH.
      path = [ config.hearth.agents.loopPackage pkgs.git pkgs.coreutils ];
      environment = {
        HEARTH_REPO = growRepo;
        HEARTH_DB = "/var/lib/hearth/runs/audit.db";
      };
      serviceConfig = {
        # Runs as operator with full reach, like the other agent paths. The
        # self-evolve gate (nix flake check on an isolated repo) is the safety net.
        User = "operator";
        Group = "users";
        NoNewPrivileges = false;
        ExecStartPre = "${seed}";
        ExecStart = "${config.hearth.agents.loopPackage}/bin/hearth-loop --grow --agent-name daemon-grow --model ${cfg.model} --max-cycles ${toString cfg.cyclesPerRun} --db /var/lib/hearth/runs/audit.db";
        # Keep growing: when a batch of cycles finishes (or fails), wait then go
        # again. This is what makes it "always on".
        Restart = "always";
        RestartSec = toString cfg.restartSec;
      };
    };
  };
}
