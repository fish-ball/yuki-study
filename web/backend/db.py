"""SQLite 连接与建表。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(__file__).resolve().parents[1] / "data" / "study.db"
PRACTICE_DIR = ROOT / "practice"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRACTICE_DIR.mkdir(parents=True, exist_ok=True)
    (PRACTICE_DIR / "papers").mkdir(parents=True, exist_ok=True)
    (PRACTICE_DIR / "attempts").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subjects (
  id TEXT PRIMARY KEY,
  name_zh TEXT NOT NULL,
  phase INTEGER NOT NULL DEFAULT 1,
  paper_full INTEGER,
  admit_score INTEGER,
  sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knowledge_nodes (
  id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  name TEXT NOT NULL,
  parent_id TEXT,
  exam_weight TEXT NOT NULL,
  prerequisites_json TEXT NOT NULL DEFAULT '[]',
  status_default TEXT NOT NULL DEFAULT 'unlearned',
  sort_index INTEGER NOT NULL DEFAULT 0,
  children_json TEXT NOT NULL DEFAULT '[]',
  FOREIGN KEY (subject_id) REFERENCES subjects(id),
  FOREIGN KEY (parent_id) REFERENCES knowledge_nodes(id)
);

CREATE TABLE IF NOT EXISTS mastery_items (
  knowledge_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  level TEXT NOT NULL DEFAULT 'L0',
  last_assessed TEXT,
  wrong_count INTEGER NOT NULL DEFAULT 0,
  notes TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (knowledge_id) REFERENCES knowledge_nodes(id),
  FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS assessments (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  subject_id TEXT,
  theme TEXT,
  date TEXT,
  minutes INTEGER,
  target_level TEXT,
  status TEXT NOT NULL DEFAULT 'ready',
  content_md TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS assessment_knowledge (
  assessment_id TEXT NOT NULL,
  knowledge_id TEXT NOT NULL,
  PRIMARY KEY (assessment_id, knowledge_id),
  FOREIGN KEY (assessment_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plans (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  content_md TEXT NOT NULL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data_json TEXT NOT NULL
);

-- 结构化题目（永久保存在 SQLite，并镜像到 practice/papers）
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  paper_id TEXT NOT NULL,
  subject_id TEXT,
  qtype TEXT NOT NULL,
  stem TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  explanation TEXT NOT NULL DEFAULT '',
  answer_key TEXT NOT NULL DEFAULT '',
  answer_accept TEXT NOT NULL DEFAULT '[]',
  auto_gradable INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'markdown',
  created_at TEXT,
  updated_at TEXT,
  FOREIGN KEY (paper_id) REFERENCES assessments(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_options (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id TEXT NOT NULL,
  label TEXT NOT NULL,
  content TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_knowledge (
  question_id TEXT NOT NULL,
  knowledge_id TEXT NOT NULL,
  PRIMARY KEY (question_id, knowledge_id),
  FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

-- 做题会话与尝试记录（同步仓库时绝不清空）
CREATE TABLE IF NOT EXISTS practice_sessions (
  id TEXT PRIMARY KEY,
  paper_id TEXT,
  mode TEXT NOT NULL DEFAULT 'paper',
  subject_id TEXT,
  knowledge_id TEXT,
  status TEXT NOT NULL DEFAULT 'in_progress',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  total_questions INTEGER NOT NULL DEFAULT 0,
  correct_count INTEGER NOT NULL DEFAULT 0,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  current_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempt_records (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  question_id TEXT NOT NULL,
  paper_id TEXT,
  user_answer TEXT NOT NULL,
  is_correct INTEGER,
  feedback TEXT NOT NULL DEFAULT '',
  attempt_no INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  elapsed_ms INTEGER,
  FOREIGN KEY (session_id) REFERENCES practice_sessions(id),
  FOREIGN KEY (question_id) REFERENCES questions(id)
);

CREATE INDEX IF NOT EXISTS idx_questions_paper ON questions(paper_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempt_records(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempt_records(question_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON practice_sessions(started_at DESC);

-- 知识点教程（文件镜像；同步不清空）
CREATE TABLE IF NOT EXISTS tutorials (
  knowledge_id TEXT PRIMARY KEY,
  subject_id TEXT NOT NULL,
  title TEXT NOT NULL,
  content_md TEXT NOT NULL,
  target_level TEXT,
  source TEXT NOT NULL DEFAULT 'file',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT,
  path TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (knowledge_id) REFERENCES knowledge_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_tutorials_subject ON tutorials(subject_id);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """已有库补列：CREATE IF NOT EXISTS 不会自动加新字段。"""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {r[1] for r in rows}
    if column not in names:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_column(
        conn, "knowledge_nodes", "sort_index", "sort_index INTEGER NOT NULL DEFAULT 0"
    )
    _ensure_column(
        conn,
        "knowledge_nodes",
        "children_json",
        "children_json TEXT NOT NULL DEFAULT '[]'",
    )
    conn.commit()
