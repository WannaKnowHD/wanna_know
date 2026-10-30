-- 팀 ERD v6 기준 테이블 6개: users, sessions, chat_logs, kb_gaps, chat_history, documents
-- - eval_results 는 두지 않는다. 평가 결과는 eval/run_eval.py 가 eval/results/*.csv 로 저장하고 시스템 탭도 CSV 를 읽는다.
-- - KB·용어집·설비 모델·안전 규칙은 파일(kb/*.json, data/*.csv, data/safety_rules.json)과 색인이 원본이라 DB 에 두지 않는다.
-- - scripts/seed.py 가 실행한다. 여러 번 실행해도 안전하도록 CREATE TABLE IF NOT EXISTS 를 쓴다.
--   (이미 있는 테이블에는 바뀐 열·FK 가 반영되지 않는다)
-- - FK 때문에 만드는 순서가 중요하다: users → sessions → chat_logs → kb_gaps → chat_history → documents
-- - 모든 테이블은 utf8mb4 로 만든다. (기본 문자셋이 latin1 인 로컬 MySQL·MariaDB 백업 환경에서도 한글이 깨지지 않게)
-- - Aiven 은 기본키 없는 테이블을 만들 수 없다(sql_require_primary_key=1). 모든 테이블에 기본키를 둔다.

-- 사원 계정. scripts/seed.py 가 넣고 auth.py 가 읽는다.
CREATE TABLE IF NOT EXISTS users (
  employee_id    VARCHAR(20)  NOT NULL COMMENT '사원번호',
  password_hash  VARCHAR(255) NOT NULL COMMENT '비밀번호 해시(pbkdf2_sha256, 원문 저장 금지)',
  name           VARCHAR(50)  NOT NULL COMMENT '이름',
  role           ENUM('user', 'admin') NOT NULL DEFAULT 'user' COMMENT '권한',
  created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '계정 생성 시각',
  PRIMARY KEY (employee_id)
) DEFAULT CHARSET=utf8mb4 COMMENT='사원 계정';

-- 로그인 세션. 8시간 뒤 만료, 로그아웃하면 삭제. 사원이 지워지면 같이 지워진다.
CREATE TABLE IF NOT EXISTS sessions (
  token          CHAR(64)    NOT NULL COMMENT '세션 토큰(secrets.token_hex(32))',
  employee_id    VARCHAR(20) NOT NULL COMMENT '사원번호',
  created_at     DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '생성 시각',
  expires_at     DATETIME    NOT NULL COMMENT '만료 시각(생성 후 SESSION_HOURS)',
  PRIMARY KEY (token),
  CONSTRAINT fk_sessions_user FOREIGN KEY (employee_id)
    REFERENCES users (employee_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4 COMMENT='로그인 세션';

-- 요청 로그. 평가 실행(eval/run_eval.py) 로그는 source='eval'·employee_id=NULL 로 저장해 대시보드 통계에서 뺀다.
-- 사원이 지워져도 통계는 남도록 employee_id 만 NULL 로 바뀐다.
CREATE TABLE IF NOT EXISTS chat_logs (
  log_id            BIGINT      NOT NULL AUTO_INCREMENT COMMENT '로그 ID',
  source            ENUM('user', 'eval') NOT NULL DEFAULT 'user' COMMENT '실사용 / 자동 채점',
  employee_id       VARCHAR(20) NULL COMMENT '사원번호(평가 실행은 NULL)',
  created_at        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '요청 시각',
  question          TEXT        NOT NULL COMMENT '질문',
  input_type        ENUM('text', 'nameplate', 'damage', 'other') NOT NULL COMMENT '입력 유형',
  question_type     VARCHAR(30) NULL COMMENT '질문 유형(troubleshooting/maintenance/procedure/out_of_scope)',
  equipment_model   VARCHAR(50) NULL COMMENT '설비 모델명',
  kb_ids            JSON        NULL COMMENT '근거 KB id 목록',
  top_score         FLOAT       NULL COMMENT '리랭커 1위 점수',
  not_found         BOOLEAN     NOT NULL DEFAULT FALSE COMMENT '답 못 찾음 여부',
  safety_included   BOOLEAN     NOT NULL DEFAULT FALSE COMMENT '안전 문구 포함 여부',
  confidence        FLOAT       NULL COMMENT '사진 판별 확신도',
  latency_ms        JSON        NULL COMMENT '단계별 응답 시간(ms)',
  error             VARCHAR(255) NULL COMMENT '오류 내용',
  PRIMARY KEY (log_id),
  INDEX idx_logs_created (created_at),
  INDEX idx_logs_not_found (not_found),
  CONSTRAINT fk_logs_user FOREIGN KEY (employee_id)
    REFERENCES users (employee_id) ON DELETE SET NULL
) DEFAULT CHARSET=utf8mb4 COMMENT='요청 로그';

-- 답 못 찾은 질문의 검토 상태. 로그는 그대로 두고, 관리자가 체크박스를 처음 바꿀 때 행이 생긴다.
-- log_id 가 UNIQUE 라서 save_review 는 INSERT ... ON DUPLICATE KEY UPDATE 한 번으로 "있으면 갱신, 없으면 추가"를 처리한다.
-- 로그가 지워지면 같이 지워지고, 처리자 계정이 지워지면 reviewed_by 만 NULL 로 바뀐다.
CREATE TABLE IF NOT EXISTS kb_gaps (
  gap_id            BIGINT      NOT NULL AUTO_INCREMENT COMMENT '검토 ID',
  log_id            BIGINT      NOT NULL COMMENT '로그 ID',
  reviewed          BOOLEAN     NOT NULL DEFAULT FALSE COMMENT '담당자 확인',
  kb_update_needed  BOOLEAN     NOT NULL DEFAULT FALSE COMMENT 'KB 업데이트 필요',
  reviewed_by       VARCHAR(20) NULL COMMENT '처리자 사원번호',
  reviewed_at       DATETIME    NULL COMMENT '처리 시각',
  PRIMARY KEY (gap_id),
  UNIQUE KEY uq_gaps_log (log_id),
  CONSTRAINT fk_gaps_log FOREIGN KEY (log_id)
    REFERENCES chat_logs (log_id) ON DELETE CASCADE,
  CONSTRAINT fk_gaps_reviewer FOREIGN KEY (reviewed_by)
    REFERENCES users (employee_id) ON DELETE SET NULL
) DEFAULT CHARSET=utf8mb4 COMMENT='KB 보강 검토';

-- 사원별 대화 기록(질문·답변 한 쌍). 같은 conversation_id 를 가진 행이 하나의 대화(스레드)다. 새 대화를 시작하면 새 ID 를 쓴다.
-- 로그아웃 후 다시 로그인해도 로그창에 보인다. 사원이 지워지면 같이 지워진다.
CREATE TABLE IF NOT EXISTS chat_history (
  id              BIGINT      NOT NULL AUTO_INCREMENT COMMENT '기록 ID',
  employee_id     VARCHAR(20) NOT NULL COMMENT '사원번호',
  conversation_id VARCHAR(36) NOT NULL COMMENT '대화 ID (같은 대화의 질문·답변끼리 같은 값)',
  created_at      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '일시',
  question        TEXT        NOT NULL COMMENT '질문',
  answer          JSON        NOT NULL COMMENT '응답 JSON',
  PRIMARY KEY (id),
  INDEX idx_history_user (employee_id, created_at),
  INDEX idx_history_conversation (employee_id, conversation_id),
  CONSTRAINT fk_history_user FOREIGN KEY (employee_id)
    REFERENCES users (employee_id) ON DELETE CASCADE
) DEFAULT CHARSET=utf8mb4 COMMENT='대화 기록';

-- 매뉴얼 PDF 와 색인 상태. 화면 08 KB 현황(kb_status)·PDF 추가(add_pdf)가 쓴다. 다른 테이블과 관계없는 독립 테이블.
-- 언어·판·적용 설비는 두지 않는다. 매뉴얼은 모두 영어이고 판은 문서마다 하나(schemas.SourceRef), 적용 설비는 data/models.csv 가 원본이다.
CREATE TABLE IF NOT EXISTS documents (
  doc_id            VARCHAR(50)  NOT NULL COMMENT '문서 id(sources.csv 의 doc_id)',
  filename          VARCHAR(255) NOT NULL COMMENT 'PDF 파일명',
  title             VARCHAR(255) NULL COMMENT '문서명',
  source            ENUM('initial', 'admin_add') NOT NULL COMMENT '초기 문서 / 관리자 추가',
  pages             INT          NULL COMMENT '페이지 수',
  chunks            INT          NULL COMMENT '청크 수',
  status            ENUM('indexing', 'done', 'failed') NOT NULL DEFAULT 'indexing' COMMENT '색인 상태',
  error_message     VARCHAR(255) NULL COMMENT '실패 사유',
  indexed_at        DATETIME     NULL COMMENT '색인 시각',
  PRIMARY KEY (doc_id)
) DEFAULT CHARSET=utf8mb4 COMMENT='매뉴얼 문서·색인 상태';
