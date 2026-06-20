# mcp.nix: the MCP audit gate. No MCP server starts until it has an approval file.
#
# Integration point: an external mcp-audit binary (a separate project) will
# replace the stub gate below. The gate enforces a simple rule: if a server is
# declared with auditRequired = true, it must have a corresponding approval file
# at /var/lib/hearth/mcp-audit/<name>.approved before it is allowed to start.
# The real binary will produce that approval file only after running its scan.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.mcp;
  serverType = lib.types.submodule {
    options = {
      name = lib.mkOption {
        type = lib.types.str;
        description = "Identifier for the MCP server. Used for the approval file name.";
      };
      command = lib.mkOption {
        type = lib.types.str;
        description = "Command that launches the MCP server.";
      };
      auditRequired = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "If true, the server may not start without an approval file.";
      };
    };
  };
in
{
  options.hearth.mcp.servers = lib.mkOption {
    type = lib.types.listOf serverType;
    default = [ ];
    description = "Declared MCP servers and whether each requires an audit approval.";
  };

  config = {
    systemd.tmpfiles.rules = [
      "d /var/lib/hearth/mcp-audit 0750 hearth hearth -"
    ];

    # One gate service per declared server. Each checks for the approval file
    # and exits 1 (blocking dependents) if the audit requirement is unmet.
    # TODO: replace with real mcp-audit binary when available. See docs/ROADMAP.md Day 6.
    systemd.services = lib.listToAttrs (map
      (s: lib.nameValuePair "hearth-mcp-audit-${s.name}" {
        description = "MCP audit gate for ${s.name}";
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = "hearth";
          Group = "hearth";
        };
        script = ''
          approval="/var/lib/hearth/mcp-audit/${s.name}.approved"
          if ${lib.boolToString s.auditRequired} && [ ! -f "$approval" ]; then
            echo "hearth-mcp-audit: WARNING ${s.name} has no approval at $approval; refusing to start" >&2
            exit 1
          fi
          echo "hearth-mcp-audit: ${s.name} approved"
        '';
      })
      cfg.servers);
  };
}
