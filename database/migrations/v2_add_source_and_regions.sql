-- ============================================================
-- Миграция v2: добавляем source/source_id + таблица regions
-- Применять ОДИН раз перед деплоем OLX-скрапера
-- ============================================================
-- Безопасно для существующих данных joymi — только ADD COLUMN IF NOT EXISTS
-- ============================================================

BEGIN;

-- ============================================================
-- 1. competitor_listings: новые колонки
-- ============================================================

ALTER TABLE competitor_listings
    ADD COLUMN IF NOT EXISTS source              VARCHAR(20) NOT NULL DEFAULT 'joymi',
    ADD COLUMN IF NOT EXISTS source_id           VARCHAR(64),
    ADD COLUMN IF NOT EXISTS url                 TEXT,
    ADD COLUMN IF NOT EXISTS contact_phone       VARCHAR(50),
    ADD COLUMN IF NOT EXISTS contact_phones_extra TEXT[],
    ADD COLUMN IF NOT EXISTS phone_revealed_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS detail_fetched_at   TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS metadata            JSONB;

-- ============================================================
-- 2. Backfill joymi-строк
-- ============================================================

UPDATE competitor_listings
SET
    source    = 'joymi',
    source_id = id::TEXT
WHERE source_id IS NULL;

-- ============================================================
-- 3. UNIQUE constraint (source, source_id)
-- ============================================================

-- Делаем source_id NOT NULL после backfill
ALTER TABLE competitor_listings
    ALTER COLUMN source_id SET NOT NULL;

-- Уникальный индекс
CREATE UNIQUE INDEX IF NOT EXISTS uq_competitor_listings_source_source_id
    ON competitor_listings (source, source_id);

-- ============================================================
-- 4. Сиквенс для OLX-строк (ID начинается с 1 млрд,
--    чтобы не пересекался с joymi-ID)
-- ============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_sequences WHERE schemaname = 'public' AND sequencename = 'competitor_listings_olx_seq'
    ) THEN
        CREATE SEQUENCE competitor_listings_olx_seq START 1000000000;
    END IF;
END
$$;

-- ============================================================
-- 5. competitor_scrape_runs: добавляем source
-- ============================================================

ALTER TABLE competitor_scrape_runs
    ADD COLUMN IF NOT EXISTS source VARCHAR(20);

-- Backfill существующих run'ов
UPDATE competitor_scrape_runs SET source = 'joymi' WHERE source IS NULL;

-- ============================================================
-- 6. Таблица регионов Узбекистана
-- ============================================================

CREATE TABLE IF NOT EXISTS regions (
    id       SERIAL PRIMARY KEY,
    name_ru  VARCHAR(100) UNIQUE NOT NULL,
    name_uz  VARCHAR(100),
    aliases  TEXT[]
);

INSERT INTO regions (name_ru, name_uz, aliases) VALUES
    ('Ташкент (город)',         'Toshkent shahri',     ARRAY['toshkent shahri', 'ташкент город']),
    ('Ташкентская область',     'Toshkent viloyati',   ARRAY['toshkent viloyati']),
    ('Самаркандская область',   'Samarqand viloyati',  ARRAY['samarqand viloyati', 'samarkand']),
    ('Бухарская область',       'Buxoro viloyati',     ARRAY['buxoro viloyati', 'bukhara']),
    ('Андижанская область',     'Andijon viloyati',    ARRAY['andijon viloyati']),
    ('Наманганская область',    'Namangan viloyati',   ARRAY['namangan viloyati']),
    ('Ферганская область',      'Farg''ona viloyati',  ARRAY['fargona viloyati', 'fergana']),
    ('Кашкадарьинская область', 'Qashqadaryo viloyati',ARRAY['qashqadaryo']),
    ('Сурхандарьинская область','Surxondaryo viloyati', ARRAY['surxondaryo']),
    ('Джизакская область',      'Jizzax viloyati',     ARRAY['jizzax']),
    ('Сырдарьинская область',   'Sirdaryo viloyati',   ARRAY['sirdaryo']),
    ('Хорезмская область',      'Xorazm viloyati',     ARRAY['xorazm', 'khorezm']),
    ('Навоийская область',      'Navoiy viloyati',     ARRAY['navoiy']),
    ('Каракалпакстан',          'Qoraqalpog''iston',   ARRAY['qoraqalpogiston', 'karakalpakstan'])
ON CONFLICT (name_ru) DO NOTHING;

-- ============================================================
-- 7. Индекс на source для быстрой фильтрации
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_competitor_listings_source
    ON competitor_listings (source);

CREATE INDEX IF NOT EXISTS idx_competitor_scrape_runs_source
    ON competitor_scrape_runs (source);

COMMIT;

-- ============================================================
-- Проверочные запросы (выполни после COMMIT):
-- ============================================================
-- \d competitor_listings                          -- колонки на месте
-- SELECT source, COUNT(*) FROM competitor_listings GROUP BY source;
--   → joymi | <твоё число>   (olx пока 0)
-- SELECT COUNT(*) FROM competitor_listings WHERE source='joymi' AND source_id IS NULL;
--   → должно быть 0
-- SELECT name_ru FROM regions ORDER BY id;
--   → 14 регионов