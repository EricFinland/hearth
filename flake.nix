# Run `nix flake check` to validate. First run may take several minutes to fetch inputs.
{
  description = "hearth: a reproducible, security-first NixOS host for local LLMs and sandboxed agents";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    nixos-generators = {
      url = "github:nix-community/nixos-generators";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    plasma-manager = {
      url = "github:nix-community/plasma-manager";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.home-manager.follows = "home-manager";
    };
  };

  outputs = { self, nixpkgs, nixos-generators, sops-nix, home-manager, plasma-manager, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Modules shared by the deployable host and the image builds. Defined once
      # so the host config and the generated image stay identical.
      hostModules = [
        ./nixos/hosts/workstation.nix
        sops-nix.nixosModules.sops
        home-manager.nixosModules.home-manager
      ];

      # The minimal overlay disables the LLM stack (CUDA + Ollama) so a first
      # image builds in minutes instead of compiling CUDA.
      minimalModule = { hearth.llm.enable = false; };
    in
    {
      # The deployable hosts. Target is an x86_64-linux Proxmox VM.
      nixosConfigurations.workstation = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = hostModules;
      };

      # Lean variant for the first boot. Switch to .#workstation for Day 2.
      nixosConfigurations.workstation-minimal = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = hostModules ++ [ minimalModule ];
      };

      # The physical Razer Blade 15 (bare metal, WiFi, RTX 2060). Built and
      # installed directly on the machine, not imaged. See nixos/hosts/blade.nix.
      nixosConfigurations.blade = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nixos/hosts/blade.nix
          sops-nix.nixosModules.sops
          home-manager.nixosModules.home-manager
        ];
      };

      # Proxmox-ready images. qcow-efi pairs with the systemd-boot EFI setup in
      # nixos/modules/base.nix, so build the Proxmox VM with OVMF (UEFI).
      # Build with: nix build .#image-minimal   (or .#image for the full stack)
      packages.${system} = {
        image = nixos-generators.nixosGenerate {
          inherit system;
          format = "qcow-efi";
          modules = hostModules;
        };

        image-minimal = nixos-generators.nixosGenerate {
          inherit system;
          format = "qcow-efi";
          modules = hostModules ++ [ minimalModule ];
        };
      };

      # Developer shell. Run `nix develop` to enter it.
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          pkgs.git
          pkgs.nix-output-monitor
          pkgs.sops
          pkgs.age
          nixos-generators.packages.${system}.default
          pkgs.jq
        ];

        shellHook = ''
          echo "hearth dev shell. Run 'nix flake check --no-build' to validate, 'bash scripts/build-image.sh' to build an image."
        '';
      };
    };
}
