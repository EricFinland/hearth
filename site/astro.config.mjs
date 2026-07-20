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
      logo: { src: './src/assets/logo.svg', alt: 'hearth flame logo' },
      favicon: '/favicon.svg',
      social: {
        github: 'https://github.com/EricFinland/hearth',
      },
      editLink: {
        baseUrl: 'https://github.com/EricFinland/hearth/edit/main/site/',
      },
      lastUpdated: true,
      components: {
        Footer: './src/components/Footer.astro',
      },
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
        { tag: 'meta', attrs: { property: 'og:image', content: 'https://ericfinland.github.io/hearth/og.svg' } },
        { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://ericfinland.github.io/hearth/og.svg' } },
        { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
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
            { label: 'Agent engine', link: '/concepts/agent-engine/' },
            { label: 'Knowledge base (RAG)', link: '/concepts/knowledge-base/' },
            { label: 'Permission modes & approvals', link: '/concepts/permission-modes/' },
            { label: 'Per-run containment', link: '/concepts/per-run-containment/' },
            { label: 'Autonomy & self-improvement', link: '/concepts/autonomy/' },
            { label: 'Sandboxing & threat model', link: '/concepts/sandboxing/' },
            { label: 'Observability & audit', link: '/concepts/observability/' },
            { label: 'MCP audit gate', link: '/concepts/mcp-audit-gate/' },
          ],
        },
        {
          label: 'Operations',
          items: [
            { label: 'Runbook', link: '/operations/runbook/' },
            { label: 'Command reference', link: '/operations/commands/' },
            { label: 'World view', link: '/operations/world-view/' },
            { label: 'Command center', link: '/operations/command-center/' },
            { label: 'Standing missions', link: '/operations/scheduler/' },
            { label: 'Replay & flight recorder', link: '/operations/replay/' },
            { label: 'Governor (budget & alerts)', link: '/operations/governor/' },
            { label: 'Model router', link: '/operations/router/' },
            { label: 'Map dashboard', link: '/operations/map-dashboard/' },
            { label: 'Demo', link: '/operations/demo/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'OpenAI-compatible API', link: '/reference/openai-api/' },
            { label: 'Configuration reference', link: '/reference/configuration/' },
            { label: 'Hosts & images', link: '/reference/hosts-and-images/' },
            { label: 'Secrets (sops-nix)', link: '/reference/secrets/' },
            { label: 'Agent credentials', link: '/reference/agent-credentials/' },
            { label: 'GPU passthrough', link: '/reference/gpu-passthrough/' },
            { label: 'Networking & remote access', link: '/reference/networking/' },
            { label: 'Telegram check-ins', link: '/reference/telegram/' },
            { label: 'Content toolchain', link: '/reference/content-toolchain/' },
            { label: 'Desktop', link: '/reference/desktop/' },
            { label: 'Troubleshooting & FAQ', link: '/reference/troubleshooting/' },
          ],
        },
        {
          label: 'Project',
          items: [
            { label: 'About', link: '/project/about/' },
            { label: 'Security model', link: '/project/security/' },
            { label: 'Contributing', link: '/project/contributing/' },
            { label: 'Roadmap', link: '/project/roadmap/' },
            { label: 'Decision records', link: '/project/decisions/' },
            { label: 'Project status', link: '/project/status/' },
          ],
        },
      ],
    }),
  ],
});
