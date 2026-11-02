"""
glossary.py 테스트 (Glossary.load_glossary · load_models)

- 저장소의 data/glossary.csv · data/models.csv 가 규칙대로 읽히는지 확인한다
- 잘못 적은 csv 는 tmp_path 에 직접 만들어서 오류가 나는지 확인한다
실행: python -m pytest tests/glossary_test.py -v
"""
import csv
import os

import pytest

from chatbot.rag.glossary import Glossary

GLOSSARY = os.path.join("data", "glossary.csv")
MODELS = os.path.join("data", "models.csv")
SOURCES = os.path.join("data", "sources.csv")


def write_csv(path, text, bom=False):
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------------------
# load_glossary
# ------------------------------------------------------------------------------
def test_repo_glossary_load():
    glossary = Glossary().load_glossary(GLOSSARY)
    # 현장 표현과 표준어 모두 키가 된다
    assert "Belt slips" in glossary["벨트 미끄러짐"]
    assert "Belt slips" in glossary["벨트 슬립"]
    # 모든 키에 영문 용어가 1개 이상
    assert all(terms for terms in glossary.values())


def test_glossary_merge_same_key(tmp_path):
    # 표준어가 같은 두 행 → 표준어 키에 영문 용어가 합쳐지고 중복은 한 번만
    path = write_csv(tmp_path / "g.csv",
                     "field_term,standard_ko,term_en\n"
                     "쏠림,벨트 사행,Belt runs off;Belt steers off\n"
                     "사행,벨트 사행,Belt steers off;Belt misalignment\n", bom=True)
    glossary = Glossary().load_glossary(path)
    assert glossary["벨트 사행"] == ["Belt runs off", "Belt steers off", "Belt misalignment"]
    assert glossary["쏠림"] == ["Belt runs off", "Belt steers off"]


def test_glossary_empty_standard_ko(tmp_path):
    # 표준어 칸이 비어도 현장 표현만으로 읽힌다
    path = write_csv(tmp_path / "g.csv", "field_term,standard_ko,term_en\n슬립,,Belt slips\n")
    assert Glossary().load_glossary(path) == {"슬립": ["Belt slips"]}


@pytest.mark.parametrize("row, message", [
    (",벨트 슬립,Belt slips", "field_term이 비어 있음"),
    ("슬립,벨트 슬립,", "term_en이 비어 있음"),
])
def test_glossary_bad_rows(tmp_path, row, message):
    path = write_csv(tmp_path / "g.csv", "field_term,standard_ko,term_en\n" + row + "\n")
    with pytest.raises(ValueError, match=message):
        Glossary().load_glossary(path)


# ------------------------------------------------------------------------------
# 저장소의 models.csv
# ------------------------------------------------------------------------------
def test_repo_models_load():
    models = Glossary().load_models(MODELS)
    # 모델명과 별칭이 같은 Model 을 가리킨다
    assert models["138-ACC"] is models["ACC"]
    assert models["138-ACC"].doc_ids == ["hytrol_138acc"]
    assert models["L400"].aliases == []
    assert {m.model_name for m in models.values()} == {
        "138-ACC", "138-LRC", "138-LRS", "138-LRSS", "L400", "L500", "L650"}


def test_repo_models_doc_ids_exist_in_sources():
    # models.csv 의 doc_ids 는 sources.csv 에 있는 문서여야 한다 (오타 방지)
    with open(SOURCES, encoding="utf-8-sig") as f:
        doc_ids = {row["doc_id"] for row in csv.DictReader(f)}
    for model in Glossary().load_models(MODELS).values():
        assert set(model.doc_ids) <= doc_ids, model


# ------------------------------------------------------------------------------
# 칸 나누기·BOM
# ------------------------------------------------------------------------------
def test_list_cells_split_and_bom(tmp_path):
    path = write_csv(tmp_path / "m.csv",
                     "model_name,aliases,doc_ids\n X-1 , a; b;;c ,d1;d2\n", bom=True)
    m = Glossary().load_models(path)["X-1"]
    assert m.aliases == ["a", "b", "c"]
    assert m.doc_ids == ["d1", "d2"]


# ------------------------------------------------------------------------------
# 잘못 적은 csv 는 오류
# ------------------------------------------------------------------------------
@pytest.mark.parametrize("row, message", [
    (",a,d1", "비어 있음"),                 # model_name 없음
    ("*,,d1", "모델명으로 쓸 수 없음"),      # 예약 값
    ("X-1,a,", "doc_ids가 비어 있음"),       # 어느 문서 모델인지 모름
])
def test_bad_rows(tmp_path, row, message):
    path = write_csv(tmp_path / "m.csv", "model_name,aliases,doc_ids\n" + row + "\n")
    with pytest.raises(ValueError, match=message):
        Glossary().load_models(path)


def test_alias_collision(tmp_path):
    # 별칭 "A" 가 두 모델을 가리키면 명판 매칭 결과가 모호해지므로 오류
    path = write_csv(tmp_path / "m.csv", "model_name,aliases,doc_ids\nX-1,A,d1\nX-2,A,d1\n")
    with pytest.raises(ValueError, match="겹침"):
        Glossary().load_models(path)
