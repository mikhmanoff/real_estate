-- ============================================
-- Схема БД для сервиса аренды недвижимости
-- PostgreSQL 15+
-- ============================================

-- Расширения
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";      -- для fuzzy search по тексту

-- ============================================
-- 1. КАНАЛЫ / ИСТОЧНИКИ
-- ============================================
CREATE TABLE channels (
    id              BIGSERIAL PRIMARY KEY,
    telegram_id     BIGINT UNIQUE NOT NULL,         -- chat_id из Telegram (-100xxx)
    username        VARCHAR(255),                    -- @username или NULL
    title           VARCHAR(500),
    chat_type       VARCHAR(50) DEFAULT 'channel',   -- channel, supergroup, group
    invite_link     TEXT,                            -- для приватных каналов
    is_active       BOOLEAN DEFAULT TRUE,
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    last_message_at TIMESTAMPTZ
);

CREATE INDEX idx_channels_telegram_id ON channels(telegram_id);
CREATE INDEX idx_channels_active ON channels(is_active) WHERE is_active = TRUE;

-- ============================================
-- 2. ПОСТЫ (сырые сообщения)
-- ============================================
CREATE TABLE posts (
    id              BIGSERIAL PRIMARY KEY,
    post_uid        VARCHAR(100) UNIQUE NOT NULL,   -- "msg:chat_id:msg_id" или "album:..."
    
    -- Telegram IDs
    channel_id      BIGINT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    message_id      BIGINT NOT NULL,
    grouped_id      BIGINT,                          -- для альбомов
    
    -- Контент
    text_raw        TEXT,
    text_len        INT DEFAULT 0,
    
    -- Извлечённые данные
    phones          TEXT[],                          -- массив телефонов
    links           TEXT[],
    hashtags        TEXT[],
    mentions        TEXT[],                          -- @usernames из текста
    
    -- Временные метки
    published_at    TIMESTAMPTZ NOT NULL,            -- date_utc из Telegram
    fetched_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    
    -- Статус
    is_deleted      BOOLEAN DEFAULT FALSE,
    deleted_at      TIMESTAMPTZ,
    
    -- Дедупликация
    text_hash       VARCHAR(64),                     -- SHA256 от нормализованного текста
    fingerprint     VARCHAR(64),                     -- для поиска похожих объявлений
    duplicate_of    BIGINT REFERENCES posts(id),     -- ссылка на оригинал если дубль
    
    UNIQUE(channel_id, message_id)
);

CREATE INDEX idx_posts_channel ON posts(channel_id);
CREATE INDEX idx_posts_published ON posts(published_at DESC);
CREATE INDEX idx_posts_not_deleted ON posts(is_deleted) WHERE is_deleted = FALSE;
CREATE INDEX idx_posts_text_hash ON posts(text_hash);
CREATE INDEX idx_posts_fingerprint ON posts(fingerprint);
CREATE INDEX idx_posts_phones ON posts USING GIN(phones);
CREATE INDEX idx_posts_hashtags ON posts USING GIN(hashtags);

-- Полнотекстовый поиск
CREATE INDEX idx_posts_text_search ON posts USING GIN(to_tsvector('russian', text_raw));

-- ============================================
-- 3. МЕДИАФАЙЛЫ
-- ============================================
CREATE TABLE media (
    id              BIGSERIAL PRIMARY KEY,
    post_id         BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    
    message_id      BIGINT NOT NULL,                 -- ID сообщения с этим медиа
    media_type      VARCHAR(50) NOT NULL,            -- image, video, document, voice, audio
    
    -- Пути к файлам
    local_path      TEXT,                            -- путь на диске
    remote_url      TEXT,                            -- если загрузим в S3/CDN
    
    -- Метаданные
    file_size       BIGINT,
    mime_type       VARCHAR(100),
    width           INT,
    height          INT,
    duration_sec    INT,                             -- для видео/аудио
    
    -- Для дедупликации изображений
    phash           VARCHAR(64),                     -- perceptual hash
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(post_id, message_id)
);

CREATE INDEX idx_media_post ON media(post_id);
CREATE INDEX idx_media_type ON media(media_type);
CREATE INDEX idx_media_phash ON media(phash);

-- ============================================
-- 4. РАСПАРСЕННЫЕ ОБЪЯВЛЕНИЯ НЕДВИЖИМОСТИ
-- ============================================
CREATE TABLE listings (
    id              BIGSERIAL PRIMARY KEY,
    post_id         BIGINT UNIQUE NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    
    -- Классификация
    is_real_estate  BOOLEAN DEFAULT TRUE,
    deal_type       VARCHAR(50),                     -- rent_long, rent_daily, sale, wanted_rent, wanted_buy
    object_type     VARCHAR(50),                     -- flat, room, house, land, office
    
    -- Параметры объекта
    rooms           SMALLINT,
    rooms_options   SMALLINT[],                      -- если указано несколько вариантов
    floor           SMALLINT,
    total_floors    SMALLINT,
    area_m2         DECIMAL(10,2),
    
    -- Цена
    price           DECIMAL(15,2),
    currency        VARCHAR(10),                     -- usd, uzs
    price_period    VARCHAR(20),                     -- month, day, total
    deposit         DECIMAL(15,2),
    has_commission  BOOLEAN DEFAULT FALSE,
    
    -- Локация (сырые данные)
    district_raw    VARCHAR(255),
    metro_raw       VARCHAR(255),
    address_raw     TEXT,
    
    -- Нормализованная локация (для фильтров)
    district_id     INT,                             -- FK на справочник районов
    metro_id        INT,                             -- FK на справочник метро
    latitude        DECIMAL(10,7),
    longitude       DECIMAL(10,7),
    
    -- Удобства (можно расширять)
    has_furniture   BOOLEAN,
    has_appliances  BOOLEAN,
    has_internet    BOOLEAN,
    has_parking     BOOLEAN,
    has_conditioner BOOLEAN,
    
    -- Контакт
    contact_phone   VARCHAR(50),                     -- основной телефон
    contact_name    VARCHAR(255),
    contact_tg      VARCHAR(100),                    -- @username контакта
    is_agent        BOOLEAN,                         -- риелтор или собственник
    
    -- Качество парсинга
    parse_score     SMALLINT DEFAULT 0,              -- 0-100, насколько полно распарсили
    needs_review    BOOLEAN DEFAULT FALSE,
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_listings_deal_type ON listings(deal_type);
CREATE INDEX idx_listings_object_type ON listings(object_type);
CREATE INDEX idx_listings_rooms ON listings(rooms);
CREATE INDEX idx_listings_price ON listings(price, currency);
CREATE INDEX idx_listings_district ON listings(district_id);
CREATE INDEX idx_listings_location ON listings(latitude, longitude);

-- Составной индекс для типичных фильтров
CREATE INDEX idx_listings_filter ON listings(deal_type, object_type, rooms, price) 
    WHERE is_real_estate = TRUE;

-- ============================================
-- 5. СПРАВОЧНИКИ
-- ============================================

-- Районы
CREATE TABLE districts (
    id              SERIAL PRIMARY KEY,
    name_ru         VARCHAR(255) NOT NULL,
    name_uz         VARCHAR(255),
    city            VARCHAR(100) DEFAULT 'Ташкент',
    aliases         TEXT[],                          -- альтернативные написания
    center_lat      DECIMAL(10,7),
    center_lng      DECIMAL(10,7)
);

-- Метро
CREATE TABLE metro_stations (
    id              SERIAL PRIMARY KEY,
    name_ru         VARCHAR(255) NOT NULL,
    name_uz         VARCHAR(255),
    line_name       VARCHAR(100),
    line_color      VARCHAR(20),
    latitude        DECIMAL(10,7),
    longitude       DECIMAL(10,7),
    aliases         TEXT[]
);

-- ============================================
-- 6. ИСТОРИЯ УДАЛЕНИЙ (для аналитики)
-- ============================================
CREATE TABLE deletion_log (
    id              BIGSERIAL PRIMARY KEY,
    post_id         BIGINT REFERENCES posts(id),
    channel_id      BIGINT,
    message_id      BIGINT,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    
    -- Сколько прожило объявление
    lifetime_hours  INT
);

CREATE INDEX idx_deletion_log_detected ON deletion_log(detected_at DESC);

-- ============================================
-- 7. ДУБЛИКАТЫ (связи между похожими постами)
-- ============================================
CREATE TABLE duplicates (
    id              BIGSERIAL PRIMARY KEY,
    original_id     BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    duplicate_id    BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    similarity      DECIMAL(5,4),                    -- 0.0000 - 1.0000
    match_type      VARCHAR(50),                     -- text_exact, text_similar, phone, image
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(original_id, duplicate_id)
);

CREATE INDEX idx_duplicates_original ON duplicates(original_id);
CREATE INDEX idx_duplicates_duplicate ON duplicates(duplicate_id);

-- ============================================
-- 8. ФУНКЦИИ И ТРИГГЕРЫ
-- ============================================

-- Автообновление updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_posts_updated
    BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER tr_listings_updated
    BEFORE UPDATE ON listings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Обновление last_message_at в channels
CREATE OR REPLACE FUNCTION update_channel_last_message()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE channels 
    SET last_message_at = NEW.published_at 
    WHERE id = NEW.channel_id 
      AND (last_message_at IS NULL OR last_message_at < NEW.published_at);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_posts_channel_activity
    AFTER INSERT ON posts
    FOR EACH ROW EXECUTE FUNCTION update_channel_last_message();

-- Логирование удалений
CREATE OR REPLACE FUNCTION log_post_deletion()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_deleted = TRUE AND OLD.is_deleted = FALSE THEN
        NEW.deleted_at = NOW();
        INSERT INTO deletion_log (post_id, channel_id, message_id, lifetime_hours)
        VALUES (
            OLD.id,
            OLD.channel_id,
            OLD.message_id,
            EXTRACT(EPOCH FROM (NOW() - OLD.published_at)) / 3600
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tr_posts_deletion_log
    BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION log_post_deletion();

-- ============================================
-- 9. НАЧАЛЬНЫЕ ДАННЫЕ: РАЙОНЫ ТАШКЕНТА
-- ============================================
INSERT INTO districts (name_ru, name_uz, aliases) VALUES
    ('Алмазарский район', 'Olmazor tumani', ARRAY['Алмазар', 'Olmazor']),
    ('Бектемирский район', 'Bektemir tumani', ARRAY['Бектемир', 'Bektemir']),
    ('Мирабадский район', 'Mirobod tumani', ARRAY['Мирабад', 'Mirobod']),
    ('Мирзо-Улугбекский район', 'Mirzo Ulugbek tumani', ARRAY['Мирзо-Улугбек', 'Mirzo Ulugbek']),
    ('Сергелийский район', 'Sergeli tumani', ARRAY['Сергели', 'Sergeli']),
    ('Учтепинский район', 'Uchtepa tumani', ARRAY['Учтепа', 'Uchtepa']),
    ('Чиланзарский район', 'Chilonzor tumani', ARRAY['Чиланзар', 'Chilonzor']),
    ('Шайхантаурский район', 'Shayxontohur tumani', ARRAY['Шайхантаур', 'Shayxontohur']),
    ('Юнусабадский район', 'Yunusobod tumani', ARRAY['Юнусабад', 'Yunusobod']),
    ('Яккасарайский район', 'Yakkasaroy tumani', ARRAY['Яккасарай', 'Yakkasaroy']),
    ('Яшнабадский район', 'Yashnobod tumani', ARRAY['Яшнабад', 'Yashnobod']);

-- ============================================
-- 10. НАЧАЛЬНЫЕ ДАННЫЕ: МЕТРО ТАШКЕНТА
-- ============================================
INSERT INTO metro_stations (name_ru, name_uz, line_name, line_color, aliases) VALUES
    -- Узбекистанская линия (красная)
    ('Буюк Ипак Йули', 'Buyuk Ipak Yoli', 'Узбекистанская', 'red', ARRAY['Максим Горький']),
    ('Пушкин', 'Pushkin', 'Узбекистанская', 'red', ARRAY[]::TEXT[]),
    ('Хамид Олимжон', 'Hamid Olimjon', 'Узбекистанская', 'red', ARRAY['Хамид Алимджан']),
    ('Ойбек', 'Oybek', 'Узбекистанская', 'red', ARRAY[]::TEXT[]),
    ('Мустакиллик Майдони', 'Mustaqillik Maydoni', 'Узбекистанская', 'red', ARRAY['Площадь Независимости']),
    ('Амир Темур Хиёбони', 'Amir Temur Xiyoboni', 'Узбекистанская', 'red', ARRAY['Сквер Амира Темура']),
    ('Юнус Ражабий', 'Yunus Rajabiy', 'Узбекистанская', 'red', ARRAY[]::TEXT[]),
    ('Минор', 'Minor', 'Узбекистанская', 'red', ARRAY[]::TEXT[]),
    
    -- Чиланзарская линия (синяя)
    ('Олмазор', 'Olmazor', 'Чиланзарская', 'blue', ARRAY['Алмазар']),
    ('Чиланзар', 'Chilonzor', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    ('Мирзо Улугбек', 'Mirzo Ulugbek', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    ('Новза', 'Novza', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    ('Миллий Бог', 'Milliy Bog', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    ('Пахтакор', 'Paxtakor', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    ('Гофур Гулом', 'Gafur Gulom', 'Чиланзарская', 'blue', ARRAY[]::TEXT[]),
    
    -- Юнусабадская линия (зелёная)
    ('Юнусабад', 'Yunusobod', 'Юнусабадская', 'green', ARRAY[]::TEXT[]),
    ('Шахристан', 'Shahriston', 'Юнусабадская', 'green', ARRAY[]::TEXT[]),
    ('Туркистон', 'Turkiston', 'Юнусабадская', 'green', ARRAY[]::TEXT[]),
    ('Бодомзор', 'Bodomzor', 'Юнусабадская', 'green', ARRAY['Бадамзар']),
    ('Абдулла Кодирий', 'Abdulla Qodiriy', 'Юнусабадская', 'green', ARRAY[]::TEXT[]);

-- ============================================
-- ПОЛЕЗНЫЕ VIEW
-- ============================================

-- Активные объявления с основными полями
CREATE VIEW v_active_listings AS
SELECT 
    p.id AS post_id,
    p.post_uid,
    c.title AS channel_title,
    c.username AS channel_username,
    l.deal_type,
    l.object_type,
    l.rooms,
    l.floor,
    l.total_floors,
    l.area_m2,
    l.price,
    l.currency,
    l.price_period,
    l.deposit,
    l.district_raw,
    l.metro_raw,
    p.phones,
    p.published_at,
    p.text_raw,
    (SELECT local_path FROM media m WHERE m.post_id = p.id LIMIT 1) AS first_image
FROM posts p
JOIN channels c ON c.id = p.channel_id
JOIN listings l ON l.post_id = p.id
WHERE p.is_deleted = FALSE 
  AND l.is_real_estate = TRUE
  AND p.duplicate_of IS NULL
ORDER BY p.published_at DESC;

-- Статистика по каналам
CREATE VIEW v_channel_stats AS
SELECT 
    c.id,
    c.title,
    c.username,
    COUNT(p.id) AS total_posts,
    COUNT(p.id) FILTER (WHERE NOT p.is_deleted) AS active_posts,
    COUNT(l.id) FILTER (WHERE l.is_real_estate) AS real_estate_posts,
    MAX(p.published_at) AS last_post_at
FROM channels c
LEFT JOIN posts p ON p.channel_id = c.id
LEFT JOIN listings l ON l.post_id = p.id
GROUP BY c.id;