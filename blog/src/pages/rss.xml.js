import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';

export async function GET(context) {
  const posts = await getCollection('posts');
  return rss({
    title: 'Content Factory blog',
    description: 'PC gaming + desk-setup, long-form.',
    site: context.site,
    items: posts.map(p => ({
      title: p.data.title,
      pubDate: p.data.date,
      link: `/posts/${p.slug}/`,
    })),
  });
}
