---
title: Secrets (sops-nix)
description: Set up encrypted secrets so no plaintext keys live in the repo.
---

Secrets like API keys and tokens must not live in plaintext in the repo. hearth
uses [sops-nix](https://github.com/Mic92/sops-nix), chosen over agenix for its
support of multiple key types (age, PGP, and cloud KMS). See
[Decision records](/hearth/project/decisions/) ADR-003 for the reasoning.

The repo ships a `.sops.yaml` and a placeholder `secrets/example.yaml`, but it
does not ship any key. You provide your own.

:::caution[Status]
sops-nix is wired into the flake, but the key setup below is a one-time step you
must run before any real secret can be encrypted or decrypted. Until then,
nothing decrypts.
:::

## 1. Generate an age key

```sh
age-keygen -o ~/.config/sops/age/keys.txt
```

Note the public key it prints. It looks like `age1...`.

## 2. Add a creation rule to `.sops.yaml`

At the repo root, map your secret files to that public key:

```yaml
creation_rules:
  - path_regex: secrets/.*\.yaml$
    age: age1your_public_key_here
```

## 3. Create an encrypted secret

```sh
sops secrets/example.yaml
```

This opens your editor; what you save is encrypted at rest with your key.

## 4. Reference the secret in NixOS

Point the decrypted output at a private directory owned by the `hearth` user:

```nix
sops.secrets."my-api-key" = {
  owner = "hearth";
  path = "/var/lib/hearth/secrets/my-api-key";
  mode = "0400";
};
```

Decrypted secrets live under `/var/lib/hearth/secrets`, which is `0700`. Because
the agent sandbox runs each agent as a different `DynamicUser`, agents cannot read
that directory even though it is on the same machine. That is by design. See
[Sandboxing & threat model](/hearth/concepts/sandboxing/).

## What not to do

- Do not commit `keys.txt` or any unencrypted secret. The repo's `.gitignore`
  already excludes `*.key` and the encrypted `secrets/*.yaml` except the example.
- Do not place decrypted secrets anywhere an agent's allow list can reach.
