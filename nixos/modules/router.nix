# router.nix: the model router (v1.6 "Router").
#
# When a launch requests the model "auto", the agent loop resolves the concrete
# model to use by walking these rules in order: the first rule whose keywords
# appear in the prompt (any_keywords) or whose tools are on the launch's toolset
# (tools_any) wins, and its model is used. If no rule matches, `default` is used
# (empty means the caller keeps its own default). An absent file makes the
# router a no-op, so a launch with a concrete model is never touched.
#
# This module is pure configuration delivery: it renders /etc/hearth/router.json
# from the declared options and nothing else. The Python side reads that path by
# default (env HEARTH_ROUTER can override it), so there is no service or env
# plumbing to do here. The file is safe to render read-only and regenerate on
# every rebuild; no runtime state lives in it.
#
# Sample rendered /etc/hearth/router.json for
#   hearth.router.default = "llama3.2:3b";
#   hearth.router.rules = [
#     { name = "code"; keywords = [ "refactor" "bug" ]; tools = [ "edit_file" ];
#       model = "qwen2.5-coder:latest"; }
#   ];
# (object keys are alphabetical because builtins.toJSON sorts attrset keys):
#   {"default":"llama3.2:3b","rules":[{"any_keywords":["refactor","bug"],
#     "model":"qwen2.5-coder:latest","name":"code","tools_any":["edit_file"]}]}
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.router;

  # listOf submodule -> JSON list. Each rule maps the option field `keywords` to
  # the JSON key `any_keywords` and `tools` to `tools_any`, matching the Python
  # contract; `name` and `model` pass through unchanged.
  routerDoc = {
    default = cfg.default;
    rules = map (r: {
      name = r.name;
      any_keywords = r.keywords;
      tools_any = r.tools;
      model = r.model;
    }) cfg.rules;
  };
in
{
  options.hearth.router = {
    enable = lib.mkEnableOption "the model router for launches that request model \"auto\"";

    default = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Fallback model when no rule matches; empty keeps the caller's own default.";
    };

    rules = lib.mkOption {
      type = lib.types.listOf (lib.types.submodule {
        options = {
          name = lib.mkOption {
            type = lib.types.str;
            description = "Human-readable rule name (surfaced in audit/logs).";
          };
          keywords = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Match if any of these keywords appear in the prompt (JSON key any_keywords).";
          };
          tools = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Match if any of these tools are on the launch's toolset (JSON key tools_any).";
          };
          model = lib.mkOption {
            type = lib.types.str;
            description = "Model to use when this rule matches.";
          };
        };
      });
      default = [ ];
      description = ''
        Ordered routing rules for "auto" launches, rendered to
        /etc/hearth/router.json. The first rule whose keywords or tools match
        wins; if none match, hearth.router.default is used.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # Pure config: no runtime state, safe to regenerate on every rebuild. The
    # mkIf on config already scopes this to cfg.enable, so the file is only
    # written when the router is on; an absent file is a no-op on the Python side.
    environment.etc."hearth/router.json".text = builtins.toJSON routerDoc;
  };
}
