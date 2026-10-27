-- 팀 ERD 기준 테이블: users, sessions, chat_logs, chat_history, kb_gaps, documents, eval_results(미정).
-- ERD와 다른 열이 있으면 ERD에 맞춰 고칠 것.
-- 모든 테이블은 utf8mb4 로 만든다. (기본 문자셋이 latin1 인 로컬 MySQL·MariaDB 백업 환경에서도 한글이 깨지지 않게)

CREATE TABLE IF NOT EXISTS users (
  employee_id    VARCHAR(20)  PRIMARY KEY,
  password_hash  VARCHAR(255) NOT NULL,
  name           VARCHAR(50)  NOT NULL,
  role           ENUM('user', 'admin') NOT NULL DEFAULT 'user',
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS sessions (
  token          CHAR(64)    PRIMARY KEY,
  employee_id    VARCHAR(20) NOT NULL,
  created_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at     DATETIME    NOT NULL,
  FOREIGN KEY (employee_id) REFERENCES users(employee_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4;

-- 요청 로그. 평가 실행(eval/run_eval.py)에서 남긴 로그는 source='eval'로 저장해 대시보드 통계에서 뺀다.
CREATE TABLE IF NOT EXISTS chat_logs (
  log_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  source            ENUM('user', 'eval') NOT NULL DEFAULT 'user',   -- 실사용 / 자동 채점
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
  INDEX idx_logs_created (created_at),
  INDEX idx_logs_not_found (not_found)
) DEFAULT CHARSET=utf8mb4;

-- 답 못 찾은 질문의 검토 상태. 로그는 그대로 두고, 관리자가 체크박스를 처음 바꿀 때 행이 생긴다.
-- log_id가 UNIQUE라서 save_review는 INSERT ... ON DUPLICATE KEY UPDATE 한 번으로 "있으면 갱신, 없으면 추가"를 처리한다.
CREATE TABLE IF NOT EXISTS kb_gaps (
  gap_id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  log_id            BIGINT NOT NULL UNIQUE,
  reviewed          BOOLEAN NOT NULL DEFAULT FALSE,   -- 담당자 확인
  kb_update_needed  BOOLEAN NOT NULL DEFAULT FALSE,   -- KB 업데이트 필요
  reviewed_by       VARCHAR(20),                      -- TODO: ERD에서 users(employee_id) FK인지 확인
  reviewed_at       DATETIME,
  FOREIGN KEY (log_id) REFERENCES chat_logs(log_id)  -- TODO: ERD에서 ON DELETE 동작 확인
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chat_history (
  id             BIGINT AUTO_INCREMENT PRIMARY KEY,
  employee_id    VARCHAR(20) NOT NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  question       TEXT NOT NULL,
  answer         JSON NOT NULL,
  INDEX idx_history_user (employee_id, created_at)
) DEFAULT CHARSET=utf8mb4;

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
) DEFAULT CHARSET=utf8mb4;

-- TODO: eval_results 테이블 (ERD에 있음, 열 정보가 정해지면 추가)
