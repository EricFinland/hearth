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
