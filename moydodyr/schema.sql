CREATE TABLE IF NOT EXISTS bot_settings (
    key text PRIMARY KEY,
    value jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id bigint PRIMARY KEY,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    key text PRIMARY KEY,
    state text,
    data jsonb NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS orders (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    draft_id uuid NOT NULL UNIQUE,
    user_id bigint NOT NULL REFERENCES users(id),
    source text NOT NULL,
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'new' CHECK (status IN ('new','working','agreed','cancelled')),
    manager_id bigint,
    external_lead_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS external_lead_id text;
CREATE TABLE IF NOT EXISTS order_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id bigint NOT NULL REFERENCES orders(id),
    manager_id bigint NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dedupe_key text UNIQUE NOT NULL,
    chat_id bigint NOT NULL,
    payload jsonb NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz,
    message_id bigint,
    failed_at timestamptz,
    last_error text
);
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS failed_at timestamptz;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS last_error text;
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(available_at)
    WHERE delivered_at IS NULL AND failed_at IS NULL;
CREATE INDEX IF NOT EXISTS orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS orders_customer_created ON orders(user_id, created_at DESC, id DESC);
ALTER TABLE order_history ALTER COLUMN manager_id DROP NOT NULL;
ALTER TABLE order_history ADD COLUMN IF NOT EXISTS actor_user_id bigint REFERENCES users(id);
CREATE TABLE IF NOT EXISTS photo_files (
    id uuid PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users(id),
    draft_id uuid NOT NULL,
    telegram_file_id text NOT NULL,
    telegram_unique_id text,
    content_type text NOT NULL,
    file_size integer NOT NULL CHECK (file_size > 0 AND file_size <= 20971520),
    sha256 text NOT NULL,
    width integer,
    height integer,
    content bytea NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id, draft_id, telegram_file_id),
    CHECK (octet_length(content) = file_size)
);
CREATE TABLE IF NOT EXISTS order_photos (
    order_id bigint NOT NULL REFERENCES orders(id),
    position integer NOT NULL CHECK (position BETWEEN 0 AND 4),
    telegram_file_id text NOT NULL,
    photo_id uuid REFERENCES photo_files(id),
    PRIMARY KEY (order_id, position)
);
CREATE INDEX IF NOT EXISTS order_photos_unarchived ON order_photos(order_id,position)
WHERE photo_id IS NULL;
-- Recover links for older orders without changing the original order payload.
INSERT INTO order_photos(order_id,position,telegram_file_id)
SELECT o.id, (p.ordinality-1)::integer, p.file_id
FROM orders o CROSS JOIN LATERAL jsonb_array_elements_text(
    CASE WHEN jsonb_typeof(o.payload->'photos')='array' THEN o.payload->'photos' ELSE '[]'::jsonb END
) WITH ORDINALITY AS p(file_id,ordinality)
WHERE p.ordinality <= 5
ON CONFLICT DO NOTHING;
