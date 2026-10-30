-- ScholarTrust schema (SQLite)。设计取舍：
-- 1) 单库多表，不用 PG/Neo4j：部署无 docker/sudo，单进程即可跑；
-- 2) 图用 edges 表 + 递归 CTE，不引图数据库；
-- 3) 全文检索用 FTS5（contentless，rowid 对齐主键），不引 ES；
-- 4) 所有"可信"字段都带 evidence/confidence/verdict，便于事后审计与门槛复核。

PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS people (
    pid            INTEGER PRIMARY KEY,
    openalex_id    TEXT UNIQUE NOT NULL,
    name           TEXT NOT NULL,
    orcid          TEXT,
    works_count    INTEGER DEFAULT 0,
    cited_by_count INTEGER DEFAULT 0,
    h_index        INTEGER,
    last_inst      TEXT,
    country        TEXT,
    topics         TEXT,             -- JSON array
    status         TEXT DEFAULT 'pending',   -- pending|published|rejected
    confidence     REAL,
    evidence       TEXT,             -- JSON: 来源 URL、原始字段
    updated_at     REAL
);

CREATE TABLE IF NOT EXISTS institutions (
    iid         INTEGER PRIMARY KEY,
    openalex_id TEXT UNIQUE,
    name        TEXT NOT NULL,
    country     TEXT,
    type        TEXT
);

CREATE TABLE IF NOT EXISTS works (
    wid         INTEGER PRIMARY KEY,
    openalex_id TEXT UNIQUE,
    doi         TEXT,
    title       TEXT,
    abstract    TEXT,
    year        INTEGER,
    cites       INTEGER DEFAULT 0,
    venue       TEXT,
    type        TEXT
);

CREATE TABLE IF NOT EXISTS work_authors (
    wid      INTEGER NOT NULL,
    pid      INTEGER NOT NULL,
    position INTEGER,
    PRIMARY KEY (wid, pid)
);

-- 合作网络：无向边，a<b 存一次
CREATE TABLE IF NOT EXISTS edges (
    a      INTEGER NOT NULL,
    b      INTEGER NOT NULL,
    kind   TEXT NOT NULL,            -- coauthor
    weight INTEGER DEFAULT 1,
    PRIMARY KEY (a, b, kind)
);

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

-- 可信采集：候选 -> 证据 -> 判定 -> 复核
CREATE TABLE IF NOT EXISTS candidates (
    cid        INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,         -- person|work|edge
    payload    TEXT NOT NULL,         -- JSON
    evidence   TEXT,                  -- JSON: 来源/字段/span
    score      REAL,
    verdict    TEXT,                  -- accept|reject|review
    reasons    TEXT,                  -- JSON array
    created_at REAL
);

CREATE TABLE IF NOT EXISTS review_queue (
    cid        INTEGER PRIMARY KEY,
    priority   REAL,
    decided_by TEXT,
    decision   TEXT,                  -- accept|reject
    note       TEXT,
    decided_at REAL
);

CREATE INDEX IF NOT EXISTS idx_people_name ON people(name);
CREATE INDEX IF NOT EXISTS idx_works_year ON works(year);
CREATE INDEX IF NOT EXISTS idx_work_authors_pid ON work_authors(pid);
CREATE INDEX IF NOT EXISTS idx_edges_a ON edges(a);
CREATE INDEX IF NOT EXISTS idx_edges_b ON edges(b);
CREATE INDEX IF NOT EXISTS idx_candidates_verdict ON candidates(verdict);

-- 全文索引（contentless，rowid 与主键一致）
CREATE VIRTUAL TABLE IF NOT EXISTS people_fts USING fts5(
    name, topics, inst, tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(
    title, abstract, tokenize='unicode61'
);
