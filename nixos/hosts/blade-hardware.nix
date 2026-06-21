# blade-hardware.nix: hardware profile for the physical Razer Blade 15
# (i7-10750H, RTX 2060 Mobile, Samsung NVMe). Captured with nixos-generate-config
# on the machine itself, so the device UUIDs and kernel modules are real.
#
# This is the bare-metal counterpart to hosts/hardware-vm.nix (which targets a
# Proxmox VM). The blade host imports this one instead.
{ config, lib, modulesPath, ... }:
{
  imports = [ (modulesPath + "/installer/scan/not-detected.nix") ];

  boot.initrd.availableKernelModules = [ "xhci_pci" "nvme" "usb_storage" "usbhid" "sd_mod" ];
  boot.initrd.kernelModules = [ ];
  boot.kernelModules = [ "kvm-intel" ];
  boot.extraModulePackages = [ ];

  fileSystems."/" = {
    device = "/dev/disk/by-uuid/0b6280d8-1ba0-4c53-885e-1b6db92496a2";
    fsType = "ext4";
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-uuid/9F20-271F";
    fsType = "vfat";
    options = [ "fmask=0022" "dmask=0022" ];
  };

  swapDevices = [ ];

  nixpkgs.hostPlatform = lib.mkDefault "x86_64-linux";
  hardware.cpu.intel.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
}
