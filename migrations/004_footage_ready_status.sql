-- 004_footage_ready_status.sql (E8.1)
-- Промежуточный статус между scripted и voiced для AI-формата
-- (footage_generator успел сгенерировать клипы) и passthrough для long_gaming.
-- Идемпотентно через IF NOT EXISTS — поддерживается PG ≥12.

ALTER TYPE video_status ADD VALUE IF NOT EXISTS 'footage_ready' BEFORE 'voiced';
