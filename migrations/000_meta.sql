-- 000_meta.sql
-- Журнал применённых миграций. Применяется первым самим runner'ом.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
