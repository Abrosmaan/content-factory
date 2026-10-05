import { defineCollection, z } from 'astro:content';

const posts = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    date: z.coerce.date(),
    video_id: z.string().optional(),
    youtube_url: z.string().url().optional(),
    products: z.array(z.object({
      name: z.string(),
      url: z.string().url(),
      price_usd: z.number().optional(),
    })).default([]),
  }),
});

export const collections = { posts };
