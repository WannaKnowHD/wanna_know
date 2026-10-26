"""Aiven MySQL 접속 확인. 실행: python scripts/test_db.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 프로젝트 루트에서 chatbot 패키지 찾기


def main():
    # TODO: Settings.load → Database.connect → fetch("SELECT 1") 결과 출력
    raise NotImplementedError


if __name__ == "__main__":
    main()
