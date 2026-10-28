"""
==============================================================================
 document_processor.py : PDF → 페이지 → 섹션  (PdfExtractor 담당: 박세람)
==============================================================================

[이 파일이 하는 일]  기능명세서 D-01
  1) extract_text   : PDF 에서 페이지별 영어 본문 + 표·그림 위치(bbox)를 뽑는다
  2) split_sections : 섹션 제목(큰 글씨)을 기준으로 본문을 나누고, 표·그림도 섹션으로 넣는다
  3) crop_regions   : 표·그림 영역을 잘라 비전 모델로 Markdown·영어 설명을 만든다 (data/interim 저장)
  (KbBuilder 는 김민재 담당)

[처리 규칙]
  - 영어·스페인어 2단 매뉴얼(Hytrol)은 스페인어 줄을 빼고 영어만 남긴다
  - 2단 배치는 왼쪽 단 → 오른쪽 단 순서로 읽는다
  - 쪽 번호·머리말처럼 위·아래 여백에 있는 숫자만 있는 줄은 뺀다
  - 표·그림 영역 안의 글자는 본문에서 빼고 그 영역(region)의 text 로 옮긴다
      → 나중에 crop_regions 이 비전 모델 결과(Markdown·영어 설명)로 text 를 바꿔 넣는다
  - Page.page_no 는 PDF 파일 안의 쪽, Section.page 는 원본 매뉴얼 쪽 (sources.csv 의 page_map)

[사용 예]
    from chatbot.rag.document_processor import PdfExtractor
    ex = PdfExtractor("data/sources.csv")
    pages = ex.extract_text("data/raw/initial/hytrol_138acc_p1-27.pdf")
    for page in pages:                                       # 표·그림 비전 파싱 (선택)
        page.regions = ex.crop_regions(pdf_path, page.page_no, page.regions)
    sections = ex.split_sections(pages)
==============================================================================
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter

import openai
import pymupdf

from chatbot.core.schemas import (ALL_MODELS, KbCause, MaintenanceKB, Page, Section, SourceRef, StrictModel,
                                  TroubleshootingKB)
from chatbot.rag.glossary import Glossary
from chatbot.rag.prompts import FIGURE_DESCRIBE, PROMPT_VERSION, TABLE_TO_MARKDOWN

# 스페인어 줄인지 판단할 때 쓰는 말 (영어에는 거의 안 나오는 짧은 단어)
SPANISH_WORDS = {"de", "la", "el", "los", "las", "del", "para", "con", "una", "por",
                 "que", "se", "en", "y", "es", "al", "su", "sus", "como", "lo", "un",
                 "grados", "temperatura", "ambiente", "revisar", "semanal", "mensual", "trimestral"}
SPANISH_CHARS = set("áéíóúñ¿¡")

MARGIN = 0.07          # 위·아래 7% 는 머리말·쪽번호 영역으로 본다
MIN_FIGURE_AREA = 0.03  # 페이지 넓이의 3% 보다 작은 그림·도형 묶음은 그림으로 보지 않는다
MIN_IMAGE_AREA = 0.01   # 사진(이미지)·자세한 도면은 1% 까지 그림으로 본다 (작아도 실제 그림인 경우가 많음)
DETAILED_PATHS = 30     # 선·도형이 30개 이상이면 자세한 도면으로 본다
TEXT_HEAVY = 0.35       # 도형 묶음 넓이의 35% 이상이 글자면 그림이 아니라 글 상자로 본다
MIN_PATHS = 6           # 선·도형이 6개보다 적으면 단순 테두리로 본다
LABEL_MAX = 40          # 그림 안에서 이보다 짧은 줄은 이름표로 본다
LABEL_NEAR = 15         # 그림에서 15pt 안에 있는 이름표 덩어리는 그림 상자에 넣는다
NEAR = 8                # 8pt 안으로 붙어 있는 조각은 같은 그림으로 합친다
CELL_GAP = 12           # 테두리만 있는 표에서 단어 사이가 12pt 넘게 벌어지면 다른 칸으로 본다
REPEAT_PAGES = 3        # 같은 자리의 그림 조각이 3쪽 이상 반복되면 꾸밈(로고·바닥 줄)으로 보고 뺀다
CROP_DPI = 200          # 표·그림을 자를 때 해상도 (작은 글자도 비전 모델이 읽도록)
CROP_PAD = 4            # 자를 때 상자 바깥으로 4pt 여유

class TableResult(StrictModel):
    """비전 모델이 표를 읽은 결과"""
    markdown: str
    summary: str


class FigureResult(StrictModel):
    """비전 모델이 그림을 설명한 결과"""
    description: str

class PdfExtractor:
    """PDF 에서 본문·표·그림 위치를 뽑고 섹션으로 나눈다."""

    def __init__(self, sources_path="data/sources.csv",llm=None, interim_dir="data/interim"):
        """
        받는 것:
          sources_path : sources.csv 경로. 파일 이름으로 doc_id 와 page_map 을 찾는다.
                         목록에 없는 PDF(관리자가 추가한 PDF)는 파일 이름이 doc_id, 쪽은 그대로.
            llm          : LLMClient (crop_regions 에서 vision 호출). 없으면 이미지만 자르고 글자는 그대로
          interim_dir  : 잘라 낸 이미지와 비전 결과를 저장할 폴더 (Settings.interim_dir)
        """
        self.llm = llm
        self.interim_dir = str(interim_dir)
        self.sources = {}      # {파일 이름: {"doc_id": ..., "page_map": ...}}
        if sources_path and os.path.exists(sources_path):
            with open(sources_path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    self.sources[row["file"]] = row
        self.page_maps = {}    # {doc_id: page_map 글자}
        self.page_lines = {}   # {(doc_id, page_no): 줄 목록} split_sections 에서 제목 찾을 때 씀

    # ==========================================================================
    # 1. extract_text : PDF → list[Page]
    # ==========================================================================
    def extract_text(self, pdf_path: str, pages: list[int] | None = None) -> list[Page]:
        """PDF 를 페이지별로 읽는다.

        받는 것:
          pdf_path : PDF 경로
          pages    : 읽을 쪽 번호 목록 (PDF 파일 기준, 1부터). None 이면 전부
        돌려주는 것: list[Page]
          Page.regions : [{"type": "table" 또는 "figure", "bbox": (x0, y0, x1, y1),
                           "text": 영역 안 글자, "image_path": None}]
        """
        doc_id = self.doc_id_of(pdf_path)
        doc = pymupdf.open(pdf_path)
        if pages is None:
            pages = range(1, doc.page_count + 1)

        # 모든 쪽에서 그림 후보를 먼저 모아, 여러 쪽에 같은 자리로 반복되는 꾸밈을 찾는다
        candidates = {no: self.figure_candidates(doc[no - 1]) for no in range(1, doc.page_count + 1)}
        decorations = self.find_decorations(candidates.values())

        result = []
        for page_no in pages:
            pdf_page = doc[page_no - 1]
            regions = self.find_regions(pdf_page, candidates[page_no], decorations)
            lines = self.read_lines(pdf_page, regions)       # 스페인어 표 글자도 본문에서 빠짐
            regions = [r for r in regions if not r.pop("spanish", False)]   # 스페인어 표는 결과에서 뺌
            result.append(Page(
                doc_id=doc_id,
                page_no=page_no,
                text="\n".join(line["text"] for line in lines),
                regions=regions,
            ))
            # split_sections 에서 제목을 찾으려고 글자 크기가 들어간 줄 목록을 저장해 둔다
            self.page_lines[(doc_id, page_no)] = lines
        doc.close()
        return result

    def find_regions(self, pdf_page, candidates=None, decorations=()):
        """표와 그림 영역(bbox)을 찾는다.

        candidates  : figure_candidates() 결과 (없으면 여기서 구함)
        decorations : 여러 쪽에 반복되는 꾸밈 자리 (find_decorations 결과) → 그림에서 뺌
        """
        page_area = pdf_page.rect.width * pdf_page.rect.height
        regions = []

        # (1) 표 : PyMuPDF 의 표 찾기 (선으로 그려진 표)
        #     회색으로 칠한 빈 칸은 "X" 로 읽는다 (Hytrol 점검표처럼 주기를 색칠로 표시한 표)
        gray_boxes = [pymupdf.Rect(d["rect"]) for d in pdf_page.get_drawings() if self.is_gray(d.get("fill"))]
        for table in pdf_page.find_tables().tables:
            rows = [[self.cell_text(pdf_page, cell, gray_boxes) for cell in row.cells] for row in table.rows]
            if len(rows) < 2:                      # 1줄짜리는 표가 아님 (상자 테두리 등)
                continue
            text = self.rows_to_markdown(rows)
            regions.append({"type": "table", "bbox": tuple(table.bbox),
                            "text": text, "image_path": None,
                            "spanish": self.is_spanish_block(text)})   # extract_text 에서 빼고 지움

        #     칸 선 없이 바깥 테두리·색칠만 있는 표 (PRECO Motor output 표 등)
        for box, rows in self.find_frame_tables(pdf_page, [pymupdf.Rect(r["bbox"]) for r in regions]):
            text = self.rows_to_markdown(rows)
            regions.append({"type": "table", "bbox": tuple(box), "text": text, "image_path": None,
                            "spanish": self.is_spanish_block(text)})

        # (2) 그림 : 사진(이미지) + 선으로 그린 도면(도형 묶음), 반복되는 꾸밈은 뺌
        if candidates is None:
            candidates = self.figure_candidates(pdf_page)
        boxes = [box for box in candidates if self.box_key(box) not in decorations]
        images = [pymupdf.Rect(info["bbox"]) for info in pdf_page.get_image_info()]
        paths = [pymupdf.Rect(d["rect"]) for d in pdf_page.get_drawings() if self.is_visible(d)]

        frames = self.find_frames(pdf_page)
        blocks = [b for b in pdf_page.get_text("dict")["blocks"] if b.get("lines")]
        label_rects = [pymupdf.Rect(ln["bbox"]) for b in blocks if self.is_label_block(b)   # 이름표 줄 하나하나
                       for ln in b["lines"]]
        para_rects = [pymupdf.Rect(b["bbox"]) for b in blocks if not self.is_label_block(b)]
        for box in self.split_by_frames(self.merge_boxes(boxes), frames):
            area = box.width * box.height
            wide = box.width >= pdf_page.rect.width * 0.5 and box.height >= 20   # 가로로 긴 도면 (옆에서 본 컨베이어)
            small_ok = area >= page_area * MIN_IMAGE_AREA and (
                any(box.contains(i) for i in images)                                   # 작은 사진
                or sum(1 for r in paths if r in box + (-1, -1, 1, 1)) >= DETAILED_PATHS)  # 작은 자세한 도면
            if (area < page_area * MIN_FIGURE_AREA and not wide and not small_ok) or area > page_area * 0.9:
                continue                           # 너무 작은 조각, 페이지 전체 테두리는 뺌
            if any(box.intersects(pymupdf.Rect(r["bbox"])) for r in regions if r["type"] == "table"):
                continue                           # 표 테두리 선은 그림이 아님
            box = self.grow_figure(box, frames, label_rects, para_rects, pdf_page.rect)   # 이름표·테두리까지 넓힘
            if any(self.same_box(box, pymupdf.Rect(r["bbox"])) for r in regions):
                continue                           # 넓혔더니 같은 테두리가 된 조각은 한 번만
            labels = self.label_lines(pdf_page, box)
            spanish = len(labels) >= 2 and sum(self.is_spanish(t) for t in labels) / len(labels) >= 0.6
            regions.append({"type": "figure", "bbox": tuple(box),
                            "text": self.drop_spanish_text("\n".join(labels)), "image_path": None,
                            "spanish": spanish})   # 이름표 60% 이상이 스페인어 = 스페인어판 그림 → extract_text 에서 뺌

        regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
        return regions

    def find_frames(self, pdf_page):
        """그림 테두리 = 선으로만 그린 큰 사각형 (페이지 넓이 2% 이상). 예) FIGURE 20A 바깥 선"""
        page_area = pdf_page.rect.width * pdf_page.rect.height
        frames = []
        for d in pdf_page.get_drawings():
            rect = pymupdf.Rect(d["rect"])
            kinds = {item[0] for item in d.get("items", [])}
            if rect.get_area() >= page_area * 0.02 and kinds <= {"l", "re"} and self.is_visible(d) \
                    and len(d["items"]) >= 4 and not any(self.same_box(rect, f) for f in frames):
                frames.append(rect)
        return frames

    def grow_figure(self, box, frames, label_rects, para_rects, page_rect):
        """그림 상자를 이름표까지 넓힌다. (비전 모델이 부품 이름까지 같이 보도록)

        1) 그림의 80% 이상이 어떤 테두리 안에 있고, 테두리가 그림보다 6배 이하로 크고, 안에 본문 문단이 없으면
           → 테두리 전체 (FIGURE 제목 포함. 그림 조각이 옆 테두리로 조금 삐져나와도 자기 테두리로 맞춤)
        2) 아니면 → 그림에서 LABEL_NEAR 안에 있고 그림과 가로로 겹치는 이름표 덩어리를 넣는다
           (넣은 이름표 옆 이름표도. 옆 단의 다른 그림 이름표까지 번지지 않게 가로로 겹치는 것만)
        """
        around = [f for f in frames
                  if (box & f).get_area() >= box.get_area() * 0.8          # 그림 대부분이 테두리 안
                  and f.get_area() <= box.get_area() * 6                    # 테두리가 그림보다 너무 크지 않음
                  and not any(f.intersects(p) and p not in box for p in para_rects)]
        if around:
            return min(around, key=lambda f: f.get_area())
        grown = pymupdf.Rect(box)
        for _ in range(3):
            near = [r for r in label_rects if (grown + (-LABEL_NEAR, -LABEL_NEAR, LABEL_NEAR, LABEL_NEAR)).intersects(r)
                    and r.x0 < box.x1 and r.x1 > box.x0 and r not in grown]
            if not near:
                break
            for r in near:
                grown |= r
        return grown & page_rect

    def split_by_frames(self, boxes, frames):
        """붙어 있는 그림 테두리(FIGURE 7A · 7B 처럼 칸이 나뉜 그림)는 테두리별로 나눈다.

        한 상자 안에 서로 겹치지 않는 테두리가 2개 이상 있고 상자의 70% 이상을 덮으면, 상자 대신 테두리들을 쓴다.
        (그림 안의 작은 확대 동그라미·상자는 테두리로 보지 않도록)
        """
        result = []
        for box in boxes:
            inside = [f for f in frames if f in box + (-2, -2, 2, 2)]
            apart = all((a & b).get_area() < min(a.get_area(), b.get_area()) * 0.05
                        for i, a in enumerate(inside) for b in inside[i + 1:])
            covers = sum(f.get_area() for f in inside) >= box.get_area() * 0.7   # 테두리가 상자 대부분을 차지
            if len(inside) >= 2 and apart and covers:
                result += inside
            else:
                result.append(box)
        return result

    def same_box(self, a, b):
        """두 상자가 2pt 안으로 같은 자리인지"""
        return max(abs(a.x0 - b.x0), abs(a.y0 - b.y0), abs(a.x1 - b.x1), abs(a.y1 - b.y1)) < 2

    def find_frame_tables(self, pdf_page, taken):
        """바깥 테두리 안에 글자가 칸처럼 줄 맞춰 있으면 표로 본다. (find_tables 가 못 찾는 표)

        - 테두리 : 페이지 폭 40% 이상, 높이 30pt 이상인 선 상자 (큰 것부터)
        - 표 조건 : 칸이 3개 이상인 줄이 3줄 이상이고, 전체 줄의 60% 이상
        돌려주는 것: [(상자, rows), ...]
        """
        width = pdf_page.rect.width
        found = []
        for drawing in sorted(pdf_page.get_drawings(), key=lambda d: -d["rect"].get_area()):
            box = pymupdf.Rect(drawing["rect"])
            if not drawing.get("color") or box.width < width * 0.4 or box.height < 30:
                continue
            if any(box.intersects(t) for t in taken + [f for f, _ in found]):
                continue
            # 세로로 쓴 글자(예: Slack H (mm))는 칸 계산에서 뺀다
            words = [w for w in pdf_page.get_text("words", clip=box)
                     if not ((w[3] - w[1]) > (w[2] - w[0]) * 1.5 and len(w[4]) > 1)]
            lines = {}
            for w in words:
                lines.setdefault(round((w[1] + w[3]) / 6), []).append(w)
            rows = [self.split_cells(line) for _, line in sorted(lines.items())]
            multi = [row for row in rows if len(row) >= 3]
            if len(multi) >= 3 and len(multi) >= len(rows) * 0.6:
                found.append((box, [row for row in rows if len(row) >= 2]))
        return found

    def split_cells(self, words):
        """한 줄의 단어를 간격(CELL_GAP)으로 나눠 칸 목록으로 만든다."""
        words = sorted(words, key=lambda w: w[0])
        cells, current = [], [words[0]]
        for w in words[1:]:
            if w[0] - current[-1][2] > CELL_GAP:
                cells.append(current)
                current = [w]
            else:
                current.append(w)
        cells.append(current)
        return [" ".join(w[4] for w in cell) for cell in cells]

    def figure_candidates(self, pdf_page):
        """그림 후보 상자 = 사진(이미지) + 도형 묶음.
        도형 묶음 중 글자로 꽉 찬 것(문단 테두리, 제목 배경, NOTE 상자)은 그림이 아니다."""
        text_boxes = [pymupdf.Rect(b[:4]) for b in pdf_page.get_text("blocks")]
        boxes = [pymupdf.Rect(info["bbox"]) for info in pdf_page.get_image_info()]
        visible = [d for d in pdf_page.get_drawings() if self.is_visible(d)]   # 흰색 배경 상자는 뺌
        try:
            drawings = pdf_page.cluster_drawings(drawings=visible) if visible else []
        except (ValueError, RuntimeError):
            drawings = []
        paths = [pymupdf.Rect(d["rect"]) for d in visible]
        for box in drawings:
            if self.text_ratio(box, text_boxes) >= TEXT_HEAVY:
                continue                           # 글자로 꽉 찬 상자
            if sum(1 for r in paths if r in box + (-1, -1, 1, 1)) < MIN_PATHS:
                continue                           # 선 몇 개짜리 테두리 (제목 배경, 각주 상자)
            boxes.append(box)
        return boxes

    def find_decorations(self, candidates_per_page):
        """여러 쪽(REPEAT_PAGES 이상)에 같은 자리로 나오는 그림 조각 = 로고·바닥 줄 같은 꾸밈"""
        count = Counter()
        for boxes in candidates_per_page:
            count.update({self.box_key(box) for box in boxes})
        return {key for key, n in count.items() if n >= REPEAT_PAGES}

    def box_key(self, box):
        """상자 위치를 5pt 단위로 반올림한 값 (쪽마다 아주 조금 달라도 같은 자리로 봄)"""
        return tuple(round(v / 5) for v in (box.x0, box.y0, box.x1, box.y1))

    def read_lines(self, pdf_page, regions):
        """본문 줄을 읽는 순서대로 돌려준다. (표·그림 안 글자, 쪽번호, 스페인어 줄은 뺌)

        돌려주는 것: [{"text", "size", "bbox"}]
        """
        height = pdf_page.rect.height
        width = pdf_page.rect.width
        table_boxes = [pymupdf.Rect(r["bbox"]) for r in regions if r["type"] == "table"]
        figure_boxes = [pymupdf.Rect(r["bbox"]) for r in regions if r["type"] == "figure"]

        lines = []
        for block in pdf_page.get_text("dict")["blocks"]:
            label_block = self.is_label_block(block)     # 짧은 줄만 있는 덩어리 = 그림 이름표 후보
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text:
                    continue
                box = pymupdf.Rect(line["bbox"])
                # 표 안 줄은 뺀다. 그림 안 줄은 이름표(짧은 줄로만 된 덩어리)만 뺀다
                #   (그림 영역이 크게 잡혀도 본문 문단의 짧은 줄 "Replace all tread rollers." 은 본문에 남도록)
                center = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
                if any(center in r for r in table_boxes):
                    continue
                if label_block and any(center in r for r in figure_boxes):
                    continue
                # 위·아래 여백의 숫자만 있는 줄 (쪽 번호) 은 뺀다
                in_margin = box.y1 < height * MARGIN or box.y0 > height * (1 - MARGIN)
                if in_margin and re.fullmatch(r"[\d\s\-–|]+", text):
                    continue
                size = max(span["size"] for span in line["spans"])
                lines.append({"text": text, "size": round(size, 1), "bbox": tuple(box),
                              "spanish": self.is_spanish(text)})

        lines = self.drop_spanish_column(lines, width)
        return self.reading_order([ln for ln in lines if not ln.pop("spanish")], width)

    def is_label_block(self, block):
        """글자 덩어리의 모든 줄이 LABEL_MAX 보다 짧으면 이름표 덩어리로 본다."""
        texts = ["".join(s["text"] for s in ln["spans"]).strip() for ln in block.get("lines", [])]
        texts = [t for t in texts if t]
        return bool(texts) and all(len(t) < LABEL_MAX for t in texts)

    def label_lines(self, pdf_page, box):
        """그림 상자 안의 이름표 줄 (이름표 덩어리이고 줄 가운데가 상자 안인 것)"""
        labels = []
        for block in pdf_page.get_text("dict")["blocks"]:
            if not self.is_label_block(block):
                continue
            for line in block["lines"]:
                rect = pymupdf.Rect(line["bbox"])
                center = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text and center in box:
                    labels.append(text)
        return labels

    def drop_spanish_column(self, lines, width):
        """영·스 2단 페이지면 스페인어 단을 통째로 뺀다.

        한 단의 줄 중 40% 이상이 스페인어로 보이면 그 단 전체를 스페인어 단으로 본다.
        (짧은 제목·번호 줄은 단어만으로 스페인어인지 알기 어려워서 단 단위로 판단)
        """
        middle = width / 2
        columns = {
            "left": [ln for ln in lines if ln["bbox"][2] <= middle + 15],
            "right": [ln for ln in lines if ln["bbox"][0] >= middle - 15],
        }
        drop = set()
        for column in columns.values():
            if column and sum(ln["spanish"] for ln in column) / len(column) >= 0.4:
                drop.update(id(ln) for ln in column)
        return [ln for ln in lines if id(ln) not in drop]

    def reading_order(self, lines, width):
        """2단 배치를 왼쪽 단 → 오른쪽 단 순서로 정렬한다.

        - 가운데를 넘는 줄(전체 폭)은 그 자리에서 단을 끊는다
        - 끊긴 구간 안에서는 왼쪽 단을 위→아래로 다 읽고 오른쪽 단을 읽는다
        """
        middle = width / 2
        lines = sorted(lines, key=lambda ln: (round(ln["bbox"][1]), ln["bbox"][0]))

        ordered, left, right = [], [], []
        for line in lines:
            x0, _, x1, _ = line["bbox"]
            if x1 <= middle + 15:
                left.append(line)
            elif x0 >= middle - 15:
                right.append(line)
            else:                                  # 전체 폭 줄 → 지금까지 모은 단을 먼저 내보냄
                ordered += left + right + [line]
                left, right = [], []
        return ordered + left + right

    # ==========================================================================
    # (다음 작업) crop_regions : 표·그림 영역 → 비전 모델 → 영역마다 text · image_path
    # ==========================================================================
    def crop_regions(self, pdf_path: str, page: int, regions: list[dict]) -> list[dict]:
        """
        PDF의 표·그림 영역을 이미지로 잘라 비전 모델(LLMClient.vision)로 보내고,
        표는 Markdown으로, 그림은 영어 설명으로 변환해 data/interim/에 저장.
        같은 영역은 캐시를 써서 다시 호출하지 않음. regions는 영역마다 종류(table/figure)와 bbox,
        결과는 영역마다 종류·변환 결과 텍스트·이미지 경로

        받는 것:
          pdf_path : PDF 경로
          page     : PDF 파일 쪽 (Page.page_no)
          regions  : Page.regions
        돌려주는 것: regions 와 같은 순서의 새 목록 (text · image_path 가 채워짐)
          저장 : data/interim/{doc_id}/p{쪽}_{번호}_{table|figure}.png  (잘라 낸 이미지)
                 data/interim/{doc_id}/p{쪽}_{번호}_{table|figure}.json (비전 결과 = 캐시)
          비전 호출이 실패하거나 llm 이 없으면 text 는 원래 글자(PDF 에서 읽은 것)를 그대로 둔다.
        """
        doc_id = self.doc_id_of(pdf_path)
        folder = os.path.join(self.interim_dir, doc_id)
        os.makedirs(folder, exist_ok=True)

        doc = pymupdf.open(pdf_path)
        pdf_page = doc[page - 1]
        results = []
        for i, region in enumerate(regions, start=1):
            name = f"p{page}_{i}_{region['type']}"
            image_path = os.path.join(folder, name + ".png")
            cache_path = os.path.join(folder, name + ".json")

            # (1) 캐시 : 같은 상자 · 같은 프롬프트 버전이면 저장된 결과를 쓴다
            cached = self.load_crop_cache(cache_path, region)
            if cached is not None:
                results.append({**region, "text": cached["text"], "image_path": image_path})
                continue

            # (2) 영역을 이미지로 잘라 저장
            box = pymupdf.Rect(region["bbox"]) + (-CROP_PAD, -CROP_PAD, CROP_PAD, CROP_PAD)
            png = pdf_page.get_pixmap(dpi=CROP_DPI, clip=box & pdf_page.rect).tobytes("png")
            with open(image_path, "wb") as f:
                f.write(png)

            # (3) 비전 모델로 표 → Markdown, 그림 → 영어 설명
            text = self.read_region(png, region)
            results.append({**region, "text": text, "image_path": image_path})

            # (4) 비전 결과가 있을 때만 캐시로 저장 (실패하면 다음 실행에 다시 시도)
            if text != region.get("text", ""):
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"bbox": list(region["bbox"]), "prompt_version": PROMPT_VERSION,
                               "text": text}, f, ensure_ascii=False, indent=2)
        doc.close()
        return results

    def read_region(self, png, region):
        """잘라 낸 이미지를 비전 모델로 읽는다. 실패하면 원래 글자를 돌려준다."""
        old_text = region.get("text", "")
        if self.llm is None:
            return old_text
        try:
            if region["type"] == "table":
                result = self.llm.vision(png, TABLE_TO_MARKDOWN, TableResult)["parsed"]
                return f"{result['markdown']}\nSummary: {result['summary']}" if result else old_text
            result = self.llm.vision(png, FIGURE_DESCRIBE, FigureResult)["parsed"]
            return result["description"] if result else old_text
        except (openai.OpenAIError, ValueError, OSError) as e:   # 한 영역이 실패해도 나머지는 계속
            print(f"[document_processor] 비전 실패 ({region['type']} {region['bbox']}): {e}")
            return old_text

    def load_crop_cache(self, cache_path, region):
        """캐시 파일이 있고 상자 위치·프롬프트 버전이 같으면 그 결과를 돌려준다."""
        if not os.path.exists(cache_path):
            return None
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
        same_box = all(abs(a - b) < 1 for a, b in zip(cached["bbox"], region["bbox"]))
        if same_box and cached.get("prompt_version") == PROMPT_VERSION:
            return cached
        return None

    def doc_id_of(self, pdf_path):
        """파일 이름으로 sources.csv 에서 doc_id 를 찾는다. (없으면 파일 이름) page_map 도 같이 기억해 둔다."""
        file_name = os.path.basename(pdf_path)
        source = self.sources.get(file_name, {})
        doc_id = source.get("doc_id") or os.path.splitext(file_name)[0]
        self.page_maps[doc_id] = source.get("page_map", "")
        return doc_id

    # ==========================================================================
    # 2. split_sections : list[Page] → list[Section]
    # ==========================================================================
    def split_sections(self, pages: list[Page]) -> list[Section]:
        """섹션 제목 기준으로 본문을 나눈다.

        - 제목 : 문서 본문 글자보다 1.25배 이상 큰 짧은 줄 (크기 순서로 단계를 정함)
        - section_path : "큰 제목 > 작은 제목"
        - 표·그림은 그 위치의 제목 아래에 type=table/figure 섹션으로 따로 넣는다
        - 페이지가 바뀌면 섹션도 끊는다 (출처 쪽을 정확히 남기려고)
        """
        if not pages:
            return []

        # (1) 문서 본문 글자 크기 = 가장 많이 쓰인 크기
        size_count = Counter()
        for page in pages:
            for line in self.lines_of(page):
                size_count[line["size"]] += len(line["text"])
        body_size = size_count.most_common(1)[0][0] if size_count else 10

        # (2) 제목 크기 목록 → 큰 것부터 1단계, 2단계, 3단계
        #     표지(첫 쪽)에만 나오는 큰 글씨는 제목 단계에서 뺀다 (책 제목이 모든 섹션의 맨 앞에 붙지 않게)
        cover = pages[0] if len(pages) > 3 and pages[0].page_no == 1 else None
        used_on = {}
        for page in pages:
            for line in self.lines_of(page):
                if self.is_heading(line, body_size):
                    used_on.setdefault(line["size"], set()).add(page.page_no)
        if cover is not None:
            used_on = {size: nos for size, nos in used_on.items() if nos != {cover.page_no}}
        heading_sizes = sorted(used_on, reverse=True)
        #     크기 차이가 10% 안쪽이면 같은 단계 (예: 16.0 과 15.8)
        level_of, level, top = {}, -1, None
        for size in heading_sizes:
            if top is None or size < top * 0.9:
                level, top = level + 1, size
            level_of[size] = min(level, 2)

        sections = []
        path = []                                  # 지금 위치의 제목들 [1단계, 2단계, 3단계]
        for page in pages:
            original_page = self.to_original_page(page.doc_id, page.page_no)
            items = self.page_items(page)          # 본문 줄 + 표·그림을 위→아래 순서로
            buffer = []                            # 아직 섹션으로 만들지 않은 본문 줄

            previous_heading = None
            for kind, item in items:
                if kind == "region":
                    text = item["text"]
                    # 표 바로 위의 짧은 대문자 줄은 표 제목으로 보고 표에 붙인다 (예: TROUBLE SHOOTING DRIVES)
                    if item["type"] == "table" and buffer and self.is_table_title(buffer[-1]["text"]):
                        text = buffer.pop()["text"] + "\n" + text
                    self.flush_text(sections, buffer, page.doc_id, path, original_page)
                    sections.append(Section(page.doc_id, " > ".join(path), original_page,
                                            item["type"], text, item.get("image_path")))
                    previous_heading = None
                elif self.is_heading(item, body_size) and item["size"] in level_of:
                    self.flush_text(sections, buffer, page.doc_id, path, original_page)
                    level = level_of[item["size"]]
                    if previous_heading is not None and previous_heading["size"] == item["size"] \
                            and item["bbox"][1] - previous_heading["bbox"][3] < item["size"] * 0.5:
                        # 두 줄로 나뉜 제목 ("Ceiling Hanger" + "Installation") 은 이어 붙인다
                        path[-1] = path[-1] + " " + self.clean_heading(item["text"])
                    else:
                        path = path[:level] + [self.clean_heading(item["text"])]
                    previous_heading = item
                else:
                    buffer.append(item)
                    previous_heading = None
            self.flush_text(sections, buffer, page.doc_id, path, original_page)

        # 글 섹션은 빈 것을 빼고, 표·그림은 글자가 없어도 남긴다 (crop_regions 이 채움)
        return [s for s in sections if s.type != "text" or s.text.strip()]

    def flush_text(self, sections, buffer, doc_id, path, page):
        """모아 둔 본문 줄을 text 섹션 1개로 만들어 sections 에 넣고 buffer 를 비운다."""
        text = self.join_lines(buffer)
        if text:
            sections.append(Section(doc_id, " > ".join(path), page, "text", text))
        buffer.clear()

    def page_items(self, page):
        """본문 줄과 표·그림을 하나의 순서로 합친다.

        표·그림은 그 영역 바로 위에 있는 줄 다음에 끼워 넣는다.
        """
        lines = list(self.lines_of(page))
        items = [("line", line) for line in lines]
        for region in page.regions:
            top = region["bbox"][1]
            index = len(items)
            for i, (kind, item) in enumerate(items):
                if kind == "line" and item["bbox"][1] > top and \
                        self.same_column(item["bbox"], region["bbox"]):
                    index = i
                    break
            items.insert(index, ("region", region))
        return items

    # ==========================================================================
    # 도우미
    # ==========================================================================
    def lines_of(self, page):
        """extract_text 때 저장한 줄 목록. 없으면 page.text 를 같은 크기 줄로 본다."""
        saved = self.page_lines.get((page.doc_id, page.page_no))
        if saved is not None:
            return saved
        return [{"text": t, "size": 10, "bbox": (0, i * 12, 1, i * 12 + 10)}
                for i, t in enumerate(page.text.splitlines()) if t.strip()]

    def is_heading(self, line, body_size):
        """큰 글씨의 짧은 줄이면 제목으로 본다."""
        text = line["text"].strip("● ").strip()
        return (line["size"] >= body_size * 1.25
                and 2 <= len(text) <= 80
                and not text[0].islower()               # 소문자로 시작하면 문장 중간
                and not re.fullmatch(r"[\d\s.]+", text))

    def is_table_title(self, text):
        """짧은 대문자 줄이면 표 제목으로 본다."""
        return len(text) <= 60 and text.upper() == text and re.search(r"[A-Z]", text) is not None

    def clean_heading(self, text):
        """제목 앞 기호(●)와 겹친 공백, 글자 사이 띄어쓰기(Pr e c a u t i o n s)를 정리한다."""
        text = re.sub(r"\s+", " ", text.strip("● ")).strip()
        return re.sub(r"\b([A-Z][a-z]?)?\s?((?:[A-Za-z] ){3,}[A-Za-z])\b",
                      lambda m: (m.group(1) or "") + m.group(2).replace(" ", ""), text)

    def is_spanish(self, text):
        """스페인어 줄이면 True. (스페인어 특수 글자 또는 스페인어 단어 비율 25% 이상)"""
        if any(c in SPANISH_CHARS for c in text.lower()):
            return True
        words = re.findall(r"[a-záéíóúñ]{2,}|\by\b", text.lower())   # 한 글자 조각(A m b i e n t e) 은 뺌
        if len(words) < 3:
            return False
        return sum(w in SPANISH_WORDS for w in words) / len(words) >= 0.25

    def is_spanish_block(self, text):
        """표 전체가 스페인어인지. 머리글 줄이 스페인어이거나 줄의 40% 이상이 스페인어면 True"""
        rows = [r for r in text.splitlines() if r.strip() and not r.startswith("|---")]
        if not rows:
            return False
        return self.is_spanish(rows[0]) or sum(self.is_spanish(r) for r in rows) / len(rows) >= 0.4

    def drop_spanish_text(self, text):
        """여러 줄 글자에서 스페인어 줄만 뺀다. (그림 안 설명 글자용)"""
        return "\n".join(ln for ln in text.splitlines() if ln.strip() and not self.is_spanish(ln))

    def join_lines(self, lines):
        """줄들을 문단으로 이어 붙인다. (줄 끝 '-' 로 끊긴 단어는 붙인다)"""
        text = ""
        previous = None
        for line in lines:
            t = line["text"]
            if previous is None:
                text = t
            elif line["bbox"][1] - previous["bbox"][3] > previous["size"] * 1.2:
                text += "\n" + t                  # 줄 간격이 크면 새 문단
            elif text.endswith("-") and t[:1].islower():
                text = text[:-1] + t               # ceil- / ing → ceiling
            else:
                text += " " + t
            previous = line
        return text.strip()

    def cell_text(self, pdf_page, cell, gray_boxes=()):
        """표 칸 안의 글자 (table.extract() 는 띄어쓰기가 사라지는 PDF 가 있어서 직접 읽음)

        글자가 없고 회색으로 칠해진 칸은 "X" 로 돌려준다.
        """
        if cell is None:                           # 합쳐진 칸
            return ""
        box = pymupdf.Rect(cell)
        text = pdf_page.get_text("text", clip=box)
        if not text.strip():
            filled = sum((box & g).get_area() for g in gray_boxes if box.intersects(g))
            return "X" if box.get_area() > 0 and filled / box.get_area() >= 0.6 else ""
        text = re.sub(r"-\n(?=[a-z])", "", text)  # fre-\nquently → frequently
        return re.sub(r"\s+", " ", text).strip()

    def rows_to_markdown(self, rows):
        """표 행 목록 → Markdown 표 (crop_regions 결과가 생기기 전 임시 글자)"""
        clean = [[(c or "").replace("|", "/") for c in row] for row in rows]
        # 모든 행에서 비어 있는 칸(줄)은 뺀다 (표 테두리 선 때문에 생기는 빈 칸)
        keep = [i for i in range(len(clean[0])) if any(i < len(r) and r[i] for r in clean)]
        clean = [[r[i] if i < len(r) else "" for i in keep] for r in clean]
        lines = ["| " + " | ".join(clean[0]) + " |", "|" + "---|" * len(clean[0])]
        lines += ["| " + " | ".join(row) + " |" for row in clean[1:]]
        return "\n".join(lines)

    def merge_boxes(self, boxes):
        """겹치는 상자를 하나로 합친다. (그림 하나가 여러 조각으로 나뉜 경우)"""
        boxes = [pymupdf.Rect(b) for b in boxes]
        merged = True
        while merged:
            merged = False
            for i in range(len(boxes)):
                for j in range(i + 1, len(boxes)):
                    if (boxes[i] + (-NEAR, -NEAR, NEAR, NEAR)).intersects(boxes[j]):
                        boxes[i] = boxes[i] | boxes[j]
                        del boxes[j]
                        merged = True
                        break
                if merged:
                    break
        return boxes

    def is_visible(self, drawing):
        """눈에 보이는 도형인지.
        선 없이 흰색으로만 칠한 '사각형'(글자 뒤 배경 상자)은 안 보이는 것으로 본다.
        흰색 곡선(아이콘 안쪽 무늬 등)은 그림의 일부라 남긴다."""
        white = (1.0, 1.0, 1.0)
        stroke = drawing.get("color")
        fill = drawing.get("fill")
        only_rects = all(item[0] == "re" for item in drawing.get("items", []))
        background = (stroke is None or tuple(stroke) == white) and fill is not None \
            and tuple(fill) == white and only_rects
        return not background

    def is_gray(self, color):
        """회색 채우기인지 (흰색·검정·유채색은 아님)"""
        if not color or len(color) != 3:
            return False
        r, g, b = color
        return max(r, g, b) - min(r, g, b) < 0.05 and 0.3 < r < 0.95

    def text_ratio(self, box, text_boxes):
        """상자 넓이 중 글자 블록이 차지하는 비율 (0~1)"""
        area = box.width * box.height
        if area <= 0:
            return 1.0
        covered = sum((box & t).get_area() for t in text_boxes if box.intersects(t))
        return min(covered / area, 1.0)

    def same_column(self, a, b):
        """두 상자가 가로로 겹치면 같은 단으로 본다."""
        return a[0] < b[2] and b[0] < a[2]

    def to_original_page(self, doc_id, page_no):
        """PDF 파일 쪽 → 원본 매뉴얼 쪽 (sources.csv page_map)

        page_map 예) "1-20>21"         : 파일 1~20쪽 = 원본 21쪽부터
                     "1-24>6;25-31>54" : 파일 1~24쪽 = 원본 6쪽부터, 25~31쪽 = 원본 54쪽부터
                     ""                : 쪽 번호 그대로
        """
        for part in (self.page_maps.get(doc_id) or "").split(";"):
            m = re.fullmatch(r"\s*(\d+)-(\d+)>(\d+)\s*", part)
            if m:
                start, end, original_start = map(int, m.groups())
                if start <= page_no <= end:
                    return original_start + (page_no - start)
        return page_no


class KbBuilder:
    """매뉴얼의 트러블슈팅 표·점검표를 KB(dict)로 바꾼다. (담당: 김민재)

    [규칙]
      - 문장은 매뉴얼 원문 그대로 쓴다 (요약·번역 금지). 답변 검증이 원문 부분 문자열인지로 확인하기 때문.
      - 표 모양이 매뉴얼마다 달라서, 매뉴얼별로 다른 부분은 호출하는 쪽(scripts/build_kb.py)이
        표준 행 모양으로 바꿔서 넘긴다. 이 클래스는 표준 행 → KB 변환만 한다.
      - kb_id = {TS|MT}-{문서 코드}-T{표 번호 2자리}-{표 안 순번 2자리} (예: TS-HYTROL138ACC-T01-01)
    """

    # 표 칸 안의 번호 목록 "1) ..." / "1. ..." 의 번호 자리
    NUMBER_MARK = re.compile(r"(?:^|\s)(\d+)[.)]\s+")
    # "1500 to 3000 Operating hours", "every 10000 operating hours", "Every 2,000 h" 의 시간 부분
    HOURS = re.compile(r"(\d[\d,]*)(?:\s*(?:to|-)\s*(\d[\d,]*))?\s*(?:operating\s+)?(?:hours?|h)\b", re.I)
    # 표 제목 줄 "Table 8. Troubleshooting chart" 의 표 번호 부분 ("Table 8")
    CAPTION = re.compile(r"((?:Table|Tab\.)\s*\d+)(?:\s*[.:]|\s+(?=[A-Z]))")
    INTERVAL_WORDS = ("daily", "weekly", "monthly", "quarterly", "yearly")
    MAINTENANCE_KEYS = {"task", "interval", "interval_note", "criteria", "details", "lockout_required"}
    # from_sections 가 트러블슈팅 표로 알아보는 3칸 머리글 (증상 | 원인 | 조치)
    TROUBLE_HEADER = (re.compile(r"TROUBLE|PROBLEM|SYMPTOM|FAULT", re.I),
                      re.compile(r"CAUSE", re.I),
                      re.compile(r"SOLUTION|REMEDY|ACTION|CORRECTIVE", re.I))
    CHECK_MARKS = {"X", "x", "✓", "✔", "●"}  # 점검표에서 "이 주기에 한다" 표시

    def __init__(self, models_path: str = "data/models.csv"):
        """
        받는 것:
          models_path : models.csv 경로. doc_id 로 그 매뉴얼의 모델 목록(applies_to)을 찾는다.
        """
        # {doc_id: [모델명, ...]}. load_models 는 별칭도 키로 넣으므로 모델명 기준으로 한 번씩만 담는다.
        self.doc_models: dict[str, list[str]] = {}
        for model in Glossary().load_models(models_path).values():
            for doc_id in model.doc_ids:
                names = self.doc_models.setdefault(doc_id, [])
                if model.model_name not in names:
                    names.append(model.model_name)

    def to_troubleshooting_kb(self, rows: list, doc_id: str, page: int, table_no: int, ref: str):
        """
        트러블슈팅 표(증상 → 원인 → 조치)를 KB JSON으로 변환하고 원인마다 id(kb_id#c1…) 부여

        받는 것:
          rows     : 표의 데이터 행 [증상, 원인, 조치] 목록 (머리글 행은 빼고)
                     - 증상·조치 칸이 비어 있으면 병합 칸으로 보고 위 행 값을 이어받는다
                     - 원인 칸이 "1) A 2) B" 처럼 번호 목록이면 원인을 나누고, 조치도 같은 번호끼리 짝짓는다
                     - 조치 칸에 list[str] 를 넣으면 이미 나눈 조치 목록으로 그대로 쓴다
          doc_id   : sources.csv 의 doc_id
          page     : 원본 매뉴얼 쪽 (PdfExtractor.to_original_page 로 바꾼 값)
          table_no : 이 매뉴얼에서 KB로 만드는 표의 순번 (kb_id 의 T{nn})
          ref      : 출처 이름 (표 제목 등. 예: "TROUBLE SHOOTING DRIVES")
        증상 1개 = KB 1건.
        Returns: list[dict]
        """
        groups: list[tuple[str, list]] = []  # [(증상, [(원인, [조치, ...]), ...]), ...]
        solution = None
        for row_no, row in enumerate(rows, start=1):
            symptom, cause, sol = (c.strip() if isinstance(c, str) else c for c in row)
            if symptom:
                groups.append((symptom, []))
            elif not groups:
                raise ValueError(f"{ref} {row_no}행: 첫 행의 증상이 비어 있음")
            if not cause:
                raise ValueError(f"{ref} {row_no}행: 원인이 비어 있음")
            if sol:
                solution = sol
            elif symptom:
                # 조치를 이어받는 것은 같은 증상 안에서만 (다른 증상의 조치가 섞이지 않게)
                raise ValueError(f"{ref} {row_no}행: 조치가 비어 있음")
            groups[-1][1].extend(self._pair_causes(cause, solution, f"{ref} {row_no}행"))

        kbs = []
        for n, (symptom, pairs) in enumerate(groups, start=1):
            kb_id = self._kb_id("TS", doc_id, table_no, n)
            kb = TroubleshootingKB(
                type="troubleshooting", kb_id=kb_id, applies_to=self._applies_to(doc_id),
                sources=[SourceRef(doc_id=doc_id, page=page, ref=ref)], symptom=symptom,
                causes=[KbCause(cause_id=f"{kb_id}#c{i}", cause=c, actions=a)
                        for i, (c, a) in enumerate(pairs, start=1)])
            # pydantic 모델로 한 번 만들어서 스키마에 안 맞으면 여기서 바로 오류가 나게 한다
            kbs.append(kb.model_dump(mode="json", exclude_none=True))
        return kbs

    def to_maintenance_kb(self, rows: list, doc_id: str, page: int, table_no: int, ref: str):
        """
        점검표(일간·주간·월간·분기 또는 운전 시간)를 점검 KB로 변환

        받는 것:
          rows : 점검 항목 dict 목록. 행 1개 = KB 1건
                 {"task": 작업(원문),
                  "interval": 주기 원문 ("Weekly", "1500 to 3000 Operating hours", "At least every 10 years" ...),
                  "interval_note": 주기 보충 원문 (선택),
                  "criteria": 판정 기준 (선택), "details": [보충 정보] (선택),
                  "lockout_required": 작업 전 전원 잠금 필요 여부 (선택, 기본 True)}
          doc_id · page · table_no · ref : to_troubleshooting_kb 와 같음
        Returns: list[dict]
        """
        kbs = []
        for n, row in enumerate(rows, start=1):
            unknown = set(row) - self.MAINTENANCE_KEYS
            if unknown:  # 키 오타가 조용히 무시되지 않게
                raise ValueError(f"{ref} {n}행: 모르는 키 {sorted(unknown)}")
            kb = MaintenanceKB(
                type="maintenance", kb_id=self._kb_id("MT", doc_id, table_no, n),
                applies_to=self._applies_to(doc_id),
                sources=[SourceRef(doc_id=doc_id, page=page, ref=ref)], task=row["task"].strip(),
                **self._parse_interval(row["interval"], row.get("interval_note")),
                criteria=row.get("criteria"), details=row.get("details", []),
                # 매뉴얼 점검 절의 안전 규칙(전원 차단 후 작업)을 따라 기본은 True. 운전 중 점검만 False 로 넘긴다
                lockout_required=row.get("lockout_required", True))
            kbs.append(kb.model_dump(mode="json", exclude_none=True))
        return kbs

    def from_sections(self, sections: list[Section]):
        """
        새 PDF(관리자 추가)의 표 섹션 중 형식을 알아볼 수 있는 표만 자동으로 KB 변환

        받는 것:
          sections : PdfExtractor.split_sections 결과. crop_regions 를 거치기 전 것이어야 한다
                     (비전 모델이 다시 쓴 표 글자는 원문과 달라질 수 있는데, KB 문장은 원문 그대로여야 하므로)
        자동 변환하는 표:
          - 트러블슈팅 : 머리글 3칸 (TROUBLE·PROBLEM·SYMPTOM·FAULT | …CAUSE… | SOLUTION·REMEDY·ACTION·CORRECTIVE)
          - 점검표     : 주기 머리글(Daily~Yearly, "Every 2,000 h") 칸에 "X" 표시.
                         lockout_required 는 모두 True (안전한 쪽). 오른쪽 Note 같은 칸은 details 로
        정할 값:
          - table_no : KB로 바뀐 표만 문서마다 순서대로 1, 2, 3 …
          - ref      : 표 위 대문자 제목 → "Table 8" 같은 표 번호 → 절 제목 → 머리글 행 (그 쪽에 실제로 적힌 글자만)
        변환하지 못한 표는 건너뛴다 (표 청크로 검색은 됨). 표 하나 때문에 색인 전체가 실패하지 않게.
        Fenner 처럼 답이 다른 쪽에 있는 표·글 목록은 못 바꾸므로 scripts/build_kb.py 에 추가해야 한다.
        Returns: list[dict]
        """
        kbs, table_no = [], {}  # table_no = {doc_id: 마지막 표 번호}
        captions = self._captions(sections)
        for i, section in enumerate(sections):
            if section.type != "table":
                continue
            title, rows = self._table_of(section.text)
            if len(rows) < 2:
                continue
            heading = section.section_path.split(" > ")[-1]
            ref = title or captions.get(i) or heading or " / ".join(c for c in rows[0] if c)
            n = table_no.get(section.doc_id, 0) + 1
            try:
                if self._is_trouble_header(rows[0]):
                    new = self.to_troubleshooting_kb(rows[1:], section.doc_id, section.page, n, ref)
                else:
                    checklist = self._checklist_rows(rows)
                    if not checklist:
                        continue
                    new = self.to_maintenance_kb(checklist, section.doc_id, section.page, n, ref)
            except ValueError as e:  # pydantic ValidationError 도 ValueError 의 한 종류
                print(f"[KbBuilder] KB 변환 건너뜀 ({section.doc_id} p{section.page} {ref}): {e}")
                continue
            table_no[section.doc_id] = n
            kbs += new
        return kbs

    def _table_of(self, text):
        """표 섹션 글자 → (표 제목 또는 "", 칸 목록의 목록).
        split_sections 는 표 제목을 "제목\\n| … |" 처럼 첫 줄에 붙인다. 칸 안의 "|" 는 "/" 로 바뀌어 있다."""
        title, rows = "", []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("|"):
                if not line.startswith("|---"):
                    rows.append([c.strip() for c in line[1:-1].split("|")])
            elif not rows and line:
                title = line
        return title, rows

    def _captions(self, sections):
        """{표 섹션 위치: "Table 8"} — 매뉴얼의 "Table 8. Troubleshooting chart" 같은 표 제목에서 표 번호 부분

        split_sections 는 표 바로 위의 대문자 제목만 표에 붙이므로, "Table N. …" 형식 제목은 여기서 찾는다.
        - 본문 섹션은 가까운 줄을 한 줄로 이어 붙여서 제목이 어디서 끝나는지 알 수 없다
          ("Table 8. Troubleshooting chart 6.1 VFD alarm codes"). 그래서 표 번호 부분만 쓴다.
          매뉴얼 본문도 "follow Table 9" 처럼 표 번호로 가리키므로 찾기에 충분하다.
        - 표가 연달아 있으면 두 표 사이의 "Table 5. …" 가 위 표 것(아래 붙는 제목)인지
          아래 표 것(위에 붙는 제목)인지 그 자리만 봐서는 알 수 없다.
          그래서 문서마다 두 방식으로 다 찾아보고, 더 많이 들어맞는 방식 하나로 정한다.
        """
        def caption(j, para_no):  # j번 본문 섹션의 첫 문단(0)/끝 문단(-1)이 표 제목으로 시작하면 "Table N"
            if sections[j].type == "text":
                paragraphs = sections[j].text.strip().split("\n")
                m = self.CAPTION.match(paragraphs[para_no].strip())
                if m:
                    return m.group(1)
            return None

        below, above = {}, {}
        for i, s in enumerate(sections):
            if s.type != "table":
                continue
            for j, line_no, found in ((i + 1, 0, below), (i - 1, -1, above)):
                if 0 <= j < len(sections) and (sections[j].doc_id, sections[j].page) == (s.doc_id, s.page):
                    text = caption(j, line_no)
                    if text:
                        found[i] = text
        result = {}
        for doc_id in {s.doc_id for s in sections}:
            b = {i: c for i, c in below.items() if sections[i].doc_id == doc_id}
            a = {i: c for i, c in above.items() if sections[i].doc_id == doc_id}
            result.update(b if len(b) >= len(a) else a)
        return result

    def _is_trouble_header(self, header):
        cells = [c for c in header if c]
        return len(header) == 3 and len(cells) == 3 and all(p.search(c) for p, c in zip(self.TROUBLE_HEADER, cells))

    def _checklist_rows(self, rows):
        """주기 칸에 X 표시가 있는 점검표 → to_maintenance_kb 행 목록. 점검표가 아니면 [].

        Hytrol 점검표처럼 머리글이 2줄(SCHEDULE 아래 Weekly·Monthly…)일 수 있어서 위 2줄에서 주기 머리글을 찾는다.
        주기 머리글 = Daily~Yearly 또는 운전 시간("Every 2,000 h").
        주기 칸 왼쪽 칸들이 작업이고, 여러 칸이면 ": " 로 잇는다 ("MOTOR: Check Noise").
        첫 칸이 비어 있으면 병합 칸으로 보고 위 행 값을 이어받는다.
        주기 칸 오른쪽 칸(Note 등)은 버리지 않고 "칸 이름: 값" 으로 details 에 넣는다 (판정 기준이 들어 있는 경우가 많음).
        """
        for head_no, header in enumerate(rows[:2]):
            columns = {i: c for i, c in enumerate(header) if self._is_interval_header(c)}
            if columns:
                break
        else:
            return []
        first, last = min(columns), max(columns)
        if first == 0:
            return []
        # 오른쪽 칸 이름: 그 줄이 비어 있으면 첫 머리글 줄에서 (2줄 머리글)
        extra = {i: header[i] or rows[0][i] for i in range(last + 1, len(header))}
        result, above = [], ""
        for cells in rows[head_no + 1:]:
            task_cells = cells[:first]
            above = task_cells[0] or above
            task = ": ".join(c for c in [above, *task_cells[1:]] if c)
            details = [f"{name}: {cells[i]}" if name else cells[i]
                       for i, name in extra.items() if i < len(cells) and cells[i]]
            for i, word in columns.items():
                if i < len(cells) and cells[i] in self.CHECK_MARKS and task:
                    result.append({"task": task, "interval": word, "details": details})
        return result

    def _is_interval_header(self, cell):
        cell = cell.strip()
        return cell.lower() in self.INTERVAL_WORDS or bool(re.fullmatch(r"(?:every\s+)?" + self.HOURS.pattern, cell, re.I))

    def _kb_id(self, prefix, doc_id, table_no, n):
        # 문서 코드 = doc_id 를 대문자로, 영문·숫자만 (hytrol_138acc → HYTROL138ACC)
        code = re.sub(r"[^A-Z0-9]", "", doc_id.upper())
        return f"{prefix}-{code}-T{table_no:02d}-{n:02d}"

    def _applies_to(self, doc_id):
        # models.csv 에 이 문서의 모델이 없으면 특정 모델용이 아닌 공통 매뉴얼(예: Fenner 벨트 매뉴얼)로 보고 "*".
        # 설비 필터를 걸어도 공통 매뉴얼 KB는 항상 검색되게 하려는 것.
        return list(self.doc_models.get(doc_id) or [ALL_MODELS])

    def _split_numbered(self, text):
        """ "1) A 2) B" · "1. A; 2. B" → ["A", "B"]. 번호 목록이 아니면 [text].

        1 로 시작해서 1, 2, 3 … 차례로 이어지는 번호만 나눈다.
        글 중간의 다른 숫자("See section 13. page")에서 잘못 나누지 않게 하려는 것.
        """
        marks, expected = [], 1
        for m in self.NUMBER_MARK.finditer(text):
            if int(m.group(1)) == expected and (marks or m.start() == 0):
                marks.append(m)
                expected += 1
        if not marks:
            return [text]
        ends = [m.start() for m in marks[1:]] + [len(text)]
        # 끝의 ";" 는 목록 구분자라 뺀다 (남은 글자는 원문의 부분 문자열 그대로)
        return [text[m.end():end].strip().rstrip(";").strip() for m, end in zip(marks, ends)]

    def _pair_causes(self, cause, solution, where):
        """원인 칸·조치 칸 → [(원인, [조치, ...]), ...]"""
        causes = self._split_numbered(cause)
        if isinstance(solution, list):  # 호출하는 쪽에서 이미 나눈 조치 목록
            if len(causes) != 1:
                raise ValueError(f"{where}: 조치를 목록으로 넘길 때는 원인이 1개여야 함")
            return [(causes[0], solution)]
        actions = self._split_numbered(solution)
        if len(causes) == 1:
            # 원인 1개에 번호 붙은 조치 여러 개 = 차례로 할 조치 단계 (PRECO "1. Check …; 2. If necessary …")
            return [(causes[0], actions)]
        if len(actions) != len(causes):
            # Hytrol 표는 원인 n) 과 조치 n) 이 짝. 개수가 다르면 어느 조치가 어느 원인 것인지 알 수 없다
            raise ValueError(f"{where}: 원인 {len(causes)}개, 조치 {len(actions)}개로 번호가 맞지 않음")
        return [(c, [a]) for c, a in zip(causes, actions)]

    def _parse_interval(self, text, note=None):
        """주기 원문 → MaintenanceKB 의 interval 필드들"""
        text = text.strip()
        if text.lower() in self.INTERVAL_WORDS:  # "Weekly", "DAILY"
            return {"interval": text.lower(), "interval_note": note}
        m = self.HOURS.search(text)
        if m:
            low = int(m.group(1).replace(",", ""))
            high = int(m.group(2).replace(",", "")) if m.group(2) else None
            rest = text.replace(m.group(0), "").strip(" ,").lower()
            if note is None and rest not in ("", "every"):
                # 시간 말고 다른 조건이 함께 있으면 (예: "…, at least every 2 years") 원문 전체를 보충으로 남긴다
                note = text
            return {"interval": "operating_hours", "interval_hours": low, "interval_hours_max": high,
                    "interval_note": note}
        # daily~yearly·운전 시간으로 나타낼 수 없는 주기는 비슷한 값으로 바꾸지 않고 원문을 남긴다
        return {"interval": "other", "interval_note": note or text}

    def merge_applies_to(self, kbs: list[dict]):
        """
        같은 표가 여러 모델에 반복되면 1건으로 합치고 applies_to에 모델 목록 기록

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.merge_applies_to")

    def validate_kb(self, kb: dict, source_text: str):
        """
        필수 항목, id 중복, 인용문이 원문의 부분 문자열인지 검사

        Returns: bool, list[str]
        """
        raise NotImplementedError("KbBuilder.validate_kb")