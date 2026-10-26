# 현장 컨베이어 문제 해결 챗봇 (wanna_know 5팀)

매뉴얼 PDF를 근거로 컨베이어 장애 조치를 안내하는 RAG 챗봇입니다. 화면은 Gradio, 저장소는 MySQL을 씁니다.

## 처음 설정

```bash
git clone <저장소 주소>
cd conveyor-chatbot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 값 채우기 (팀 채널에서 공유받기)
# certs/ca.pem 넣기 (팀 채널에서 공유받기, 커밋 금지)
python scripts/test_db.py          # DB 접속 확인
```

## 실행

모든 명령은 프로젝트 루트에서 실행합니다.

| 명령 | 하는 일 |
| --- | --- |
| `python app.py` | 챗봇 실행 |
| `python build_index.py` | KB·색인 전체 재생성 |
| `python eval/run_eval.py` | 평가셋 자동 채점 |
| `python scripts/seed.py` | 시연 계정·초기 문서 등록 |
| `pytest` | 코드가 기능명세서와 일치하는지 확인 |

## 폴더 구조

```
app.py · build_index.py      실행 파일
chatbot/pipeline.py          질문 처리 파이프라인
chatbot/ui/                  화면 (gradio_app, dashboard)
chatbot/rag/                 문서 처리·검색·생성·검증
chatbot/vision/              사진 처리
chatbot/core/                설정, 로그인·세션, 상태, 안전 문구, 로그, 공통 데이터 클래스
chatbot/db/                  MySQL 연결, 테이블 정의(schema.sql)
data/                        원본 PDF(커밋 안 함), 용어집·모델 목록 등 CSV
kb/                          KbBuilder가 만든 KB JSON
index/                       색인 (커밋 안 함)
eval/                        평가 스크립트·평가셋·결과
tests/                       명세서 일치 테스트
docs/                        기능명세서
```

## 개발 규칙

- 사용자별 값(로그인 사원, 설비 필터, 대화 맥락)은 **전역 변수에 두지 않고** `gr.State`로 넘깁니다.
- 관리자 함수는 화면에서 숨겨도 API로 호출될 수 있으므로, **함수 안에서 `user.is_admin`을 확인**합니다.
- `.env`, `certs/`, `data/raw/`, `index/`는 커밋하지 않습니다.
- 기능명세서를 바꾸면 `docs/기능명세서.xlsx`와 `docs/기능명세서.md`를 함께 갱신하고 `pytest`로 확인합니다.
- WBS(진행 상태)는 구글 드라이브에서만 관리합니다. 저장소에는 두지 않습니다.

## 담당

| 영역 | 파일 | 담당 |
| --- | --- | --- |
| 화면 | chatbot/ui/ | |
| 문서 처리·색인 | chatbot/rag/document_processor.py, build_index.py | |
| 검색 | chatbot/rag/retriever.py, glossary.py | |
| 생성·검증 | chatbot/pipeline.py, rag/llm.py, rag/validator.py, core/safety.py | |
| 사진 | chatbot/vision/ | |
| 로그인·DB | chatbot/core/auth.py, state.py, logger.py, chatbot/db/ | |
| 평가·대시보드 | eval/, chatbot/ui/dashboard.py | |
