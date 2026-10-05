-- 001_initial_schema.sql
-- Базовая схема пайплайна: enums + основные таблицы из раздела 5 ТЗ.
-- distributions намеренно вынесена в эпик 6.
-- Идемпотентна: повторный прогон не падает.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
  CREATE TYPE video_status AS ENUM (
    'raw',
    'scored',
    'pending_approval',
    'approved',
    'scripted',
    'voiced',
    'assembled',
    'packaged',
    'ready_for_review',
    'publish_queued',
    'published',
    'analyzed',
    'rejected',
    'failed'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE video_format AS ENUM ('short', 'long_gaming', 'long_setup', 'bridge');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS topics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source text NOT NULL,
  title text NOT NULL,
  url text,
  raw_payload jsonb,
  score numeric,
  score_reasoning text,
  suggested_format video_format,
  dedup_hash text UNIQUE,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS topics_created_at_idx ON topics (created_at DESC);
CREATE INDEX IF NOT EXISTS topics_score_null_idx ON topics (created_at DESC) WHERE score IS NULL;

CREATE TABLE IF NOT EXISTS videos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id uuid REFERENCES topics(id),
  format video_format NOT NULL,
  status video_status NOT NULL DEFAULT 'raw',
  title text,
  script jsonb,
  voice_r2_key text,
  render_r2_key text,
  thumbnail_r2_key text,
  seo jsonb,
  youtube_video_id text,
  scheduled_publish_at timestamptz,
  status_updated_at timestamptz DEFAULT now(),
  attempts int DEFAULT 0,
  last_error text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS videos_status_idx ON videos (status, status_updated_at);
CREATE INDEX IF NOT EXISTS videos_scheduled_idx ON videos (scheduled_publish_at)
  WHERE scheduled_publish_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS status_log (
  id bigserial PRIMARY KEY,
  video_id uuid REFERENCES videos(id),
  from_status video_status,
  to_status video_status,
  actor text NOT NULL,
  detail text,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS status_log_video_idx ON status_log (video_id, created_at DESC);

CREATE TABLE IF NOT EXISTS footage (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  r2_key text NOT NULL UNIQUE,
  game text NOT NULL,
  tags text[] NOT NULL,
  duration_sec numeric NOT NULL,
  resolution text NOT NULL,
  used_count int DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS footage_game_idx ON footage (game);
CREATE INDEX IF NOT EXISTS footage_tags_idx ON footage USING gin (tags);

CREATE TABLE IF NOT EXISTS products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  category text NOT NULL,
  affiliate_url text NOT NULL,
  program text NOT NULL,
  commission_pct numeric,
  price_usd numeric,
  active boolean DEFAULT true,
  last_link_check timestamptz,
  link_ok boolean DEFAULT true
);

CREATE INDEX IF NOT EXISTS products_active_idx ON products (active, category) WHERE active;
CREATE UNIQUE INDEX IF NOT EXISTS products_url_unique ON products (affiliate_url);

CREATE TABLE IF NOT EXISTS analytics (
  id bigserial PRIMARY KEY,
  video_id uuid REFERENCES videos(id),
  snapshot_at timestamptz DEFAULT now(),
  views bigint,
  impressions bigint,
  ctr numeric,
  avg_view_duration_sec numeric,
  retention_pct numeric,
  subs_gained int,
  estimated_revenue numeric
);

CREATE INDEX IF NOT EXISTS analytics_video_idx ON analytics (video_id, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS format_weights (
  format video_format PRIMARY KEY,
  weight numeric NOT NULL DEFAULT 1.0,
  killed boolean DEFAULT false,
  updated_at timestamptz DEFAULT now()
);

INSERT INTO format_weights (format, weight)
  VALUES
    ('short',       1.0),
    ('long_gaming', 1.0),
    ('long_setup',  1.0),
    ('bridge',      1.0)
  ON CONFLICT (format) DO NOTHING;

CREATE TABLE IF NOT EXISTS errors (
  id bigserial PRIMARY KEY,
  worker text NOT NULL,
  video_id uuid,
  error text NOT NULL,
  payload jsonb,
  resolved boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS errors_unresolved_idx ON errors (created_at DESC) WHERE NOT resolved;
