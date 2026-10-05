import pytest

from notes_bot.domain import lecture_number, split_messages, textbook_pages, utf16_length, video_id
from notes_bot.ml import context_from_results


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/HuM41bMIFIE",
        "https://www.youtube.com/watch?v=HuM41bMIFIE&t=10",
        "https://youtube.com/live/HuM41bMIFIE",
    ],
)
def test_video_canonicalization(url):
    assert video_id(url) == "HuM41bMIFIE"


@pytest.mark.parametrize(
    "url",
    [
        "http://youtu.be/HuM41bMIFIE",
        "https://youtube.com.evil.test/watch?v=HuM41bMIFIE",
        "https://youtube.com/playlist?list=123",
        "https://user@youtu.be/HuM41bMIFIE",
        "https://127.0.0.1/watch?v=HuM41bMIFIE",
    ],
)
def test_video_rejects_non_video_and_unsafe_hosts(url):
    with pytest.raises(ValueError):
        video_id(url)


def test_lecture_normalization():
    assert lecture_number(" 1 / 7 ") == "1/7"
    with pytest.raises(ValueError):
        lecture_number("0/1")


@pytest.mark.parametrize(
    "text",
    ["🙂" * 2500, "x" * 10000, ("Paragraph.\n\n" + "line\n" * 50) * 30, "x" * 1899 + "\n\n" + "y" * 3000],
)
def test_discord_split_preserves_all_content_and_size(text):
    chunks = split_messages(text)
    assert "".join(chunks) == text
    assert all(0 < utf16_length(part) <= 1900 for part in chunks)


def test_textbook_markers_and_plain_text():
    assert textbook_pages("===== PAGE 12 =====\nمرحبا\n===== PAGE 13 =====\nSecond") == [
        (12, "مرحبا"),
        (13, "Second"),
    ]
    assert textbook_pages("plain text") == [(1, "plain text")]
    with pytest.raises(ValueError):
        textbook_pages("===== PAGE 1 =====\nOne\n===== PAGE 1 =====\nTwo")


def test_rag_backups_require_adjacent_lecture_chunks():
    def reference(page, rank):
        return {"page": page, "chunk": 1, "text": f"PASSAGE {page}", "bge_rank": rank, "e5_rank": rank}

    primary = [reference(i, i) for i in range(1, 6)]
    results = [
        primary + [reference(6, 6), reference(7, 7)],
        primary + [reference(6, 6)],
        primary + [reference(7, 6)],
    ]
    context = context_from_results(["lecture one", "lecture two", "lecture three"], results)
    assert "PASSAGE 6" in context
    assert "PASSAGE 7" not in context
    assert context.count("PASSAGE 6") == 1
    assert "BACKUP" in context
