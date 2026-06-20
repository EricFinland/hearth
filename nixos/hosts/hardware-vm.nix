# hardware-vm.nix: the minimal hardware profile that lets this config evaluate
# and boot as a Proxmox/QEMU virtual machine.
#
# Why this file exists: a NixOS configuration will not evaluate without a root
# filesystem and the kernel modules needed to find the disk at boot. The
# scaffold had neither, so `nix flake check` would fail before checking anything
# else. These values are correct for a QEMU VM with virtio disks (Proxmox's
# default). When you build an image with nixos-generators, the image format
# overrides these fileSystems automatically (mkImageMediaOverride), so there is
# no conflict between this file and the generated image.
{ modulesPath, ... }:
{
  imports = [ (modulesPath + "/profiles/qemu-guest.nix") ];

  # Kernel modules the initrd needs to see a virtio disk and mount root.
  boot.initrd.availableKernelModules = [
    "ata_piix"
    "uhci_hcd"
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
    "sd_mod"
    "sr_mod"
  ];
  boot.initrd.kernelModules = [ ];
  boot.kernelModules = [ ];
  boot.extraModulePackages = [ ];

  # Grow the root partition to fill whatever disk size Proxmox gives the VM.
  boot.growPartition = true;

  # Root and EFI system partition, addressed by label so the config does not
  # depend on a specific device name.
  fileSystems."/" = {
    device = "/dev/disk/by-label/nixos";
    fsType = "ext4";
  };

  fileSystems."/boot" = {
    device = "/dev/disk/by-label/ESP";
    fsType = "vfat";
  };

  # The QEMU guest agent lets Proxmox read the VM's IP and do clean shutdowns.
  services.qemuGuest.enable = true;
}
