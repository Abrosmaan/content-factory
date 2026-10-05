import { defineConfig } from 'astro/config';

export default defineConfig({
  site: process.env.BLOG_DOMAIN
    ? `https://${process.env.BLOG_DOMAIN}`
    : 'https://example.com',
  build: { format: 'directory' },
  vite: { build: { assetsInlineLimit: 0 } },
});
