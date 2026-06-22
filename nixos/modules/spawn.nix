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
      name="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name','agent'))" "$req")"
      prompt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('prompt') or chr(0)*0)" "$req")"
      rm -f "$req"
      ws="/var/lib/hearth/agents/$id"
      mkdir -p "$ws"
      exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$name" --model "$model" --workspace "$ws" "$prompt"
    '';
  };
in
lib.mkIf config.hearth.agents.enable {
  systemd.tmpfiles.rules = [
    "d /var/lib/hearth/queue 2770 hearth hearth -"
  ];

  systemd.services."hearth-agent@" = {
    description = "hearth on-demand agent run %i";
    serviceConfig = config.hearth.sandbox.profile // {
      Type = "oneshot";
      # Extend the sandbox allow list with the queue dir so the instance can
      # delete its own request file after reading it (the profile only grants
      # agents + runs). Done before running the agent, so a failed run does not
      # leave a file that would respawn the instance.
      ReadWritePaths = config.hearth.sandbox.profile.ReadWritePaths ++ [ "/var/lib/hearth/queue" ];
      # Make the user's stored API credentials available to the agent through
      # systemd's credential channel (readable at $CREDENTIALS_DIRECTORY/creds,
      # not world-readable). Optional: if the file is absent, the unit still runs.
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
          ${pkgs.systemd}/bin/systemctl start "hearth-agent@$id.service" || true
        done
      '';
    };
  };
}
