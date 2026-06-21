# Hearth Docs Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish hearth's documentation as a Claude-Code-styled website on GitHub Pages, built with Astro Starlight, with all existing docs migrated and three new install guides written.

**Architecture:** A self-contained Astro Starlight project in `site/`. Existing Markdown docs are migrated into `site/src/content/docs/` with frontmatter and base-aware links. A GitHub Actions workflow builds and deploys to GitHub Pages on every push to `main`. The repo README links to the live site.

**Tech Stack:** Astro 5, `@astrojs/starlight` 0.30+, Node 20 (CI only), GitHub Pages, `withastro/action`.

---

## Conventions for this plan

- **Working directory** for all commands is the repo root: `C:\Users\ericc\OneDrive\Desktop\hearth` (Git Bash). The Astro project lives in `site/`.
- **Commits:** every commit MUST use the identity override:
  `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit ...`
  No AI attribution anywhere in messages or content. No em-dashes in committed files.
- **Base path:** the site deploys under `/hearth/`. Therefore **every in-content absolute link must start with `/hearth/`** (e.g. `/hearth/installation/choose-your-path/`). This is the deterministic, base-safe convention. Sidebar links in `astro.config.mjs` use root-relative paths (e.g. `/installation/...`) because Starlight prefixes the base for sidebar entries automatically.
- **Verification** in a docs project is `npm --prefix site run build` succeeding with zero errors, plus a spot check that expected output files exist. There is no unit-test runner here; the build is the test.
- **Node availability:** if `node`/`npm` are not installed locally, author all files anyway and rely on CI to build. Where a task says "run the build," attempt it; if Node is absent, note it and proceed (CI is the source of truth). Do not install Node system-wide as part of this plan.

---

## File structure

```
site/
  package.json
  astro.config.mjs
  tsconfig.json
  src/
    content.config.ts
    styles/
      theme.css
    content/
      docs/
        index.mdx                         (landing / splash)
        getting-started/
          what-is-hearth.md
          quickstart.md
        installation/
          choose-your-path.md
          existing-nixos-host.md
          fresh-install.md
          linux-primer.md
        concepts/
          architecture.md                 (migrated, minus threat model)
          features.md                      (migrated)
          sandboxing.md                    (split from ARCHITECTURE.md)
          observability.md                 (new, derived)
        operations/
          runbook.md                       (migrated)
          demo.md                          (migrated)
        project/
          roadmap.md                       (migrated)
          decisions.md                     (migrated)
          status.md                        (migrated from START_HERE.md)
  public/
    .nojekyll                              (prevents Jekyll processing)
.github/workflows/deploy-docs.yml          (new)
README.md                                  (modified)
.gitignore                                 (modified: ignore site/node_modules, site/dist)
```

---

## Task 1: Scaffold the Starlight project

**Files:**
- Create: `site/package.json`
- Create: `site/tsconfig.json`
- Create: `site/src/content.config.ts`
- Create: `site/public/.nojekyll`
- Modify: `.gitignore`

- [ ] **Step 1: Create `site/package.json`**

```json
{
  "name": "hearth-docs",
  "type": "module",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview"
  },
  "dependencies": {
    "astro": "^5.1.0",
    "@astrojs/starlight": "^0.30.0",
    "sharp": "^0.33.5"
  }
}
```

- [ ] **Step 2: Create `site/tsconfig.json`**

```json
{
  "extends": "astro/tsconfigs/strict",
  "include": [".astro/types.d.ts", "**/*"],
  "exclude": ["dist"]
}
```

- [ ] **Step 3: Create `site/src/content.config.ts`**

```ts
import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema() }),
};
```

- [ ] **Step 4: Create `site/public/.nojekyll`** (empty file)

Content: a single empty line is fine. This stops GitHub Pages from running Jekyll on the built output.

- [ ] **Step 5: Append to `.gitignore`**

Add these lines to the repo root `.gitignore`:

```
# docs site build artifacts
site/node_modules/
site/dist/
site/.astro/
```

- [ ] **Step 6: Install dependencies**

Run: `npm --prefix site install`
Expected: `node_modules` populated, no error. (If Node is unavailable locally, skip and note that CI will install.)

- [ ] **Step 7: Commit**

```bash
git add site/package.json site/tsconfig.json site/src/content.config.ts site/public/.nojekyll .gitignore
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "chore(docs-site): scaffold Astro Starlight project"
```

---

## Task 2: Configure Astro + Starlight (sidebar, base, fonts)

**Files:**
- Create: `site/astro.config.mjs`

- [ ] **Step 1: Create `site/astro.config.mjs`**

```js
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://ericfinland.github.io',
  base: '/hearth',
  integrations: [
    starlight({
      title: 'hearth',
      description:
        'A security-first NixOS system for running local LLMs and sandboxed agents.',
      social: [
        { icon: 'github', label: 'GitHub', href: 'https://github.com/EricFinland/hearth' },
      ],
      customCss: ['./src/styles/theme.css'],
      head: [
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.googleapis.com' } },
        { tag: 'link', attrs: { rel: 'preconnect', href: 'https://fonts.gstatic.com', crossorigin: true } },
        {
          tag: 'link',
          attrs: {
            rel: 'stylesheet',
            href: 'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&display=swap',
          },
        },
      ],
      sidebar: [
        {
          label: 'Getting Started',
          items: [
            { label: 'Overview', link: '/' },
            { label: 'What is hearth', link: '/getting-started/what-is-hearth/' },
            { label: 'Quickstart', link: '/getting-started/quickstart/' },
          ],
        },
        {
          label: 'Installation',
          items: [
            { label: 'Choose your path', link: '/installation/choose-your-path/' },
            { label: 'Existing NixOS host', link: '/installation/existing-nixos-host/' },
            { label: 'Fresh install (VM / Proxmox)', link: '/installation/fresh-install/' },
            { label: 'Linux / NixOS primer', link: '/installation/linux-primer/' },
          ],
        },
        {
          label: 'Concepts',
          items: [
            { label: 'Architecture', link: '/concepts/architecture/' },
            { label: 'Features', link: '/concepts/features/' },
            { label: 'Sandboxing & threat model', link: '/concepts/sandboxing/' },
            { label: 'Observability & audit', link: '/concepts/observability/' },
          ],
        },
        {
          label: 'Operations',
          items: [
            { label: 'Runbook', link: '/operations/runbook/' },
            { label: 'Demo', link: '/operations/demo/' },
          ],
        },
        {
          label: 'Project',
          items: [
            { label: 'Roadmap', link: '/project/roadmap/' },
            { label: 'Decision records', link: '/project/decisions/' },
            { label: 'Project status', link: '/project/status/' },
          ],
        },
      ],
    }),
  ],
});
```

- [ ] **Step 2: Commit**

```bash
git add site/astro.config.mjs
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "chore(docs-site): configure starlight sidebar, base path, and fonts"
```

---

## Task 3: Theme the site to match the Claude Code docs feel

**Files:**
- Create: `site/src/styles/theme.css`

- [ ] **Step 1: Create `site/src/styles/theme.css`**

```css
/* hearth docs theme: warm palette, clay accent, serif display headings.
   Original styling inspired by the Claude Code docs aesthetic. */

:root {
  --sl-font: 'Inter', ui-sans-serif, system-ui, -apple-system, sans-serif;
  --sl-font-serif: 'Newsreader', Georgia, 'Times New Roman', serif;

  /* Clay / terracotta accent */
  --sl-color-accent-low: #2a160e;
  --sl-color-accent: #cc785c;
  --sl-color-accent-high: #eab59e;

  /* Dark mode (default): warm charcoal */
  --sl-color-white: #faf7f2;
  --sl-color-gray-1: #ece8e1;
  --sl-color-gray-2: #c6c0b6;
  --sl-color-gray-3: #948d82;
  --sl-color-gray-4: #5d564c;
  --sl-color-gray-5: #3b352d;
  --sl-color-gray-6: #2a251f;
  --sl-color-black: #1c1813;

  --sl-color-bg: #1c1813;
  --sl-color-bg-nav: #211c16;
  --sl-color-bg-sidebar: #211c16;
}

:root[data-theme='light'] {
  /* Light mode: warm cream */
  --sl-color-accent-low: #f3ddd3;
  --sl-color-accent: #b5613f;
  --sl-color-accent-high: #5a2c1a;

  --sl-color-white: #20180f;
  --sl-color-gray-1: #2c241b;
  --sl-color-gray-2: #463c30;
  --sl-color-gray-3: #6f6557;
  --sl-color-gray-4: #988d7c;
  --sl-color-gray-5: #d8d0c4;
  --sl-color-gray-6: #ece6db;
  --sl-color-gray-7: #f5f1e9;
  --sl-color-black: #faf7f2;

  --sl-color-bg: #faf7f2;
  --sl-color-bg-nav: #f5f1e9;
  --sl-color-bg-sidebar: #f5f1e9;
}

/* Serif display headings, the editorial Claude feel */
.sl-markdown-content h1,
.sl-markdown-content h2,
.sl-markdown-content h3,
.sl-markdown-content h4,
h1#_top,
.hero h1,
.site-title {
  font-family: var(--sl-font-serif);
  font-weight: 500;
  letter-spacing: -0.01em;
}

/* Slightly calmer body rhythm */
.sl-markdown-content {
  --sl-content-width: 50rem;
  line-height: 1.7;
}

/* Hero polish on the splash page */
.hero {
  padding-block: 2rem;
}
.hero .tagline {
  font-size: var(--sl-text-lg);
  color: var(--sl-color-gray-2);
}
```

- [ ] **Step 2: Build to verify CSS parses and theme applies**

Run: `npm --prefix site run build`
Expected: build succeeds (it will warn about missing pages until later tasks add content; that is fine if it still exits 0. If the build errors only because `src/content/docs` is empty, proceed to Task 4 and build again there.)

- [ ] **Step 3: Commit**

```bash
git add site/src/styles/theme.css
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat(docs-site): warm clay theme with serif headings"
```

---

## Task 4: Landing page (splash hero + cards)

**Files:**
- Create: `site/src/content/docs/index.mdx`

- [ ] **Step 1: Create `site/src/content/docs/index.mdx`**

```mdx
---
title: hearth
description: A security-first NixOS system for running local LLMs and sandboxed agents.
template: splash
hero:
  tagline: An opinionated, reproducible, security-first Linux system where local LLMs and agents run sandboxed by default, every run is audited, and system state is legible from boot.
  actions:
    - text: Get started
      link: /hearth/installation/choose-your-path/
      icon: right-arrow
      variant: primary
    - text: View on GitHub
      link: https://github.com/EricFinland/hearth
      icon: external
      variant: minimal
---

import { Card, CardGrid, LinkCard } from '@astrojs/starlight/components';

## Get started

<CardGrid>
  <LinkCard
    title="Existing NixOS host"
    description="Already running NixOS? Apply hearth with a single rebuild."
    href="/hearth/installation/existing-nixos-host/"
  />
  <LinkCard
    title="Fresh install (VM / Proxmox)"
    description="Build an image and boot hearth in a virtual machine or on bare metal."
    href="/hearth/installation/fresh-install/"
  />
  <LinkCard
    title="New to Linux?"
    description="Start with the NixOS primer, then come back and pick a path."
    href="/hearth/installation/linux-primer/"
  />
</CardGrid>

## Why hearth

<CardGrid>
  <Card title="Sandboxed by default" icon="approve-check">
    Agents run as ephemeral, isolated systemd processes. They cannot read host
    secrets or write outside their allowed paths.
  </Card>
  <Card title="Every run audited" icon="list-format">
    Tokens, cost, latency, and errors for every agent run land in a local SQLite
    store. Query the last 20 runs in one command.
  </Card>
  <Card title="Reproducible" icon="seti:nix">
    The whole OS is one flake. A rebuild brings any NixOS host to the exact
    defined state, with atomic, bootloader-level rollback.
  </Card>
  <Card title="Legible from boot" icon="laptop">
    A boot dashboard shows model status, system state, and recent runs the moment
    you log in.
  </Card>
</CardGrid>

:::note[Work in progress]
hearth is under active development. Pages call out which capabilities are built
today versus on the roadmap. See the [Roadmap](/hearth/project/roadmap/) for status.
:::
```

- [ ] **Step 2: Build**

Run: `npm --prefix site run build`
Expected: build succeeds; `site/dist/index.html` exists.

- [ ] **Step 3: Commit**

```bash
git add site/src/content/docs/index.mdx
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat(docs-site): landing page with hero and cards"
```

---

## Task 5: Getting Started pages

**Files:**
- Create: `site/src/content/docs/getting-started/what-is-hearth.md`
- Create: `site/src/content/docs/getting-started/quickstart.md`

- [ ] **Step 1: Create `what-is-hearth.md`**

```md
---
title: What is hearth
description: What hearth is, what it is not, and who it is for.
---

hearth is a declarative NixOS configuration for running local language models and
autonomous agents on hardware you control. The entire operating system is defined
in one `flake.nix` that Nix builds reproducibly and deploys to any NixOS host or
Proxmox VM.

## What it is not

hearth is not a custom Linux kernel and not a remastered distro. There is no ISO
to flash with a bespoke userland. It is a single flake that configures stock
NixOS, which means you get reproducibility and atomic rollback for free.

## Why it exists

Most people running local agents are flying blind: agents run with full system
privileges and leave no record of what they did. hearth makes agent activity
legible and contained at the operating-system level.

- **Contained.** Every agent run is sandboxed with systemd isolation primitives.
- **Legible.** Every run records its token count, cost, latency, and errors to a
  local SQLite database.
- **Reproducible.** The flake lock pins every input, so two builds produce the
  same system.

## Who it is for

People running local LLMs and agents on a homelab, a workstation, or a VM who
want least-privilege isolation and a real audit trail instead of trust by default.

:::note[Status]
hearth is a work in progress. See [Project status](/hearth/project/status/) and the
[Roadmap](/hearth/project/roadmap/) for exactly what is built today.
:::

## Next steps

- [Quickstart](/hearth/getting-started/quickstart/) to validate the flake.
- [Choose your install path](/hearth/installation/choose-your-path/).
- [Architecture](/hearth/concepts/architecture/) for the system design and module map.
```

- [ ] **Step 2: Create `quickstart.md`**

```md
---
title: Quickstart
description: Clone hearth, validate the flake, and build an image.
---

This gets you from zero to a validated flake and a buildable image. It assumes a
machine with Nix and flakes enabled.

:::caution[You need Nix]
Every command here uses `nix`. Windows cannot run them directly. Use a Mac, a
Linux box, or a NixOS host. New to this? Start with the
[Linux / NixOS primer](/hearth/installation/linux-primer/).
:::

## 1. Clone

```sh
git clone https://github.com/EricFinland/hearth
cd hearth
```

## 2. Validate the flake

The first run fetches inputs and takes a few minutes.

```sh
nix flake check
```

## 3. Build a Proxmox-compatible image

```sh
bash scripts/build-image.sh
```

## 4. Apply to an existing NixOS host

```sh
bash scripts/bootstrap.sh
```

## Where to go next

- Deploying to a machine that already runs NixOS? See
  [Existing NixOS host](/hearth/installation/existing-nixos-host/).
- Starting from nothing? See
  [Fresh install (VM / Proxmox)](/hearth/installation/fresh-install/).
- For the full operational sequence on real hardware, see the
  [Runbook](/hearth/operations/runbook/).
```

- [ ] **Step 3: Build**

Run: `npm --prefix site run build`
Expected: build succeeds; both pages render.

- [ ] **Step 4: Commit**

```bash
git add site/src/content/docs/getting-started
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs(site): getting started pages"
```

---

## Task 6: Installation pages

**Files:**
- Create: `site/src/content/docs/installation/choose-your-path.md`
- Create: `site/src/content/docs/installation/existing-nixos-host.md`
- Create: `site/src/content/docs/installation/fresh-install.md`
- Create: `site/src/content/docs/installation/linux-primer.md`

- [ ] **Step 1: Create `choose-your-path.md`**

```md
---
title: Choose your path
description: Pick the install guide that matches your situation.
---

hearth is a NixOS flake. How you install it depends on what you are starting from.
Pick the row that matches you.

| Your situation | Start here |
| --- | --- |
| You already run NixOS on the target machine | [Existing NixOS host](/hearth/installation/existing-nixos-host/) |
| You have a spare machine or a hypervisor (Proxmox, etc.) | [Fresh install (VM / Proxmox)](/hearth/installation/fresh-install/) |
| You are new to Linux or NixOS | [Linux / NixOS primer](/hearth/installation/linux-primer/), then come back |

:::tip
If you just want to read the config and validate it without deploying anything,
the [Quickstart](/hearth/getting-started/quickstart/) is enough. You only need a
machine with Nix.
:::
```

- [ ] **Step 2: Create `existing-nixos-host.md`**

```md
---
title: Existing NixOS host
description: Apply hearth to a machine that already runs NixOS.
---

If your target machine already runs NixOS, applying hearth is a single rebuild
against this flake.

:::caution[Prerequisites]
- The machine runs NixOS with flakes enabled.
- You have a hardware configuration for it (NixOS generates one at install time).
- You have your SSH public key. hearth disables SSH password auth.
:::

## 1. Get the flake

Clone the repo onto the host, or reference it as a flake input from your own
configuration.

```sh
git clone https://github.com/EricFinland/hearth
cd hearth
```

## 2. Add your SSH key

Edit `nixos/hosts/workstation.nix` and add your public key so you keep access
after the rebuild:

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

## 3. Rebuild

Apply the `workstation` configuration:

```sh
sudo nixos-rebuild switch --flake .#workstation
```

This creates a new generation. If anything breaks, roll back with
`sudo nixos-rebuild switch --rollback` or pick a previous generation from the
bootloader.

## 4. Verify

```sh
hearth-status
```

You should see Ollama active and the recent-runs section.

:::note
The `workstation` host targets specific hardware (see
`nixos/hosts/workstation.nix`). For different hardware, copy that host file and
adjust the imports for your machine. The deep operational walkthrough lives in the
[Runbook](/hearth/operations/runbook/).
:::
```

- [ ] **Step 3: Create `fresh-install.md`**

```md
---
title: Fresh install (VM / Proxmox)
description: Build a hearth image and boot it in a virtual machine or on bare metal.
---

Starting from nothing? Build an image from the flake and boot it. The primary
documented target is a Proxmox VM, but any hypervisor or bare-metal machine that
can boot a NixOS image works.

:::caution[Prerequisites]
- A machine with Nix and flakes to build the image (a Mac or Linux box). Windows
  cannot build it. See the [Quickstart](/hearth/getting-started/quickstart/).
- A Proxmox node or other hypervisor to run it.
- Your SSH public key (`ssh-keygen -t ed25519` if you do not have one).
:::

## 1. Add your SSH key before building

hearth disables SSH password auth, so bake your key in first. Edit
`nixos/hosts/workstation.nix`:

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

## 2. Build the image

```sh
cd hearth
bash scripts/build-image.sh
```

## 3. Boot it

Upload the built image to your hypervisor and boot it as a new VM. On Proxmox,
import the disk and attach it to a new VM. Until your key takes effect you can
reach the box through the Proxmox web console (user `operator`, initial password
`hearth`, which you should change with `passwd` on first login).

## 4. Apply updates over SSH

Once it boots and you can SSH in, future changes are just rebuilds against the
repo:

```sh
sudo nixos-rebuild switch --flake .#workstation
```

:::note[Full hardware walkthrough]
GPU passthrough, disk import, and the exact Proxmox steps are operational and
need real hardware. They are documented step by step in the
[Runbook](/hearth/operations/runbook/).
:::
```

- [ ] **Step 4: Create `linux-primer.md`**

```md
---
title: Linux / NixOS primer
description: For newcomers. Get a NixOS machine ready so you can install hearth.
---

hearth is built on NixOS. If Linux is new to you, this page gets you to the
starting line. It is a map, not a full tutorial, with links to the official docs
at each step.

## What NixOS is

NixOS is a Linux distribution where the whole system is described by configuration
files instead of changed by hand. You declare what you want, run a rebuild, and
the system matches your declaration. If a change breaks something, you roll back
to a previous generation from the boot menu. That is exactly the property hearth
relies on.

## The path to running hearth

1. **Get a machine to run it on.** A spare laptop or desktop, or a VM on a
   hypervisor like Proxmox or VirtualBox. hearth assumes x86_64.
2. **Install NixOS.** Download the ISO and follow the official guide:
   [nixos.org/download](https://nixos.org/download) and the
   [NixOS manual installation guide](https://nixos.org/manual/nixos/stable/#sec-installation).
   During install, NixOS generates a hardware configuration for your machine.
3. **Enable flakes.** hearth is a flake. Enable the feature by adding this to your
   configuration and rebuilding:

   ```nix
   nix.settings.experimental-features = [ "nix-command" "flakes" ];
   ```

4. **Pick your path.** Now you have a NixOS machine. Continue with
   [Existing NixOS host](/hearth/installation/existing-nixos-host/), or build a
   dedicated image with [Fresh install](/hearth/installation/fresh-install/).

## Helpful references

- [Nix & NixOS official site](https://nixos.org)
- [The NixOS manual](https://nixos.org/manual/nixos/stable/)
- [nix.dev tutorials](https://nix.dev)

:::tip
You do not need to master Nix to try hearth. Get NixOS installed with flakes
enabled, then follow an install guide. You can learn the language as you go.
:::
```

- [ ] **Step 5: Build**

Run: `npm --prefix site run build`
Expected: build succeeds; all four installation pages render.

- [ ] **Step 6: Commit**

```bash
git add site/src/content/docs/installation
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs(site): installation guides (existing host, fresh install, linux primer)"
```

---

## Task 7: Migrate Concepts pages

Source files live at the repo root under `docs/`. For each migrated page: read the
source, **drop the top-level `# Heading`** (Starlight renders the title from
frontmatter), add the frontmatter shown below, and rewrite any in-repo links
(e.g. `docs/RUNBOOK.md`) to site routes per the link map. Preserve all ASCII
diagrams and code blocks verbatim.

**Link map (apply in every migrated page):**

| Source link | Site link |
| --- | --- |
| `docs/ARCHITECTURE.md` | `/hearth/concepts/architecture/` |
| `docs/FEATURES.md` | `/hearth/concepts/features/` |
| `docs/ROADMAP.md` | `/hearth/project/roadmap/` |
| `docs/RUNBOOK.md` | `/hearth/operations/runbook/` |
| `docs/DEMO.md` | `/hearth/operations/demo/` |
| `docs/DECISIONS.md` | `/hearth/project/decisions/` |
| `START_HERE.md` | `/hearth/project/status/` |

**Files:**
- Create: `site/src/content/docs/concepts/architecture.md`
- Create: `site/src/content/docs/concepts/sandboxing.md`
- Create: `site/src/content/docs/concepts/features.md`
- Create: `site/src/content/docs/concepts/observability.md`

- [ ] **Step 1: Create `concepts/architecture.md`**

Copy the body of `docs/ARCHITECTURE.md` **except** the threat-model section
(that moves to `sandboxing.md` in Step 2). Keep the system diagram and module
responsibilities. Prepend:

```md
---
title: Architecture
description: System diagram, module responsibilities, and how hearth is deployed.
---
```

Add this line near the top, after the intro paragraph:

```md
For the isolation model and threat analysis, see [Sandboxing & threat model](/hearth/concepts/sandboxing/).
```

- [ ] **Step 2: Create `concepts/sandboxing.md`**

Move the threat-model / sandboxing section out of `docs/ARCHITECTURE.md` into this
page. Prepend:

```md
---
title: Sandboxing & threat model
description: How hearth isolates agents and what the sandbox is designed to stop.
---
```

If the source threat-model section references "above" or "the diagram," add at the
top:

```md
This builds on the [Architecture](/hearth/concepts/architecture/) overview.
```

- [ ] **Step 3: Create `concepts/features.md`**

Copy the body of `docs/FEATURES.md` (drop its `# hearth Feature List` heading).
Prepend:

```md
---
title: Features
description: What hearth does today, the differentiators, and what is captured for later.
---
```

- [ ] **Step 4: Create `concepts/observability.md`** (new, derived)

```md
---
title: Observability & audit
description: How every agent run is recorded and how to query the audit log.
---

Every agent run on hearth is recorded. There is no trust-by-default: if an agent
ran, there is a row for it.

## What gets recorded

For each run, hearth writes to a local SQLite database:

- token count
- cost
- latency
- errors

## Querying runs

`hearth-runs` reads the SQLite store and prints the most recent runs with their
cost and latency:

```sh
hearth-runs
```

The boot dashboard also surfaces recent runs the moment you log in, so the last
thing an agent did is visible without running a command.

## Where it fits

Observability is one of hearth's core guarantees alongside sandboxing. See
[Features](/hearth/concepts/features/) for the full list and
[Architecture](/hearth/concepts/architecture/) for where the audit store lives in
the system.
```

- [ ] **Step 5: Build**

Run: `npm --prefix site run build`
Expected: build succeeds; no broken-link warnings for the concepts pages.

- [ ] **Step 6: Commit**

```bash
git add site/src/content/docs/concepts
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs(site): migrate architecture, features, sandboxing, observability"
```

---

## Task 8: Migrate Operations pages

**Files:**
- Create: `site/src/content/docs/operations/runbook.md`
- Create: `site/src/content/docs/operations/demo.md`

- [ ] **Step 1: Create `operations/runbook.md`**

Copy the body of `docs/RUNBOOK.md` (drop its `# hearth Runbook` heading), apply
the link map from Task 7. Prepend:

```md
---
title: Runbook
description: The operational steps that need real hardware to install and run hearth.
---
```

- [ ] **Step 2: Create `operations/demo.md`**

Copy the body of `docs/DEMO.md` (drop its `# hearth Demo` heading), apply the link
map. Prepend:

```md
---
title: Demo
description: A walkthrough of hearth running, for a portfolio or live demo.
---
```

- [ ] **Step 3: Build**

Run: `npm --prefix site run build`
Expected: build succeeds.

- [ ] **Step 4: Commit**

```bash
git add site/src/content/docs/operations
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs(site): migrate runbook and demo"
```

---

## Task 9: Migrate Project pages

**Files:**
- Create: `site/src/content/docs/project/roadmap.md`
- Create: `site/src/content/docs/project/decisions.md`
- Create: `site/src/content/docs/project/status.md`

- [ ] **Step 1: Create `project/roadmap.md`**

Copy the body of `docs/ROADMAP.md` (drop its `# hearth Roadmap` heading), apply
the link map. Preserve the `- [x]` / `- [ ]` checkboxes verbatim. Prepend:

```md
---
title: Roadmap
description: The day-by-day build plan and what is done versus pending.
---
```

- [ ] **Step 2: Create `project/decisions.md`**

Copy the body of `docs/DECISIONS.md` (drop its `# hearth Decision Records`
heading), apply the link map. Prepend:

```md
---
title: Decision records
description: Architecture decision records for the choices that shaped hearth.
---
```

- [ ] **Step 3: Create `project/status.md`**

Copy the body of `START_HERE.md` (drop its `# START HERE` heading), apply the link
map. Prepend:

```md
---
title: Project status
description: A snapshot briefing of what is built, what is stubbed, and what needs input.
---
```

- [ ] **Step 4: Build**

Run: `npm --prefix site run build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
git add site/src/content/docs/project
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs(site): migrate roadmap, decision records, project status"
```

---

## Task 10: GitHub Pages deploy workflow

**Files:**
- Create: `.github/workflows/deploy-docs.yml`

- [ ] **Step 1: Create `.github/workflows/deploy-docs.yml`**

```yaml
name: Deploy Docs

on:
  push:
    branches: [main]
    paths:
      - 'site/**'
      - '.github/workflows/deploy-docs.yml'
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4
      - name: Build with Astro
        uses: withastro/action@v3
        with:
          path: ./site

  deploy:
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/deploy-docs.yml
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "ci: deploy docs site to github pages"
```

- [ ] **Step 3: Record the one-time manual setting**

After this is pushed, the repo owner must set **Settings -> Pages -> Build and
deployment -> Source -> GitHub Actions** once. Note this in the final handoff to
the user. The workflow cannot enable it.

---

## Task 11: README integration

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a docs link near the top of `README.md`**

Immediately under the existing top description line, add:

```md
**📖 Documentation: https://ericfinland.github.io/hearth/**
```

- [ ] **Step 2: Update the Documentation section**

Replace the existing `## Documentation` list so the live site is the primary entry
point, keeping the in-repo links as secondary:

```md
## Documentation

Full documentation lives at **https://ericfinland.github.io/hearth/**.

In-repo sources:

- [Roadmap](docs/ROADMAP.md): the day-by-day build plan.
- [Architecture](docs/ARCHITECTURE.md): system diagram, module responsibilities, and the threat model.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "docs: link README to the hosted docs site"
```

---

## Task 12: Final verification and push

- [ ] **Step 1: Clean build from scratch**

Run:

```sh
rm -rf site/dist
npm --prefix site run build
```

Expected: exit 0, no error. (If Node is unavailable locally, skip and rely on the
deploy workflow; note this in the handoff.)

- [ ] **Step 2: Verify expected output exists**

Check that these files exist in `site/dist/`:
- `index.html`
- `installation/choose-your-path/index.html`
- `concepts/architecture/index.html`
- `project/roadmap/index.html`

- [ ] **Step 3: Confirm no stray secrets or build artifacts are staged**

Run: `git status --porcelain`
Expected: clean tree (all work committed), and `site/dist/` / `site/node_modules/`
are ignored.

- [ ] **Step 4: Push**

```bash
git push origin main
```

- [ ] **Step 5: Hand off to the user**

Tell the user to:
1. Set **Settings -> Pages -> Source -> GitHub Actions** (one time).
2. Watch the **Deploy Docs** action run.
3. Visit **https://ericfinland.github.io/hearth/**.

---

## Self-review notes

- **Spec coverage:** site location + base (Task 1, 2), theming/Claude look (Task 3),
  landing (Task 4), getting-started + quickstart (Task 5), three install guides +
  choose-your-path (Task 6), all 7 doc migrations incl. threat-model split (Tasks
  7-9), GitHub Pages deploy (Task 10), README link (Task 11), honesty/WIP callouts
  (asides in Tasks 4-6). All spec sections map to a task.
- **Link convention:** every in-content absolute link uses the `/hearth/` base
  prefix; sidebar links in config use root-relative (Starlight prefixes base).
  Consistent across all tasks.
- **No placeholders:** new pages include full content; migrations specify exact
  frontmatter, the heading-drop rule, and a concrete link map.
- **Out of scope confirmed:** no custom domain, no image diagrams, originals under
  `docs/*.md` left in place (de-dup is a noted follow-up).
```

