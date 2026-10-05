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
