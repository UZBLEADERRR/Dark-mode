-- Sarideo — Supabase sxemasi
--
-- Buni qo'lda ishga tushirish SHART EMAS: ilova birinchi ulanganda bu
-- jadvallarni o'zi yaratadi. Bu fayl ikki narsa uchun:
--   1. nima saqlanishini oldindan ko'rish uchun,
--   2. Supabase'ning SQL Editor'ida bir marta bosib tayyorlab qo'yish uchun.
--
-- Ishga tushirish: Supabase → SQL Editor → New query → shuni qo'yib "Run".
-- Ikkinchi marta ishga tushirsangiz ham hech narsa buzilmaydi.

-- Qahramonlar: siz yuklagan surat. Bu qayta yaratib bo'lmaydigan yagona narsa.
-- `voice_id` berilgan qahramon o'z gaplarini o'zi aytadi; bo'sh bo'lsa diktor
-- o'qiydi. `tts_provider` bo'sh bo'lsa loyihaning provayderi ishlatiladi.
CREATE TABLE IF NOT EXISTS heroes (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    mime         TEXT NOT NULL DEFAULT 'image/png',
    ext          TEXT NOT NULL DEFAULT '.png',
    image        BYTEA NOT NULL,
    voice_id     TEXT NOT NULL DEFAULT '',
    tts_provider TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

-- Musiqa va effektlar. `kind`: 'music' — fon, 'sfx' — bir martalik tovush.
CREATE TABLE IF NOT EXISTS music (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'music',
    mime        TEXT NOT NULL DEFAULT 'audio/mpeg',
    ext         TEXT NOT NULL DEFAULT '.mp3',
    audio       BYTEA NOT NULL,
    created_at  TEXT NOT NULL
);

-- Sozlamalar: brend to'plami, tanlangan modellar va ovozlar. Har biri JSON.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Sahna ustiga qo'yiladigan hamma narsa: stikerlar, logotip, kesib olingan
-- aktyorlar va o'zingiz yozgan ovoz dubllari.
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    mime        TEXT NOT NULL DEFAULT 'image/png',
    ext         TEXT NOT NULL DEFAULT '.png',
    data        BYTEA NOT NULL,
    created_at  TEXT NOT NULL
);

-- Loyihalar. `result` ichida ssenariy, sahnalar, ovoz uzunliklari va tayyor
-- video havolasi turadi — ya'ni yarim tayyor loyihani keyin davom ettirish
-- uchun kerak bo'ladigan hamma narsa.
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    step         TEXT NOT NULL DEFAULT '',
    progress     INTEGER NOT NULL DEFAULT 0,
    request      TEXT NOT NULL,
    result       TEXT NOT NULL DEFAULT '{}',
    logs         TEXT NOT NULL DEFAULT '[]',
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);

-- Render natijalari: sahna rasmlari va ovoz bo'laklari. Storage ulangan bo'lsa
-- ular bucket'ga chiqadi va bu jadval bo'sh turadi. Storage ulanmagan bo'lsa —
-- shu yerda saqlanadi, ya'ni faqat DATABASE_URL bilan ham hech narsa yo'qolmaydi.
CREATE TABLE IF NOT EXISTS media (
    path       TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    data       BYTEA NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS media_job_idx ON media (job_id);

-- O'z kanallaringiz: Instagram/YouTube/TikTok profilingiz skrinshoti. `summary`
-- — AI skrinshotdan nimani o'qib olgani. U bir marta, yuklaganda o'qiladi va shu
-- yerda qoladi; keyingi har bir suhbat rasm emas, shu matnni ko'radi — ya'ni
-- kanalga qarash bir marta to'lanadi, har savolda emas.
CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,
    handle     TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL DEFAULT 'image/png',
    ext        TEXT NOT NULL DEFAULT '.png',
    image      BYTEA NOT NULL,
    created_at TEXT NOT NULL
);

-- Bu jadvallarga faqat serverning o'zi (service key bilan) kiradi, brauzerdan
-- emas. RLS yoqilmagan — anon key bu jadvallarni umuman ko'rmaydi.

-- Baza avval yaratilgan bo'lsa, ustunlar shu yerda qo'shiladi. Ilova buni
-- o'zi ham qiladi — bu faqat qo'lda ishga tushirganlar uchun.
ALTER TABLE heroes ADD COLUMN IF NOT EXISTS voice_id     TEXT NOT NULL DEFAULT '';
ALTER TABLE heroes ADD COLUMN IF NOT EXISTS tts_provider TEXT NOT NULL DEFAULT '';
ALTER TABLE music  ADD COLUMN IF NOT EXISTS kind         TEXT NOT NULL DEFAULT 'music';
