# spawn.nix: on-demand sandboxed agent runs launched from the UI.
# The web app (hearth-mapd) drops a request file in /var/lib/hearth/queue; a root
# path-watcher starts a per-run sandboxed hearth-agent@<id> instance, which reads
# the request, runs the agent (Ollama call + audit + state), and removes the file.
{ config, lib, pkgs, ... }:
let
  agentPkg = config.hearth.agents.package;
  runner = pkgs.writeShellApplication {
    name = "hearth-run-from-queue";
    runtimeInputs = [ agentPkg config.hearth.agents.loopPackage pkgs.python3 pkgs.coreutils ];
    text = ''
      id="$1"
      req="/var/lib/hearth/queue/$id.json"
      [ -f "$req" ] || exit 0
      model="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('model','qwen2.5-coder'))" "$req")"
      mode="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode','bypass'))" "$req")"
      creds="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('creds') or chr(0)*0)" "$req")"
      tools="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('tools') or chr(0)*0)" "$req")"
      hosts="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('allowed_hosts') or chr(0)*0)" "$req")"
      prompt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('prompt') or chr(0)*0)" "$req")"
      swarm="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('swarm') else chr(0)*0)" "$req")"
      marathon="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('marathon') else chr(0)*0)" "$req")"
      checkin="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('checkin') else chr(0)*0)" "$req")"
      evolve="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('evolve') else chr(0)*0)" "$req")"
      grow="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('grow') else chr(0)*0)" "$req")"
      rm -f "$req"
      ws="/var/lib/hearth/agents/$id"
      mkdir -p "$ws"
      [ -n "$creds" ] && export HEARTH_ALLOWED_CREDS="$creds"
      [ -n "$tools" ] && export HEARTH_ALLOWED_TOOLS="$tools"
      [ -n "$hosts" ] && export HEARTH_ALLOWED_HOSTS="$hosts"
      if [ -n "$swarm" ]; then
        exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --manager --agent-name "$id" --model "$model" --mode "$mode" --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
      fi
      if [ -n "$marathon" ]; then
        ck=""; [ -n "$checkin" ] && ck="--checkin"
        exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --marathon $ck --agent-name "$id" --model "$model" --mode "$mode" --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
      fi
      if [ -n "$evolve" ]; then
        exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --evolve --agent-name "$id" --model "$model" --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
      fi
      if [ -n "$grow" ]; then
        exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --grow --agent-name "$id" --model "$model" --workspace "$ws" --db /var/lib/hearth/runs/audit.db
      fi
      exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$id" --model "$model" --mode "$mode" --io db --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
    '';
  };
in
lib.mkIf config.hearth.agents.enable {
  systemd.tmpfiles.rules = [
    "d /var/lib/hearth/queue 2770 hearth hearth -"
  ];

  systemd.services."hearth-agent@" = {
    description = "hearth on-demand agent run %i";
    serviceConfig = {
      Type = "oneshot";
      # Background workers are meant to act on the real machine (the user chose
      # full-machine reach). They run unsandboxed as operator with sudo available,
      # like the mapd-hosted interactive sessions. Containment is the audit log
      # plus the approvals queue (auto mode) and the kill switch.
      User = "operator";
      Group = "users";
      NoNewPrivileges = false;
      # Stored API credentials are still delivered through systemd's credential
      # channel (readable at $CREDENTIALS_DIRECTORY/creds), not world-readable.
      LoadCredential = [ "creds:/var/lib/hearth/secrets/agent-credentials" ];
      ExecStart = "${runner}/bin/hearth-run-from-queue %i";
    };
  };

  systemd.paths.hearth-spawn = {
    description = "watch the hearth agent queue";
    wantedBy = [ "multi-user.target" ];
    pathConfig.PathExistsGlob = "/var/lib/hearth/queue/*.json";
    pathConfig.MakeDirectory = false;
  };
  systemd.services.hearth-spawn = {
    description = "start sandboxed agents for queued requests";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "hearth-spawn" ''
        shopt -s nullglob
        for f in /var/lib/hearth/queue/*.json; do
          id="$(${pkgs.coreutils}/bin/basename "$f" .json)"
          # --no-block: do NOT wait for the oneshot to finish. A manager agent
          # spawns its own children by dropping queue files, so a blocking start
          # would deadlock (the spawner waits on the manager, the manager waits on
          # the spawner to start its children).
          ${pkgs.systemd}/bin/systemctl start --no-block "hearth-agent@$id.service" || true
        done
      '';
    };
  };
}
