CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
INSERT INTO schema_version VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS courses (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    channel_id text,
    active boolean NOT NULL DEFAULT true,
    revision integer NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rule_revisions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope text NOT NULL,
    content text NOT NULL,
    author_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS rule_scope ON rule_revisions(scope, id DESC);
CREATE TABLE IF NOT EXISTS books (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    course_id bigint NOT NULL REFERENCES courses(id),
    filename text NOT NULL,
    content text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    active boolean NOT NULL DEFAULT false,
    embedding_model text NOT NULL DEFAULT 'intfloat/multilingual-e5-base',
    items jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_book ON books(course_id) WHERE active;
CREATE TABLE IF NOT EXISTS jobs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('generate','index','publish')),
    course_id bigint NOT NULL REFERENCES courses(id),
    status text NOT NULL DEFAULT 'queued',
    stage text NOT NULL DEFAULT 'Waiting for worker',
    payload jsonb NOT NULL,
    error text,
    progress_channel_id text,
    progress_message_id text,
    notified_status text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS job_queue ON jobs(status,id);
CREATE UNIQUE INDEX IF NOT EXISTS active_generation ON jobs(course_id,(payload->>'video_id'),(payload->>'lecture'))
    WHERE kind='generate' AND status IN ('queued','running');
CREATE UNIQUE INDEX IF NOT EXISTS one_publication ON jobs((payload->>'draft_id')) WHERE kind='publish';
CREATE TABLE IF NOT EXISTS drafts (
    id bigint PRIMARY KEY REFERENCES jobs(id),
    transcript text NOT NULL,
    context text NOT NULL,
    content text NOT NULL,
    revision integer NOT NULL DEFAULT 1,
    published_at timestamptz
);
CREATE TABLE IF NOT EXISTS draft_revisions (
    draft_id bigint NOT NULL REFERENCES drafts(id),
    revision integer NOT NULL,
    content text NOT NULL,
    author_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(draft_id,revision)
);
CREATE TABLE IF NOT EXISTS publication_parts (
    job_id bigint NOT NULL REFERENCES jobs(id),
    part integer NOT NULL,
    content text NOT NULL,
    state text NOT NULL DEFAULT 'pending',
    message_id text,
    PRIMARY KEY(job_id,part)
);
