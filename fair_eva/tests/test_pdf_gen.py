from io import BytesIO
from pathlib import Path

import pytest
from flask import Flask
from flask_babel import Babel
from pypdf import PdfReader

from fair_eva.utils import pdf_gen

IMG = Path(__file__).resolve().parents[2] / "static" / "img"


def indicator(name, points, weight, msg):
    return {
        "name": name,
        "points": points,
        "color": "#2ECC71" if points > 80 else "#E74C3C",
        "test_status": "pass" if points >= 75 else "fail",
        "score": {"earned": points, "total": 100, "weight": weight},
        "msg": msg,
    }


def evaluation(msg):
    """Result of rda_all as export_pdf hands it to create_pdf."""
    data = {
        "findable": {
            "rda_f1_01m": indicator("rda_f1_01m", 100, 3, msg),
            "rda_f1_02m": indicator("rda_f1_02m", 50, 2, msg),
        },
        "accessible": {"rda_a1_01m": indicator("rda_a1_01m", 0, 3, msg)},
        "interoperable": {"rda_i1_01m": indicator("rda_i1_01m", 75, 1, msg)},
        "reusable": {"rda_r1_01m": indicator("rda_r1_01m", 90, 3, msg)},
    }
    for group in data.values():
        group["result"] = {"points": 70.0, "color": "#F4D03F"}
    return data


@pytest.fixture(autouse=True)
def request_context():
    app = Flask(__name__)
    Babel(app)
    with app.test_request_context():
        yield


def create_pdf(msg):
    return pdf_gen.create_pdf(
        evaluation(msg),
        "fair_report.pdf",
        "https://doi.org/10.1234/abcd",
        str(IMG / "logo_fair_eosc_2.png"),
        str(IMG / "csic.png"),
        63.8,
    )


def pdf_text(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


@pytest.mark.parametrize(
    "msg",
    [
        # what the indicators return through the API
        [
            {"message": "Found persistent identifier: 10.1234/abcd", "points": 100},
            {"message": "Second message", "points": 0},
        ],
        "Plain feedback & <markup-like> text",
        None,
    ],
    ids=["message-list", "string", "none"],
)
def test_create_pdf_builds_a_readable_report(msg):
    pdf = create_pdf(msg)
    assert pdf.startswith(b"%PDF")
    text = pdf_text(pdf)
    assert "FAIR EVA" in text
    assert "https://doi.org/10.1234/abcd" in text
    for group in ("FINDABLE", "ACCESSIBLE", "INTEROPERABLE", "REUSABLE"):
        assert group in text


def test_create_pdf_includes_the_messages_of_the_indicators():
    text = pdf_text(create_pdf([{"message": "Found persistent identifier"}]))
    assert "Found persistent identifier" in text


@pytest.mark.parametrize(
    "msg, expected",
    [
        ([{"message": "a", "points": 1}, {"message": "b"}], "a<br/>b"),
        (["a", "b"], "a<br/>b"),
        ("x & <y>", "x &amp; &lt;y&gt;"),
        (None, ""),
        ("abcdef", "abc"),
    ],
)
def test_msg_to_text(msg, expected):
    max_length = 3 if msg == "abcdef" else 500
    assert pdf_gen.msg_to_text(msg, max_length) == expected


def test_merge_pdf(tmp_path):
    first, second, merged = (tmp_path / n for n in ("a.pdf", "b.pdf", "out.pdf"))
    first.write_bytes(create_pdf("first"))
    second.write_bytes(create_pdf("second"))
    pages = len(PdfReader(first).pages) + len(PdfReader(second).pages)
    pdf_gen.merge_pdf(str(first), str(second), str(merged))
    assert len(PdfReader(merged).pages) == pages
