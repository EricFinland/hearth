# gpu-nvidia.nix: NVIDIA proprietary driver and CUDA, gated behind
# hearth.gpu.enable (default off).
#
# It is off by default on purpose. On an Optimus laptop (Intel iGPU + NVIDIA
# dGPU) the driver is the most likely thing to break a boot, and a broken boot
# on a remote machine strands it. So the first install runs with this off; turn
# it on with a later `nixos-rebuild switch`, which keeps the previous generation
# bootable for rollback.
#
# Hardware here: RTX 2060 Mobile (Turing, TU106) at PCI 1:0:0, Intel UHD at
# PCI 0:2:0. Turing works with the proprietary driver and CUDA (compute 7.5).
{ config, lib, ... }:
let
  cfg = config.hearth.gpu;
in
{
  options.hearth.gpu.enable =
    lib.mkEnableOption "NVIDIA proprietary driver and CUDA for the dGPU";

  config = lib.mkIf cfg.enable {
    nixpkgs.config.allowUnfree = true;

    # Pulls in and loads the NVIDIA kernel driver even without a desktop.
    services.xserver.videoDrivers = [ "nvidia" ];
    hardware.graphics.enable = true;

    hardware.nvidia = {
      modesetting.enable = true;
      # Proprietary modules (not the open kernel modules) for this Turing card.
      open = false;
      nvidiaSettings = true;
      powerManagement.enable = true;

      # Optimus hybrid graphics. Offload mode keeps the Intel iGPU as the primary
      # and powers the NVIDIA card for compute and on-demand offload. For a
      # headless inference box this gives CUDA access without forcing the dGPU on
      # all the time. If model loads do not see the GPU, switch to prime.sync.
      prime = {
        intelBusId = "PCI:0:2:0";
        nvidiaBusId = "PCI:1:0:0";
        offload.enable = true;
        offload.enableOffloadCmd = true;
      };
    };
  };
}
