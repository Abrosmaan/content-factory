-- 005_costs.sql
-- Учёт расходов на внешние API. Каждый воркер пишет сюда после вызова
-- (record-after-success, не до). /costs читает агрегаты, watchdog следит за
-- дневным порогом.

CREATE TABLE IF NOT EXISTS costs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    service     text NOT NULL,        -- 'anthropic' | 'elevenlabs_tts' | 'elevenlabs_music' | 'creatomate' | 'fal_kling' | 'fal_flux'
    amount_usd  numeric(12, 6) NOT NULL,
    units       text,                 -- 'tokens_in+out' | 'chars' | 'tracks' | 'sec' | 'clips' | 'images'
    quantity    numeric,              -- raw count (для проверок и breakdown)
    worker      text,                 -- 'topic_strategist' | 'scriptwriter' | ...
    video_id    uuid REFERENCES videos(id) ON DELETE SET NULL,
    job_id      text,                 -- external API id (render_id, request_id)
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS costs_service_created_idx ON costs (service, created_at DESC);
CREATE INDEX IF NOT EXISTS costs_created_idx          ON costs (created_at DESC);
