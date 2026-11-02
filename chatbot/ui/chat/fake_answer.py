"""가짜 응답 (WBS 3.7.1). RagPipeline.answer 가 연결되기 전까지 화면이 움직이는지 보는 용도.

돌려주는 값은 schemas.AnswerResponse 라서(만들 때 형식을 검사함), 나중에 진짜 응답으로 바꿔도 화면 코드는 그대로 쓴다.
RagPipeline 을 연결하는 곳(ChatApp.on_submit)에서 이 파일의 함수 호출만 pipeline.answer 로 바꾸면 된다.
"""
from __future__ import annotations

from chatbot.core.schemas import AnswerResponse, Cause, Figure, SafetyNote, Source

# 이 말이 질문에 들어 있으면 "문서에서 확인되지 않음" 응답을 돌려줌 (Figma 03 근거 부족 화면 확인용)
NOT_FOUND_HINTS = ("소리",)

FAKE_NOTICE = "(가짜 응답) 아직 챗봇에 연결되지 않았습니다. 화면 확인용 예시입니다."


def fake_answer(question: str) -> AnswerResponse:
    """질문 한 건에 대한 가짜 응답"""
    if any(hint in (question or "") for hint in NOT_FOUND_HINTS):
        return AnswerResponse(
            status="not_found",
            input_type="text",
            message="질문하신 증상과 일치하는 내용을 매뉴얼에서 찾지 못했습니다. 다시 질문해주세요.",
        )
    return AnswerResponse(
        status="answered",
        input_type="text",
        question_type="troubleshooting",
        summary=f"{FAKE_NOTICE} 테일 풀리 정렬 불량이나 리턴 롤러 고착이 주요 원인입니다. 정지 후 롤러 상태를 확인하고 테일 풀리를 조정하세요.",
        safety=[SafetyNote(rule_id="loto", text="비상정지 후 LOTO(잠금·표지)를 실시하고, 회전체가 완전히 멈춘 것을 확인한 뒤 작업하세요.")],
        causes=[
            Cause(
                id="TS-HYTROL138ACC-T01-04#c1",
                cause="테일 풀리 정렬 불량",
                actions=["쏠리는 방향 확인", "테일 풀리 텐션 볼트 조정", "저속 시운전으로 확인"],
            ),
            Cause(id="TS-HYTROL138ACC-T01-04#c2", cause="리턴 롤러 고착", actions=["리턴 롤러 회전 점검", "고착된 롤러 교체"]),
        ],
        figures=[
            Figure(
                label="그림 4-2",
                caption="테일 풀리 조정 위치",
                doc_id="hytrol_138acc",
                page=12,
                image_path="data/figures/hytrol_138acc_p12.png",
            )
        ],
        sources=[
            Source(
                ref_id="TS-HYTROL138ACC-T01-04",
                quote="Belt tracks to one side: check tail pulley alignment and return roller rotation.",
                doc_id="hytrol_138acc",
                doc_title="Hytrol 138ACC 매뉴얼",
                page=12,
                chunk_type="kb",
            )
        ],
        kb_ids=["TS-HYTROL138ACC-T01-04"],
    )


def fake_photo_answer(filename: str = "") -> AnswerResponse:
    """사진 첨부에 대한 가짜 응답. 사진 분석(on_image)은 3.7.5 에서 연결"""
    return AnswerResponse(
        status="retake",
        input_type="damage_photo",
        message=f"{FAKE_NOTICE} 사진 분석은 아직 연결되지 않았습니다." + (f" (받은 파일: {filename})" if filename else ""),
    )
