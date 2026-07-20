# governor.nix: run-wide budgets and alerting configuration (v1.5 "Governor").
#
# This module is pure configuration delivery. The daily token breaker itself is
# enforced in the agent loop, which sums today's token usage from the audit DB
# (/var/lib/hearth/runs/audit.db) before and during each run and refuses to
# start or continue once the cap is hit. The scheduler checks the same budget
# before dispatching due missions, and mapd surfaces the remaining budget in
# the cockpit. All the OS layer does here is hand the same numbers to every
# consumer by injecting environment variables into the services that read them:
#
#   HEARTH_DAILY_TOKEN_CAP  daily token budget across all runs (0/unset = off)
#   HEARTH_NTFY_TOPIC       ntfy topic for the unified alert fan-out
#   HEARTH_NTFY_URL         ntfy server base URL (only set when a topic is set)
#   HEARTH_NOTIFY_DONE      "on" to also notify on successful completion
#
# Consumers: the per-run spawn template (hearth-agent@), the map/cockpit server
# (hearth-mapd), the standing-missions scheduler (hearth-schedule), and the
# growth loop (hearth-grow). Each injection is gated on that subsystem's own
# enable flag so this module never conjures unit stubs for services that were
# never defined.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.governor;

  # Only ship vars that carry a meaningful value; unset means "feature off" on
  # the Python side, so an empty string would just be noise.
  govEnv =
    lib.optionalAttrs (cfg.dailyTokenCap > 0) {
      HEARTH_DAILY_TOKEN_CAP = toString cfg.dailyTokenCap;
    }
    // lib.optionalAttrs (cfg.ntfyTopic != "") {
      HEARTH_NTFY_TOPIC = cfg.ntfyTopic;
      HEARTH_NTFY_URL = cfg.ntfyUrl;
    }
    // lib.optionalAttrs cfg.notifyDone {
      HEARTH_NOTIFY_DONE = "on";
    };
in
{
  options.hearth.governor = {
    enable = lib.mkEnableOption "run-wide token budgets and ntfy alerting";

    dailyTokenCap = lib.mkOption {
      type = lib.types.int;
      default = 0;
      description = "Daily token budget across all runs; 0 disables the breaker.";
    };

    ntfyTopic = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "ntfy topic for alert fan-out; empty disables notifications.";
    };

    ntfyUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://ntfy.sh";
      description = "Base URL of the ntfy server to publish to.";
    };

    notifyDone = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Also send a notification when a run completes successfully.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services =
      lib.optionalAttrs config.hearth.agents.enable {
        "hearth-agent@".environment = govEnv;
      }
      // lib.optionalAttrs config.hearth.mapui.enable {
        hearth-mapd.environment = govEnv;
      }
      // lib.optionalAttrs config.hearth.schedule.enable {
        hearth-schedule.environment = govEnv;
      }
      // lib.optionalAttrs config.hearth.grow.enable {
        hearth-grow.environment = govEnv;
      };
  };
}
