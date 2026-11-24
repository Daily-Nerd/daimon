import {themes as prismThemes} from 'prism-react-renderer';
import type {Config} from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

// This runs in Node.js - Don't use client-side code here (browser APIs, JSX...)

// The one place the base path is written. Head tags below build from it.
const baseUrl = '/daimon/';

// One tagline per locale; Docusaurus sets the current locale while building.
const taglines: Record<string, string> = {
  en: 'Session memory your agents can prove: briefings, trust classes, and receipts for coding agents',
  es: 'Memoria de sesiones que tus agentes pueden demostrar: briefings, clases de confianza y receipts para agentes de código',
};
const locale = process.env.DOCUSAURUS_CURRENT_LOCALE ?? 'en';

const config: Config = {
  title: 'daimon',
  tagline: taglines[locale] ?? taglines.en,
  favicon: 'img/favicon.ico',

  // Future flags, see https://docusaurus.io/docs/api/docusaurus-config#future
  future: {
    v4: true, // Improve compatibility with the upcoming Docusaurus v4
  },

  // Set the production url of your site here
  url: 'https://daily-nerd.github.io',
  // Set the /<baseUrl>/ pathname under which your site is served
  // For GitHub pages deployment, it is often '/<projectName>/'
  baseUrl,

  // GitHub pages deployment config.
  // If you aren't using GitHub pages, you don't need these.
  organizationName: 'Daily-Nerd', // Usually your GitHub org/user name.
  projectName: 'daimon', // Usually your repo name.

  onBrokenLinks: 'throw',
  // A link to a heading that no longer exists resolves to the page and
  // silently drops the reader at the top, so the default 'warn' is one
  // line in a build log nobody reads. #767 orphaned two such links by
  // renaming one heading; the diff caught them, the build did not.
  onBrokenAnchors: 'throw',

  // Even if you don't use internationalization, you can use this field to set
  // useful metadata like html lang. For example, if your site is Chinese, you
  // may want to replace "en" with "zh-Hans".
  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'es'],
  },

  // Docusaurus does not prefix head tag hrefs with baseUrl, so build them here.
  headTags: [
    {tagName: 'link', attributes: {rel: 'icon', type: 'image/svg+xml', href: `${baseUrl}img/favicon.svg`}},
    {tagName: 'link', attributes: {rel: 'apple-touch-icon', sizes: '180x180', href: `${baseUrl}img/apple-touch-icon.png`}},
    {tagName: 'link', attributes: {rel: 'manifest', href: `${baseUrl}site.webmanifest`}},
  ],

  plugins: [
    [
      '@docusaurus/plugin-client-redirects',
      {
        // A page that moved keeps answering at its old address. The plugin
        // writes one static page per locale, so each entry covers /docs/...
        // and /es/docs/... alike.
        redirects: [
          {
            from: '/docs/getting-started/configuration',
            to: '/docs/reference/configuration',
          },
        ],
      },
    ],
  ],

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          // Please change this to your repo.
          // Remove this to remove the "edit this page" links.
          editUrl:
            'https://github.com/Daily-Nerd/daimon/tree/main/website/',
        },
        blog: {
          blogTitle: 'daimon blog',
          blogDescription:
            'Releases, feature explainers, and field incidents from building provable memory for AI agents.',
          showReadingTime: true,
          feedOptions: {
            type: ['rss', 'atom'],
            description:
              'daimon: releases, explainers, and field incidents.',
          },
          onInlineAuthors: 'throw',
          onUntruncatedBlogPosts: 'throw',
        },
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    // Link-preview card: ink variant, the site is dark first. Docusaurus
    // resolves it to an absolute URL and emits twitter:card summary_large_image.
    image: 'img/daimon-og-ink-1200x630.png',
    colorMode: {
      defaultMode: 'dark',
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'daimon',
      logo: {
        alt: 'daimon',
        // Small cut: the kit says use it below 24 px, the mock uses it at 28.
        src: 'img/daimon-symbol-small-ink.svg',
        srcDark: 'img/daimon-symbol-small-white.svg',
        width: 28,
        height: 28,
      },
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'tutorialSidebar',
          position: 'left',
          label: 'Docs',
        },
        {to: '/blog', label: 'Blog', position: 'left'},
                {type: 'localeDropdown', position: 'right'},
        {
          href: 'https://github.com/Daily-Nerd/daimon',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      logo: {
        alt: 'daimon',
        src: 'img/daimon-symbol-small-white.svg',
        width: 28,
        height: 28,
      },
      links: [
        {label: 'Docs', to: '/docs/'},
        {label: 'Blog', to: '/blog'},
        {label: 'GitHub', href: 'https://github.com/Daily-Nerd/daimon'},
        {label: 'PyPI', href: 'https://pypi.org/project/daimon-briefing/'},
      ],
      copyright: `Apache-2.0 licensed. Built with Docusaurus.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
