"""시연 계정을 users 테이블에 넣음. 실행: python scripts/seed.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 프로젝트 루트에서 chatbot 패키지 찾기


def main():
    # TODO: Settings.load → Database.connect → 비밀번호 해시(bcrypt) 후 users에 INSERT
    #       data/sources.csv의 초기 문서 목록을 documents 테이블에 등록
    raise NotImplementedError


if __name__ == "__main__":
    main()
