-- 006_manual_topics.sql
-- Ручные идеи владельца — введённые через /note. Не путать с topics
-- (автоматический скрейп). Связь с topics — через matched_topic_id если
-- /note подхватил похожий уже существующий пост; или created_topic_id
-- если /note создал новый topic сам (score=80, ручной приоритет).

CREATE TABLE IF NOT EXISTS manual_topics (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    note              text NOT NULL,
    matched_topic_id  uuid REFERENCES topics(id) ON DELETE SET NULL,
    created_topic_id  uuid REFERENCES topics(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS manual_topics_created_idx
    ON manual_topics (created_at DESC);
