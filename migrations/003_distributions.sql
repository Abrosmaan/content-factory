-- 003_distributions.sql — дистрибуционный контур (E6.1).
-- ОТДЕЛЬНАЯ цепочка: статусы distributions не пересекаются с video_status
-- (изоляция контуров — раздел 2 ТЗ).

DO $$ BEGIN
  CREATE TYPE dist_channel AS ENUM ('blog', 'pinterest', 'threads');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE dist_status AS ENUM (
    'pending', 'content_ready', 'posted', 'failed', 'skipped'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS distributions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  video_id uuid NOT NULL REFERENCES videos(id),
  channel dist_channel NOT NULL,
  status dist_status NOT NULL DEFAULT 'pending',
  content jsonb,
  external_url text,
  attempts int DEFAULT 0,
  last_error text,
  scheduled_at timestamptz,
  posted_at timestamptz,
  UNIQUE (video_id, channel)
);

CREATE INDEX IF NOT EXISTS distributions_status_idx
  ON distributions (status, channel);
CREATE INDEX IF NOT EXISTS distributions_video_idx
  ON distributions (video_id);

-- threads_enabled — по умолчанию false (раздел 6.14 ТЗ)
INSERT INTO settings (key, value) VALUES ('threads_enabled', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;
