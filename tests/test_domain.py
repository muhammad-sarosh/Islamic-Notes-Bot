import pytest

from notes_bot.domain import (
    lecture_number,
    split_long_point,
    split_messages,
    textbook_pages,
    utf16_length,
    video_id,
)
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
    assert "".join("".join(chunks).split()) == "".join(text.split())
    assert all(0 < utf16_length(part) <= 1900 for part in chunks)


def test_discord_split_keeps_small_heading_sections_separate():
    assert split_messages("Title\n\n**First**\n- One\n\n**Second**\n- Two") == [
        "Title", "**First**\n- One", "**Second**\n- Two"
    ]


def test_discord_split_oversized_section_at_top_level_points():
    text = "**Topic**\n- " + "a" * 1000 + "\n  - Nested point\n- " + "b" * 1000
    chunks = split_messages(text)
    assert len(chunks) == 2
    assert "Nested point" in chunks[0]
    assert chunks[1].startswith("- b")


def test_split_preserves_nested_bullets_in_oversized_section():
    first = "2. **Both parties must consent.** A main point.\n  - If consent is absent.\n  - Another case."
    text = "**Conditions**\n" + first + "\n3. " + "x" * 1850
    parts = split_messages(text)
    assert first in parts[0]
    assert "point. - If" not in "".join(parts)


def test_long_numbered_point_preserves_newlines_and_nested_indentation():
    text = "2. **Consent.** " + "a" * 900 + ".\n  - " + "b" * 900 + ".\n  - " + "c" * 900 + "."
    parts = split_long_point(text, 1900)
    assert parts[0] == text.split("\n  - " + "c" * 900)[0]
    assert parts[1].startswith("  - ")
    assert all(utf16_length(part) <= 1900 for part in parts)
    assert "".join("".join(parts).split()) == "".join(text.split())


@pytest.mark.parametrize("heading", ["**Topic**", "## Topic"])
@pytest.mark.parametrize("blank", ["", "\n", "\n \n"])
def test_discord_heading_has_no_blank_line_for_short_and_split_sections(heading, blank):
    for size in [10, 1000]:
        text = heading + "\n" + blank + "- " + "a" * size + "\n- " + "b" * size
        parts = split_messages(text)
        assert parts[0].startswith(heading + "\n- ")
        assert all(utf16_length(part) <= 1900 for part in parts)
        assert "".join("".join(parts).split()) == "".join(text.split())


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
