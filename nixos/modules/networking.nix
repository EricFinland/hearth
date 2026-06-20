# networking.nix: Tailscale mesh plus a tight firewall.
{ ... }:
{
  services.tailscale.enable = true;

  networking.firewall = {
    enable = true;

    # Trust the Tailscale interface fully. Anything reachable only over the
    # mesh is treated as private.
    trustedInterfaces = [ "tailscale0" ];

    # On the public interface, allow only SSH and the local Ollama API port.
    # NOTE: port 11434 (Ollama) should NOT be exposed on a public interface in
    # production. Prefer reaching it over Tailscale only. It is opened here for
    # homelab convenience; remove it from allowedTCPPorts and rely on
    # trustedInterfaces if this host ever faces the open internet.
    allowedTCPPorts = [ 22 11434 ];
  };
}
