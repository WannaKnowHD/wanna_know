-- 기능명세서 기준 테이블. 팀에서 이미 정한 테이블이 있으면 이름·열을 맞춰 합칠 것.

CREATE TABLE IF NOT EXISTS users (
  employee_id    VARCHAR(20)  PRIMARY KEY,
  password_hash  VARCHAR(100) NOT NULL,
  name           VARCHAR(50)  NOT NULL,
  role           ENUM('engineer', 'admin') NOT NULL DEFAULT 'engineer',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
  token          CHAR(64)    PRIMARY KEY,
  employee_id    VARCHAR(20) NOT NULL,
  created_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at     DATETIME    NOT NULL,
  FOREIGN KEY (employee_id) REFERENCES users(employee_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS query_logs (
  log_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id       VARCHAR(20),
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question          TEXT NOT NULL,
  input_type        ENUM('text', 'nameplate', 'damage', 'other') NOT NULL,
  question_type     VARCHAR(30),
  equipment_model   VARCHAR(50),
  kb_ids            JSON,
  top_score         FLOAT,            -- 리랭커 1위 점수
  not_found         BOOLEAN NOT NULL DEFAULT FALSE,
  safety_included   BOOLEAN NOT NULL DEFAULT FALSE,
  confidence        FLOAT,            -- 사진 판별 확신도
  latency_ms        JSON,             -- 단계별 응답 시간
  tokens_in         INT,
  tokens_out        INT,
  error             VARCHAR(255),
  reviewed          BOOLEAN NOT NULL DEFAULT FALSE,   -- 담당자 확인
  kb_update_needed  BOOLEAN NOT NULL DEFAULT FALSE,   -- KB 업데이트 필요
  reviewed_by       VARCHAR(20),
  reviewed_at       DATETIME,
  INDEX idx_logs_created (created_at),
  INDEX idx_logs_not_found (not_found)
);

CREATE TABLE IF NOT EXISTS chat_history (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id    VARCHAR(20) NOT NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question       TEXT NOT NULL,
  answer         JSON NOT NULL,
  INDEX idx_history_user (employee_id, created_at)
);

CREATE TABLE IF NOT EXISTS documents (
  doc_id            VARCHAR(50) PRIMARY KEY,
  filename          VARCHAR(255) NOT NULL,
  title             VARCHAR(255),
  version           VARCHAR(20),
  equipment_models  JSON,
  lang              VARCHAR(5) DEFAULT 'en',
  source            ENUM('initial', 'admin_add') NOT NULL,
  pages             INT,
  chunks            INT,
  status            ENUM('indexing', 'done', 'failed') NOT NULL DEFAULT 'indexing',
  error_message     VARCHAR(255),
  indexed_at        DATETIME
);
