# sbom.nix: the system bill of materials (v1.7 "Tycoon").
#
# Renders /etc/hearth/sbom.json at build time from the evaluated system: every
# flake input with its locked rev and narHash (read from flake.lock), every
# system and per-user package with its store path (the hash in that path pins
# the exact build), the hearth modules and whether each is switched on, and the
# hearth-* services this machine runs. hearth-mapd merges it with the model
# digests Ollama reports and serves it at /sbom, so the whole machine is
# legible from one page.
#
# Pure configuration delivery: no service, no runtime state. The document is
# regenerated on every rebuild, so it can never drift from what is installed.
{ config, lib, ... }:
let
  cfg = config.hearth.sbom;

  # Store paths as plain strings. The packages are already in the system
  # closure; discarding the context keeps this JSON file from being the thing
  # that references them.
  pathOf = p: builtins.unsafeDiscardStringContext (toString p);

  lock = builtins.fromJSON (builtins.readFile ../../flake.lock);

  sourceOf = l:
    if l ? owner && l ? repo then "${l.type or "git"}:${l.owner}/${l.repo}"
    else l.url or (l.type or "unknown");

  inputs = lib.mapAttrsToList (name: node: {
    inherit name;
    source = sourceOf node.locked;
    rev = node.locked.rev or "";
    narHash = node.locked.narHash or "";
    lastModified = node.locked.lastModified or 0;
  }) (lib.filterAttrs (n: node: n != lock.root && node ? locked) lock.nodes);

  userPackages = lib.concatMap (u: u.packages or [ ]) (lib.attrValues config.users.users);

  # Deduplicate by store path, then sort by name for a stable, readable page.
  packages =
    let
      byPath = builtins.listToAttrs (map (p: {
        name = pathOf p;
        value = { name = lib.getName p; version = lib.getVersion p; path = pathOf p; };
      }) (config.environment.systemPackages ++ userPackages));
    in
    lib.sort (a: b: if a.name == b.name then a.path < b.path else a.name < b.name)
      (lib.attrValues byPath);

  modules = lib.mapAttrsToList (name: v: { inherit name; enabled = v.enable; })
    (lib.filterAttrs (_: v: builtins.isAttrs v && v ? enable && builtins.isBool v.enable)
      config.hearth);

  services = lib.mapAttrsToList (name: s: { inherit name; description = s.description or ""; })
    (lib.filterAttrs (n: _: lib.hasPrefix "hearth-" n) config.systemd.services);

  doc = {
    system = {
      hostName = config.networking.hostName;
      nixosVersion = config.system.nixos.version;
      configurationRevision = config.system.configurationRevision or null;
      kernel = config.boot.kernelPackages.kernel.version;
    };
    inherit inputs packages modules services;
  };
in
{
  options.hearth.sbom = {
    enable = lib.mkEnableOption "the build-time system bill of materials at /etc/hearth/sbom.json"
      // { default = true; };
  };

  config = lib.mkIf cfg.enable {
    environment.etc."hearth/sbom.json".text = builtins.toJSON doc;
  };
}
