-- 002_settings.sql
-- Конфиг-таблица: pause-флаг, API-аудит, счётчики токенов, seed format_weights и пр.
-- Идемпотентна.

CREATE TABLE IF NOT EXISTS settings (
  key text PRIMARY KEY,
  value jsonb NOT NULL,
  updated_at timestamptz DEFAULT now()
);

INSERT INTO settings (key, value) VALUES
  ('pipeline_paused',       'false'::jsonb),
  ('api_audit_passed',      'false'::jsonb),
  ('threads_enabled',       'false'::jsonb),
  ('owner_last_seen_at',    'null'::jsonb),
  ('tokens_claude_in',      '0'::jsonb),
  ('tokens_claude_out',     '0'::jsonb),
  ('elevenlabs_chars_used', '0'::jsonb),
  ('creatomate_minutes_used','0'::jsonb)
ON CONFLICT (key) DO NOTHING;
