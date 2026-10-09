"""塊與向量的存放層:Postgres + pgvector 的 `retrieval.chunks` 表。

一列一塊,欄位對齊 chunking.py 的輸出,再加上向量與它是怎麼算出來的。
附帶欄位(namespace、count、first_seen、last_seen、extra)只存不進 embedding(SPEC §8.3、§8.4)。
`text` 永遠是塊的原文;向量若是截短後算的,`sent_chars` 會小於 `length(text)`。

續跑:`done()` 回傳已存的 {id: 原文 md5}。原文 md5 對不上的塊(切法改過)不算做完,
會被重算並覆寫,避免舊向量配新文字。
"""

import hashlib
import json

import psycopg

from config import settings

SCHEMA = "retrieval"

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.chunks (
    id          text PRIMARY KEY,          -- 例:Scenario-1/events/000001
    scenario    text NOT NULL,
    source      text NOT NULL,
    namespace   text NOT NULL,
    key         jsonb NOT NULL,
    count       int,
    first_seen  text NOT NULL,
    last_seen   text NOT NULL,
    extra       jsonb NOT NULL,
    text        text NOT NULL,             -- 原文(SPEC §8.4 的「原句」),INV-2 比對以它為準
    text_md5    text NOT NULL,
    sent_chars  int  NOT NULL,             -- 送進 embedding 的字元數;< length(text) = 截短過
    embedding   vector(1024) NOT NULL,
    model       text NOT NULL,
    provider    text NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);
"""


def md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def connect() -> psycopg.Connection:
    conn = psycopg.connect(settings.postgres_dsn, autocommit=True)
    conn.execute(DDL)
    return conn


def done(conn: psycopg.Connection, source: str) -> dict[str, str]:
    rows = conn.execute(f"SELECT id, text_md5 FROM {SCHEMA}.chunks WHERE source = %s", (source,))
    return dict(rows.fetchall())


def upsert(
    conn: psycopg.Connection, chunks: list[dict], vectors: list, sent_chars: list[int]
) -> None:
    with conn.transaction():
        for c, v, n in zip(chunks, vectors, sent_chars, strict=True):
            conn.execute(
                f"""
                INSERT INTO {SCHEMA}.chunks
                    (id, scenario, source, namespace, key, count, first_seen, last_seen, extra,
                     text, text_md5, sent_chars, embedding, model, provider)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    scenario = EXCLUDED.scenario, source = EXCLUDED.source,
                    namespace = EXCLUDED.namespace, key = EXCLUDED.key, count = EXCLUDED.count,
                    first_seen = EXCLUDED.first_seen, last_seen = EXCLUDED.last_seen,
                    extra = EXCLUDED.extra, text = EXCLUDED.text, text_md5 = EXCLUDED.text_md5,
                    sent_chars = EXCLUDED.sent_chars, embedding = EXCLUDED.embedding,
                    model = EXCLUDED.model, provider = EXCLUDED.provider, embedded_at = now()
                """,
                (
                    c["id"],
                    c["scenario"],
                    c["source"],
                    c["namespace"],
                    json.dumps(c["key"], ensure_ascii=False),
                    c["count"],
                    c["first_seen"],
                    c["last_seen"],
                    json.dumps(c["extra"], ensure_ascii=False),
                    c["text"],
                    md5(c["text"]),
                    n,
                    "[" + ",".join(map(str, v)) + "]",
                    settings.openrouter_embedding_model,
                    settings.openrouter_embedding_provider,
                ),
            )
