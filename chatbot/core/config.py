"""설정값 읽기 (.env)"""
from __future__ import annotations

from typing import TYPE_CHECKING


class Settings:
    """Settings (기능명세서 기준 뼈대)."""

    def load(self):
        """
        환경변수(.env)에서 API 키, MODEL_TEXT·MODEL_VISION, MySQL(Aiven) 접속 정보(호스트·계정·비밀번호, 인증서
        certs/ca.pem 경로), 로그인 세션 설정(브라우저 저장 키·암호 키, 만료 시간 8시간), 문서·색인 경로, 기준값(근거 점수 0.55,
        사진 확신도 0.6)을 읽음 (코드에 모델명·키·비밀번호를 직접 쓰지 않음)

        Returns: Settings
        """
        raise NotImplementedError("Settings.load")
