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
  };

  outputs = { self, nixpkgs, nixos-generators, sops-nix, home-manager, ... }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in
    {
      # The deployable host. Target is an x86_64-linux Proxmox VM.
      nixosConfigurations.workstation = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./nixos/hosts/workstation.nix
          sops-nix.nixosModules.sops
          home-manager.nixosModules.home-manager
        ];
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
          echo "hearth dev shell. Run 'nix flake check' to validate, 'bash scripts/build-image.sh' to build an image."
        '';
      };
    };
}
