"""
==============================================================================
 document_processor.py : PDF → 페이지 → 섹션  (PdfExtractor 담당: 박세람)
==============================================================================

[이 파일이 하는 일]  기능명세서 D-01
  1) extract_text   : PDF 에서 페이지별 영어 본문 + 표·그림 위치(bbox)를 뽑는다
  2) split_sections : 섹션 제목(큰 글씨)을 기준으로 본문을 나누고, 표·그림도 섹션으로 넣는다

  (crop_regions 는 다음 작업, KbBuilder 는 김민재 담당)

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
    sections = ex.split_sections(pages)
==============================================================================
"""
from __future__ import annotations

import csv
import os
import re
from collections import Counter

import pymupdf

from chatbot.core.schemas import Page, Section

# 스페인어 줄인지 판단할 때 쓰는 말 (영어에는 거의 안 나오는 짧은 단어)
SPANISH_WORDS = {"de", "la", "el", "los", "las", "del", "para", "con", "una", "por",
                 "que", "se", "en", "y", "es", "al", "su", "sus", "como", "lo", "un"}
SPANISH_CHARS = set("áéíóúñ¿¡")

MARGIN = 0.07          # 위·아래 7% 는 머리말·쪽번호 영역으로 본다
MIN_FIGURE_AREA = 0.03  # 페이지 넓이의 3% 보다 작은 그림·도형 묶음은 그림으로 보지 않는다
TEXT_HEAVY = 0.35       # 도형 묶음 넓이의 35% 이상이 글자면 그림이 아니라 글 상자로 본다
MIN_PATHS = 6           # 선·도형이 6개보다 적으면 단순 테두리로 본다
LABEL_MAX = 40          # 그림 안에서 이보다 짧은 줄은 이름표로 본다
NEAR = 8                # 8pt 안으로 붙어 있는 조각은 같은 그림으로 합친다


class PdfExtractor:
    """PDF 에서 본문·표·그림 위치를 뽑고 섹션으로 나눈다."""

    def __init__(self, sources_path="data/sources.csv"):
        """
        받는 것:
          sources_path : sources.csv 경로. 파일 이름으로 doc_id 와 page_map 을 찾는다.
                         목록에 없는 PDF(관리자가 추가한 PDF)는 파일 이름이 doc_id, 쪽은 그대로.
        """
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
        file_name = os.path.basename(pdf_path)
        source = self.sources.get(file_name, {})
        doc_id = source.get("doc_id") or os.path.splitext(file_name)[0]
        self.page_maps[doc_id] = source.get("page_map", "")

        doc = pymupdf.open(pdf_path)
        if pages is None:
            pages = range(1, doc.page_count + 1)

        result = []
        for page_no in pages:
            pdf_page = doc[page_no - 1]
            regions = self.find_regions(pdf_page)
            lines = self.read_lines(pdf_page, regions)
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

    def find_regions(self, pdf_page):
        """표와 그림 영역(bbox)을 찾는다."""
        page_area = pdf_page.rect.width * pdf_page.rect.height
        regions = []

        # (1) 표 : PyMuPDF 의 표 찾기 (선으로 그려진 표)
        for table in pdf_page.find_tables().tables:
            rows = [[self.cell_text(pdf_page, cell) for cell in row.cells] for row in table.rows]
            if len(rows) < 2:                      # 1줄짜리는 표가 아님 (상자 테두리 등)
                continue
            regions.append({"type": "table", "bbox": tuple(table.bbox),
                            "text": self.rows_to_markdown(rows), "image_path": None})

        # (2) 그림 : 사진(이미지) + 선으로 그린 도면(도형 묶음)
        #     도형 묶음 중 글자로 꽉 찬 것(문단 테두리, 제목 배경, NOTE 상자)은 그림이 아니다
        text_boxes = [pymupdf.Rect(b[:4]) for b in pdf_page.get_text("blocks")]
        boxes = [pymupdf.Rect(info["bbox"]) for info in pdf_page.get_image_info()]
        try:
            drawings = pdf_page.cluster_drawings()
        except (ValueError, RuntimeError):
            drawings = []
        paths = [pymupdf.Rect(d["rect"]) for d in pdf_page.get_drawings()]
        for box in drawings:
            if self.text_ratio(box, text_boxes) >= TEXT_HEAVY:
                continue                           # 글자로 꽉 찬 상자
            if sum(1 for r in paths if r in box + (-1, -1, 1, 1)) < MIN_PATHS:
                continue                           # 선 몇 개짜리 테두리 (제목 배경, 각주 상자)
            boxes.append(box)

        for box in self.merge_boxes(boxes):
            area = box.width * box.height
            if area < page_area * MIN_FIGURE_AREA or area > page_area * 0.9:
                continue                           # 너무 작은 조각, 페이지 전체 테두리는 뺌
            if any(box.intersects(pymupdf.Rect(r["bbox"])) for r in regions):
                continue                           # 표 테두리 선은 그림이 아님
            labels = [ln for ln in pdf_page.get_text("text", clip=box).splitlines()
                      if ln.strip() and len(ln.strip()) < LABEL_MAX]
            regions.append({"type": "figure", "bbox": tuple(box),
                            "text": self.drop_spanish_text("\n".join(labels)), "image_path": None})

        regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
        return regions

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
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if not text:
                    continue
                box = pymupdf.Rect(line["bbox"])
                # 표 안 줄은 뺀다. 그림 안 줄은 짧은 이름표(부품명·번호)만 뺀다
                #   (그림 영역이 크게 잡혀도 본문 문장은 본문에 남도록)
                center = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
                if any(center in r for r in table_boxes):
                    continue
                if len(text) < LABEL_MAX and any(center in r for r in figure_boxes):
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

        Returns: list[dict]
        """
        raise NotImplementedError("PdfExtractor.crop_regions")

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
        words = re.findall(r"[a-záéíóúñ]+", text.lower())
        if len(words) < 3:
            return False
        return sum(w in SPANISH_WORDS for w in words) / len(words) >= 0.25

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

    def cell_text(self, pdf_page, cell):
        """표 칸 안의 글자 (table.extract() 는 띄어쓰기가 사라지는 PDF 가 있어서 직접 읽음)"""
        if cell is None:                           # 합쳐진 칸
            return ""
        text = pdf_page.get_text("text", clip=pymupdf.Rect(cell))
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
    """KbBuilder (기능명세서 기준 뼈대)."""

    def to_troubleshooting_kb(self, rows: list, doc_id: str, page: int):
        """
        트러블슈팅 표(증상 → 원인 → 조치)를 KB JSON으로 변환하고 원인마다 id(kb_id#c1…) 부여

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.to_troubleshooting_kb")

    def to_maintenance_kb(self, rows: list, doc_id: str, page: int):
        """
        점검표(일간·주간·월간·분기 또는 운전 시간)를 점검 KB로 변환

        Returns: list[dict]
        """
        raise NotImplementedError("KbBuilder.to_maintenance_kb")

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