-- Инициализация БД при первом старте контейнера postgis/postgis.
-- Расширения, доступные в образе; для доменных схем используйте Alembic.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
