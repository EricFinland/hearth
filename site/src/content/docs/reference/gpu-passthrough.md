---
title: GPU passthrough
description: Get a real NVIDIA GPU into hearth, on Proxmox or on bare metal.
---

hearth runs its models on an NVIDIA GPU through CUDA. There are two separate
things to get right: getting the card into the machine, and turning the driver on
inside hearth.

## On bare metal

If hearth is installed directly on a machine with the GPU (like the `blade`
host), there is no passthrough. You only turn the driver on:

```nix
hearth.gpu.enable = true;
```

This is off by default on purpose. On a laptop with switchable graphics the
driver is the most likely thing to break a boot, so the first install runs
without it. Enable it with a later `nixos-rebuild switch`, which keeps the
previous generation bootable for rollback if it goes wrong.

## On a Proxmox VM

The GPU has to be handed from the Proxmox host to the VM over PCIe before hearth
can see it. That is configured on the Proxmox side, not in hearth.

1. On the Proxmox host, enable IOMMU and bind the card to `vfio-pci`. Follow the
   official guide: [Proxmox PCI(e) Passthrough](https://pve.proxmox.com/wiki/PCI_Passthrough).
2. Add the GPU as a PCI device to the hearth VM.
3. Boot the VM. With `hearth.gpu.enable = true`, `nvidia-smi` inside the VM should
   list the card.

## The CUDA compile-time pin

The first time CUDA builds, it compiles kernels for a set of GPU architectures.
By default that is six to eight of them, which is slow. hearth pins the build to
just the card's compute capability:

```nix
nixpkgs.config.cudaCapabilities = [ "7.5" ];
nixpkgs.config.cudaForwardCompat = false;
```

`7.5` is the Turing architecture, which covers both the RTX 2060 and the GTX 1660
Ti. Pinning it cuts the `ollama-cuda` build from well over an hour to roughly an
eighth of that, and makes later rebuilds fast. If you run a different card, set
its compute capability instead.

## Verifying

Once passthrough (if any) and the driver are in place:

```sh
# the card is visible
nvidia-smi

# a model actually runs on the GPU (watch nvidia-smi for resident memory)
hearth-agent --agent-name gpu-check --model llama3.2:3b "Say hello."
```

The Ollama logs will show CUDA buffers when inference runs on the GPU rather than
the CPU.
