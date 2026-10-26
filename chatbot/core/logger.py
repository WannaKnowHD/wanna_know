"""질의 로그 저장"""
from __future__ import annotations

from typing import TYPE_CHECKING


class RequestLogger:
    """RequestLogger (기능명세서 기준 뼈대)."""

    def log_request(self, record: dict):
        """
        요청 1건을 MySQL chat_logs 테이블에 저장 (사원번호, 질문, 입력 유형(텍스트·명판 사진·손상 사진·그 외 사진), 질문 유형,
        설비, kb_ids, 검색 점수, 답 못 찾음 여부, 안전 문구 포함 여부, 확신도, 단계별 시간, 토큰, 오류). 평가 실행이면
        source='eval', 실사용이면 source='user'(기본값)로 저장. 담당자 확인·KB 업데이트 필요는 여기서 저장하지 않음
        (kb_gaps 테이블에서 관리). 이름 등 사원번호 외 개인정보는 저장하지 않음

        Returns: int (log_id)
        """
        raise NotImplementedError("RequestLogger.log_request")
