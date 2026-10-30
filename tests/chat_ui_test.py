"""채팅 화면 틀·가짜 응답 확인 (WBS 3.7.1, 로컬 전용). DB·모델 없이 메모리 가짜 DB 로 검사함.

실행 (프로젝트 루트에서):
    pytest tests/chat_ui_test.py -v
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # chatbot 패키지
sys.path.insert(0, str(ROOT / "tests"))  # login_ui_test.FakeDb (users·sessions·chat_history 가짜 DB)

import pytest

from chatbot.core.auth import AuthManager
from chatbot.core.schemas import Model, SafetyNote
from chatbot.core.state import ConversationState
from chatbot.ui.chat import (
    CHAT_CSS_PATHS,
    EXAMPLE_QUESTIONS,
    fake_answer,
    fake_photo_answer,
    render_answer_card,
    render_user_message,
    render_welcome,
)
from chatbot.ui.gradio_app import ChatApp
from login_ui_test import FakeDb


class FakePipeline:
    """RagPipeline 대신 쓰는 가짜: answer 와 같은 모양(AnswerResponse dict)으로 가짜 응답을 돌려주고, 받은 값을 기록한다."""

    def __init__(self):
        self.calls = []
        self.result = None   # 예외 객체를 넣으면 던지고, dict 를 넣으면 그대로 돌려줌

    def answer(self, question, state, input_type):
        self.calls.append((question, input_type))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result if self.result is not None else fake_answer(question).model_dump()


@pytest.fixture
def env():
    db = FakeDb()
    conversation = ConversationState(db)
    app = ChatApp(auth=AuthManager(db, session_hours=8), conversation=conversation, pipeline=FakePipeline())
    _, state = app.on_login("e1", "pw-eng", {})
    return app, state, db


# ---------------- 가짜 응답 ----------------
def test_fake_answers_are_answer_responses():
    """가짜 응답은 진짜 응답 스키마(AnswerResponse)라서, RagPipeline 으로 바꿔도 화면 코드가 그대로 동작함"""
    assert fake_answer("벨트가 쏠려요").status == "answered"
    assert fake_answer("이상한 소리가 나요").status == "not_found"
    assert fake_photo_answer("a.png").status == "retake"


# ---------------- 말풍선·답변 카드 ----------------
def test_user_message_is_escaped():
    assert "<script>" not in render_user_message("<script>x</script>")
    assert "&lt;script&gt;" in render_user_message("<script>x</script>")


def test_answer_card_order_and_escape():
    html = render_answer_card(fake_answer("벨트"))
    positions = [html.index(w) for w in ("요약", "안전", "원인·조치", "그림", "출처·원문")]
    assert positions == sorted(positions)             # 요약 → 안전 문구 → 원인·조치 → 그림 → 출처·원문
    assert "wk-cause" in html and "<ol class='wk-actions'>" in html and "원문 보기" in html
    evil = fake_answer("벨트").model_copy(update={"summary": "<img src=x onerror=alert(1)>"})
    assert "<img src=x" not in render_answer_card(evil)


def test_answer_card_merges_duplicate_causes():
    """같은 원인이 KB마다 반복되면 한 블록으로 합치고, 조치는 중복 없이 순서대로 보인다"""
    from chatbot.core.schemas import Cause

    base = fake_answer("벨트")
    causes = [
        Cause(id="TS-HYTROL138ACC-T01-04#c1", cause="과부하", actions=["물량 줄이기", "전압 확인"]),
        Cause(id="TS-HYTROL138ACC-T01-05#c1", cause="과부하", actions=["전압 확인", "용량 확인"]),
        Cause(id="TS-HYTROL138ACC-T01-04#c2", cause="윤활 부족", actions=["재윤활"]),
    ]
    html = render_answer_card(base.model_copy(update={"causes": causes}))
    assert html.count("wk-cause-head") == 2 and html.count("<li>전압 확인</li>") == 1
    assert html.index("물량 줄이기") < html.index("용량 확인") < html.index("윤활 부족")


def test_answer_card_notice_for_not_found():
    html = render_answer_card(fake_answer("이상한 소리"))
    assert "문서에서 확인되지 않았습니다" in html and "다시 질문해주세요" in html


def test_answer_card_shows_table_and_figure_images(tmp_path):
    """표·그림 근거는 원문 보기 안에, 답변 그림은 그림 칸에 잘라 둔 이미지를 넣는다 (파일이 없으면 글자만)"""
    image = tmp_path / "crop.png"
    image.write_bytes(b"\x89PNG fake")
    base = fake_answer("벨트")
    source = base.sources[0].model_copy(update={"chunk_type": "table", "image_path": str(image)})
    figure = base.figures[0].model_copy(update={"image_path": str(image)})
    html = render_answer_card(base.model_copy(update={"sources": [source], "figures": [figure]}))
    assert html.count("data:image/png;base64,") == 2

    text_source = source.model_copy(update={"chunk_type": "text"})          # 본문 근거에는 이미지를 넣지 않음
    assert "wk-image" not in render_answer_card(base.model_copy(update={"sources": [text_source], "figures": []}))

    missing = str(tmp_path / "none.png")
    broken = base.model_copy(update={"sources": [source.model_copy(update={"image_path": missing})],
                                     "figures": [figure.model_copy(update={"image_path": missing})]})
    html = render_answer_card(broken)
    assert "wk-image" not in html and "이미지를 불러올 수 없습니다" in html and "원문 보기" in html


def test_source_quote_first_then_page_toggle(tmp_path, monkeypatch):
    """원문 보기: 출처 바로 아래 인용문 → 잘라 둔 표 이미지 → 쪽 전체 보기(누르면 확대). 쪽 이미지가 없으면 토글을 뺀다"""
    import chatbot.ui.chat.messages as messages

    image = tmp_path / "crop.png"
    image.write_bytes(b"\x89PNG fake")
    base = fake_answer("벨트")
    source = base.sources[0].model_copy(update={"chunk_type": "table", "image_path": str(image)})
    answer = base.model_copy(update={"sources": [source], "figures": []})

    monkeypatch.setattr(messages, "page_image_url", lambda doc_id, page: f"/gradio_api/file=/pages/{doc_id}_p{page}.jpg")
    html = render_answer_card(answer)
    assert html.index("</summary><blockquote class='wk-quote'>") < html.index("wk-image") < html.index("wk-page")
    assert f"p.{source.page} 전체 보기" in html and "class='wk-zoom'" in html and "<input type='checkbox'>" in html

    monkeypatch.setattr(messages, "page_image_url", lambda doc_id, page: None)
    assert "wk-page" not in render_answer_card(answer)


@pytest.mark.parametrize(("page", "page_map", "expected"), [
    (23, "1-19>21", 3),            # fenner_belt: 파일 1쪽 = 인쇄 21쪽
    (57, "1-24>6;25-31>54", 28),   # preco_l_series 두 번째 구간
    (7, "1-24>6;25-31>54", 2),
    (7, "", 7),                    # page_map 이 없으면 그대로
])
def test_source_page_to_file_page(page, page_map, expected):
    from chatbot.ui.chat.source_page import to_file_page

    assert to_file_page(page, page_map) == expected


def test_answer_card_safety_shows_source():
    note = SafetyNote(rule_id="loto", text="잠금 후 작업하세요.", doc_title="Hytrol 138ACC 매뉴얼", page=3)
    html = render_answer_card(fake_answer("벨트").model_copy(update={"safety": [note]}))
    assert "Hytrol 138ACC 매뉴얼 · p.3" in html
    bare = SafetyNote(rule_id="loto", text="잠금 후 작업하세요.")             # 출처가 없으면 문구만
    assert "wk-safety-source" not in render_answer_card(fake_answer("벨트").model_copy(update={"safety": [bare]}))


def test_render_answer_validates_dict_responses(env):
    """파이프라인이 돌려주는 응답 dict 는 ChatApp.render_answer 가 AnswerResponse 로 검사해서 그린다"""
    app, _, _ = env
    assert "wk-card" in app.render_answer(fake_answer("벨트").model_dump())
    bad = app.render_answer({"status": "answered"})           # 요약·출처가 없는 answered → 형식 오류
    assert "답변 형식이 올바르지 않아" in bad and "답변을 만들지 못했습니다" in bad
    assert "답변 형식이 올바르지 않아" in app.render_answer({})


def test_welcome_text():
    assert "어떤 문제가 있나요?" in render_welcome()


# ---------------- ChatApp 동작 ----------------
def test_submit_calls_pipeline_and_saves_history(env):
    app, state, db = env
    history, state = app.on_submit("  3라인 벨트가 쏠려요  ", [], state)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert "3라인 벨트가 쏠려요" in history[0]["content"] and "wk-card" in history[1]["content"]
    assert app._pipeline.calls == [("3라인 벨트가 쏠려요", "text")]   # 앞뒤 공백을 지운 질문, 입력 유형 text
    assert len(db.history) == 1                                       # 질문·답변을 대화 기록에 저장


def test_submit_shows_not_found_notice(env):
    app, state, _ = env
    app._pipeline.result = fake_answer("이상한 소리가 나요").model_dump()
    history, _ = app.on_submit("질문", [], state)
    assert "문서에서 확인되지 않았습니다" in history[1]["content"]


def test_submit_without_pipeline_shows_notice(env):
    app, state, _ = env
    app._pipeline = None
    history, _ = app.on_submit("벨트가 쏠려요", [], state)
    assert "답변 기능을 아직 사용할 수 없습니다" in history[1]["content"]


def test_submit_survives_pipeline_error_and_bad_response(env):
    app, state, _ = env
    app._pipeline.result = RuntimeError("boom")
    history, _ = app.on_submit("질문", [], state)
    assert "답변을 만드는 중 문제가 생겼어요" in history[1]["content"]
    app._pipeline.result = {"status": "answered"}          # summary·sources 가 없는 형식 오류
    history, _ = app.on_submit("질문", [], state)
    assert "답변 형식이 올바르지 않아" in history[1]["content"]


def test_submit_ignores_empty_question_and_logged_out(env):
    app, state, _ = env
    assert app.on_submit("   ", [], state)[0] == []
    assert app.on_submit(None, [], state)[0] == []
    assert app.on_submit("질문", [], {})[0] == []     # 로그인 전


def test_submit_keeps_given_history(env):
    app, state, _ = env
    first, state = app.on_submit("첫 질문", [], state)
    second, _ = app.on_submit("둘째 질문", first, state)
    assert len(second) == 4 and len(first) == 2       # 받은 history 는 고치지 않고 새 목록을 돌려줌


def test_reset_clears_equipment_but_keeps_user(env):
    app, state, _ = env
    state = app._conversation.set_equipment(state, Model("138-ACC"))
    state = app.reset_conversation(state)
    assert state["equipment"] is None and state["user"].employee_id == "e1"


def test_show_examples(env):
    app, _, _ = env
    templates, text = app.show_examples("모터가 뜨거워요")
    assert templates == list(EXAMPLE_QUESTIONS) and text == "모터가 뜨거워요"
    assert app.show_examples(None)[1] == ""


def test_screen_has_no_equipment_chip(env):
    """10/30: 상단 바 설비 표시를 지우고 서비스 이름(Wanna Know)을 둠. 설비는 대화 상태에만 남고 화면 칸은 없음"""
    app, state, _ = env
    state = app._conversation.set_equipment(state, Model("138-ACC"))
    screen = app._screen(state)
    assert len(screen) == 8 and "138-ACC" not in str(screen)


def test_chat_css_files_exist():
    assert all(Path(p).exists() for p in CHAT_CSS_PATHS)
    assert ChatApp.launch_options()["css_paths"][-len(CHAT_CSS_PATHS):] == list(CHAT_CSS_PATHS)


def test_theme_toggle_is_enabled_and_keeps_choice():
    """10/30: 딥 포레스트 다크를 모든 화면에 맞춰 전환을 켬 (ui/login/theme.py 의 THEME_TOGGLE_ENABLED).
    처음은 라이트, 고른 값은 localStorage 에 기억하고 Gradio 가 OS 설정으로 dark 를 바꿔도 고른 값으로 되돌림"""
    from chatbot.ui.login.theme import THEME_INIT_JS, THEME_KEY, THEME_TOGGLE_ENABLED, THEME_TOGGLE_JS

    assert THEME_TOGGLE_ENABLED is True
    assert f"localStorage.getItem('{THEME_KEY}')" in THEME_INIT_JS and "MutationObserver" in THEME_INIT_JS
    assert f"localStorage.setItem('{THEME_KEY}'" in THEME_TOGGLE_JS


def test_photo_notice_titles_and_retake_button():
    """Figma 04: 명판으로 설비를 정하면 제목에 모델명, 사진 재촬영이면 확신도 + '다시 촬영'(사진 첨부 버튼을 누름)"""
    from chatbot.core.schemas import AnswerResponse, DamageResult, Equipment, ImageResult

    nameplate = AnswerResponse(status="nameplate", input_type="nameplate_photo", message="이제부터 CV-1200 매뉴얼 기준",
                               equipment=Equipment(model="CV-1200", doc_ids=[], source="nameplate"))
    html = render_answer_card(nameplate)
    assert "명판에서 모델명 CV-1200을(를) 확인했습니다" in html and "wk-retake" not in html
    damage = DamageResult(label="other", symptom_ko="", confidence=0.42)
    retake = AnswerResponse(status="retake", input_type="damage_photo", message="다시 찍어 주세요",
                            image=ImageResult(kind="belt_damage", confidence=0.9, damage=damage))
    html = render_answer_card(retake)
    assert "사진만으로 판단하기 어렵습니다 (확신도 0.42)" in html
    assert "class='wk-retake'" in html and "#chat-image" in html


def test_theme_buttons_are_shown():
    import gradio as gr

    from chatbot.ui.chat import build_chat_area
    from chatbot.ui.login.login_form import build_login_form

    with gr.Blocks():
        area, login = build_chat_area(), build_login_form()
    assert area.theme_button.visible is True and login.theme_button.visible is True
