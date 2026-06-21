# Hearth Docs Site — Design

Date: 2026-06-21
Status: Approved

## Goal

Publish the hearth documentation as a clean, intuitive website on GitHub Pages,
styled to feel like the Claude Code docs (left sidebar, top search, dark/light
toggle, right-hand TOC, copy-button code blocks). The site is the canonical home
for hearth docs going forward and is linked prominently from the repo README. It
must be easy to keep extending as the project grows.

This is an original implementation inspired by the Claude Code docs aesthetic. It
does not copy Anthropic's proprietary site code or assets.

## Constraints & context

- hearth is a declarative NixOS flake, not a from-scratch distro. Install docs are
  framed around NixOS: apply to an existing NixOS host, install fresh in a VM
  (Proxmox) or bare metal, and a primer for newcomers on getting Linux/NixOS ready.
- The project is a work in progress. Docs must be honest about what is built vs.
  roadmap-only (callout banners where relevant).
- Existing prose docs already exist in `docs/*.md` and `START_HERE.md` and are rich.
- Repo: `EricFinland/hearth`, default branch `main`. Project-pages URL will be
  `https://ericfinland.github.io/hearth/`.

## Tech approach

**Astro Starlight**, themed with custom CSS.

- Starlight provides the exact docs skeleton out of the box: left sidebar nav,
  built-in search (Pagefind), dark/light toggle, right-hand "On this page" TOC,
  and copy-button code blocks.
- Content is Markdown/MDX, so the existing docs migrate directly.
- Deploys to GitHub Pages via the official `withastro/action` workflow.
- Chosen over fully-custom HTML (too much hand-maintenance as docs grow) and
  Docusaurus (heavier, default theme further from the Claude look).

## Site location & build

- New `site/` directory at the repo root holds the Astro Starlight project.
- `astro.config.mjs`:
  - `site: 'https://ericfinland.github.io'`
  - `base: '/hearth'`
  - Starlight integration with the sidebar, social link to the GitHub repo, and
    custom CSS.
- Node build step runs only in CI; no local Node required to author content
  (though `npm run dev` is available for previewing).

## Theming (the Claude-Code feel)

Override Starlight CSS custom properties in `site/src/styles/theme.css`:

- **Palette**: warm off-white/cream light mode; deep warm-charcoal dark mode;
  clay/terracotta accent (approximately `#CC785C`). Honest neutral grays.
- **Type**: serif display headings (a free Tiempos-like serif such as Newsreader
  or Fraunces, loaded via the build or a CSS font stack) + clean sans body
  (system/Inter). Generous line-height and spacing for a calm, editorial density.
- **Landing page**: hero with tagline, three "Get started" cards linking to the
  install paths, and a feature grid. Built with Starlight's splash template +
  card components.

## Information architecture (sidebar)

```
Getting Started
  - Overview (landing)
  - What is hearth
  - Quickstart

Installation
  - Choose your path        (decision page -> the 3 below)
  - Existing NixOS host      ("pre-existing device")
  - Fresh install (VM / Proxmox)  ("fresh device")
  - Linux / NixOS primer     ("how to set up Linux")

Concepts
  - Architecture            (from ARCHITECTURE.md)
  - Features                (from FEATURES.md)
  - Sandboxing & threat model  (split out of ARCHITECTURE.md)
  - Observability & audit

Operations
  - Runbook                 (from RUNBOOK.md)
  - Demo                    (from DEMO.md)

Project
  - Roadmap                 (from ROADMAP.md)
  - Decision records        (from DECISIONS.md)
  - Project status          (from START_HERE.md)
```

## Content plan (v1)

**Migrate** all existing prose into Starlight pages under `site/src/content/docs/`:

- Add `title` + `description` frontmatter to each page.
- Fix relative links to point at site routes.
- Split the threat-model section of ARCHITECTURE.md into its own
  "Sandboxing & threat model" page; keep system diagram + module map in Architecture.
- Preserve existing ASCII diagrams verbatim (no image conversion in v1).

**Write new** pages:

- Landing / Overview (hero + cards + feature grid).
- What is hearth (the "what it is / is not" framing, expanded).
- Quickstart (the README quickstart, polished).
- Installation "Choose your path" (decision page).
- Existing NixOS host guide (`nixos-rebuild switch --flake .#workstation`).
- Fresh install guide (VM / Proxmox) — friendly on-ramp that links into RUNBOOK
  for the deep operational steps; avoids duplicating them wholesale.
- Linux / NixOS primer (getting a NixOS machine ready so a newcomer can reach step 1).

All pages use callout banners (Starlight asides) to mark roadmap-only features
honestly.

## GitHub Pages deploy

- New workflow `.github/workflows/deploy-docs.yml`:
  - Triggers on push to `main` (optionally path-filtered to `site/**`).
  - Uses `withastro/action` to build, then `actions/deploy-pages` to publish.
  - Requires Pages source set to "GitHub Actions" in repo settings (a one-time
    manual step the user performs; documented in the plan).
- Existing `build.yml` (nix flake check) is left untouched.

## README integration

- Add a "📖 Documentation" link/badge near the top of README.md pointing to the
  live site.
- Update the Documentation section to link to the site as the primary entry point,
  keeping the in-repo doc links as secondary references.

## Out of scope (v1, clean follow-ups)

- Custom domain (e.g. `docs.hearth.dev`) + DNS.
- Converting ASCII diagrams to rendered images/SVG.
- Deep per-feature tutorials, screenshots, and full command reference.
- Deleting or stubbing the original `docs/*.md` (kept in place this pass; de-dup
  is a follow-up once the site is the established canonical source).

## Success criteria

- `npm run build` in `site/` produces a static site with no errors.
- Pushing to `main` publishes to `https://ericfinland.github.io/hearth/`.
- Sidebar matches the IA above; all migrated and new pages render with working
  nav, search, and dark/light toggle.
- The site visually evokes the Claude Code docs (warm palette, clay accent, serif
  headings, sidebar + TOC layout).
- README links to the live docs.
