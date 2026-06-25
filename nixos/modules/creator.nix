# creator.nix: the local content-creation toolchain.
#
# hearth is the platform; apps get built ON it (the first being a fully-local
# brain-rot video bot: research a template, pull clips, assemble + caption +
# voice a video, loop-post N/day). Those apps run as agents with full shell
# reach, so the only thing they need from the host is the tools present in PATH.
# This module installs the local, free, no-API media toolchain so an agent can
# do the whole pipeline on the box (RTX 2060) without any cloud service.
#
# Gated behind hearth.creator.enable (default on). Heavier local generative
# models (Stable Diffusion / video gen via ComfyUI) are a separate, optional
# follow-on, not installed here.
{ config, lib, pkgs, ... }:
let
  cfg = config.hearth.creator;
in
{
  options.hearth.creator = {
    enable = lib.mkEnableOption "the local content-creation toolchain (ffmpeg, yt-dlp, image + audio tools, local TTS)" // {
      default = true;
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = with pkgs; [
      ffmpeg-full      # video + audio assembly, overlays, captions, encoding
      yt-dlp           # source clips from the web (the "find the clips" step)
      imagemagick      # image manipulation, thumbnails, caption frames
      sox              # audio shaping
      piper-tts        # fully-local neural text-to-speech (voiceover narration)
    ];
  };
}
