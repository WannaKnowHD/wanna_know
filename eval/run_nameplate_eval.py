"""명판 인식 정확도 평가. 기본은 목록 확인, --run을 붙이면 실제 API를 호출한다.

정답은 파일 이름 앞부분 (예: 138-ACC_front_img.png → 138-ACC, MB-200-400_side_img.png → MB-200-400)
  명판에 다른 표기가 인쇄된 경우는 EXTRA_ANSWERS 에 함께 인정할 표기를 적는다 (예: MB-200-400-WD)
  - read_ok  : 판독한 모델명(raw_text)을 정리했을 때 정답과 같음  ← 지표 "명판 인식 정확도"
  - match    : models.csv 매칭 결과 (exact · near · none). MB-200 은 models.csv 에 없어서 none 이 정상
  - retake   : 모델명을 못 읽었거나 확신도가 기준 미만 → 앱에서 재촬영 요청

[실행]  python eval/run_nameplate_eval.py          # 목록 확인
        python eval/run_nameplate_eval.py --run    # 실제 판독 (20장)
[결과]  eval/results/nameplate_{날짜시각}.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IMAGE_DIR = ROOT / "eval" / "images" / "nameplate"
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
# 파일 이름 정답 외에 맞은 것으로 인정할 표기 (명판 사진에 실제로 인쇄된 표기, 사진을 보고 확인한 것만)
EXTRA_ANSWERS = {
    "MB-200-400_side_img.png": ["MB-200-400-WD"],
    "MB-200-600_side_img.png": ["MB-200-600-WD"],
    "MB-200-800_side_img.png": ["MB-200-800-WD"],
}


def expected_model(path: Path) -> str:
    """138-LRSS_right_blur_img.png → 138-LRSS (첫 '_' 앞)"""
    return path.stem.split("_", 1)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="실제 사진을 API에 전송")
    parser.add_argument("--limit", type=int, help="앞에서부터 평가할 사진 수")
    args = parser.parse_args()

    paths = sorted(p for p in IMAGE_DIR.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS)
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        parser.error("평가할 명판 사진이 없습니다.")
    if not args.run:
        for path in paths:
            print(f"{path.relative_to(ROOT).as_posix()} → 정답: {expected_model(path)}")
        print(f"{len(paths)}장 목록 확인만 완료했습니다. 실제 호출은 --run을 붙이세요.")
        return

    from chatbot.core.config import Settings
    from chatbot.rag.glossary import Glossary
    from chatbot.rag.llm import LLMClient
    from chatbot.vision.vision import NameplateReader

    settings = Settings.load()
    reader = NameplateReader(LLMClient(settings, cache_path=""))
    models = Glossary().load_models(str(ROOT / "data" / "models.csv"))
    output_dir = ROOT / "eval" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S")
    output = output_dir / f"nameplate_{stamp}.csv"
    fields = ["file", "expected", "raw_text", "read_ok", "matched_model", "match", "confidence",
              "retake", "model", "latency_ms", "error_type"]

    read_ok = errors = 0
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for path in paths:
            expected = expected_model(path)
            row = {"file": path.relative_to(ROOT).as_posix(), "expected": expected, "model": settings.model_vision}
            start = time.perf_counter()
            try:
                reading = reader.read_nameplate(path.read_bytes())
                answers = {reader.normalize_model(a) for a in [expected, *EXTRA_ANSWERS.get(path.name, [])]}
                ok = reader.normalize_model(reading["raw_text"]) in answers
                matched, match = reader.match_model(reading["raw_text"] or "", models)
                row.update(raw_text=reading["raw_text"], read_ok=ok, confidence=reading["confidence"],
                           retake=reading["retake"], match=match,
                           matched_model=getattr(matched, "model_name", "") if matched else "")
                read_ok += int(ok)
                print(f"{path.name}: {reading['raw_text']} ({reading['confidence']:.2f}) "
                      f"{'OK' if ok else 'X'} 매칭={match}")
            except Exception as error:  # noqa: BLE001 — 오류 원문에는 인증 정보가 섞일 수 있어 종류만 기록
                row["error_type"] = type(error).__name__
                errors += 1
                print(f"{path.name}: 호출/검증 오류 ({type(error).__name__})")
            row["latency_ms"] = round((time.perf_counter() - start) * 1000)
            writer.writerow(row)
            stream.flush()

    print(f"\n명판 인식 정확도: {read_ok}/{len(paths)} = {read_ok / len(paths):.1%} (오류 {errors}건 포함)")
    print(f"결과표: {output}")


if __name__ == "__main__":
    main()
