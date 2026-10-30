"""사진 분류 평가. 기본은 목록 확인, --run을 붙이면 실제 API를 호출한다."""
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
LABELS = ("nameplate", "belt_damage", "other")
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="실제 사진을 API에 전송")
    parser.add_argument("--limit", type=int, help="앞에서부터 평가할 사진 수")
    parser.add_argument("--label", choices=LABELS, help="평가할 분류 하나만 선택")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다.")

    cases = []
    for label in LABELS:
        paths = sorted(
            path for path in (ROOT / "eval/images" / label).rglob("*")
            if path.is_file() and path.suffix.lower() in EXTENSIONS
        )
        print(f"{label}: {len(paths)}장 (폴더명을 정답으로 사용)")
        if args.label is None or args.label == label:
            cases.extend((path, label) for path in paths)
    if args.limit:
        cases = cases[:args.limit]
    if not cases:
        parser.error("평가할 이미지가 없습니다.")
    if not args.run:
        for path, label in cases:
            print(f"{path.relative_to(ROOT)} → 정답: {label}")
        print("목록 확인만 완료했습니다. 실제 호출은 --run을 붙이세요.")
        return

    from chatbot.core.config import Settings
    from chatbot.rag.llm import LLMClient
    from chatbot.vision.vision import ImageRouter

    settings = Settings.load()
    router = ImageRouter(LLMClient(settings, cache_path=""))
    output_dir = ROOT / "eval/results"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d_%H%M%S_%f")
    output = output_dir / f"image_classification_{stamp}.csv"
    fields = ["file", "expected", "predicted", "confidence", "correct",
              "retake", "threshold", "model", "latency_ms", "error_type"]
    successes = correct = errors = 0
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for path, expected in cases:
            row = dict(file=str(path.relative_to(ROOT)), expected=expected,
                       threshold=settings.image_confidence_threshold,
                       model=settings.model_vision)
            start = time.perf_counter()
            try:
                predicted, confidence = router.classify_image(path.read_bytes())
                matched = predicted == expected
                row.update(predicted=predicted, confidence=confidence,
                           correct=matched,
                           retake=(predicted == "other" or
                                   confidence < settings.image_confidence_threshold))
                successes += 1
                correct += int(matched)
                print(f"{path.name}: {predicted} ({confidence:.3f}), 정답={expected}")
            except Exception as error:
                # 오류 원문에는 인증·서비스 정보가 포함될 수 있어 종류만 기록한다.
                row["error_type"] = type(error).__name__
                errors += 1
                print(f"{path.name}: 호출/검증 오류 ({type(error).__name__})")
            row["latency_ms"] = round((time.perf_counter() - start) * 1000)
            writer.writerow(row)
            stream.flush()
    print(f"정답 {correct}/{len(cases)}, 정상 처리 {successes}, 오류 {errors}")
    print(f"전체 기준 정확도: {correct / len(cases):.1%} (오류 포함)")
    print(f"결과표: {output}")
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
