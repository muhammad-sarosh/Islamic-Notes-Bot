import re
from urllib.parse import parse_qs, urlparse


def video_id(url):
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise ValueError("Use a normal HTTPS YouTube video link")
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        value = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            value = parse_qs(parsed.query).get("v", [""])[0]
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([^/]+)/?", parsed.path)
            value = match[1] if match else ""
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("Enter a YouTube video link, not a playlist or another website")
    return value


def lecture_number(value):
    match = re.fullmatch(r"\s*([1-9]\d{0,3})\s*/\s*([1-9]\d{0,3})\s*", value)
    if not match:
        raise ValueError("Lecture number must look like 1/7")
    return f"{match[1]}/{match[2]}"


def utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def split_messages(text, limit=1900):
    """Prefer paragraph/line boundaries, never drop content or exceed Discord's UTF-16 limit."""
    if not text.strip():
        raise ValueError("Notes cannot be empty")
    output = []
    remaining = text
    while utf16_length(remaining) > limit:
        size = 0
        end = 0
        for end, char in enumerate(remaining, 1):
            size += utf16_length(char)
            if size > limit:
                end -= 1
                break
        cut = remaining.rfind("\n\n", 0, end + 1)
        if cut >= end // 2:
            end = cut + 2
        else:
            cut = remaining.rfind("\n", 0, end + 1)
            if cut >= end // 2:
                end = cut + 1
        # A separator can cross the selected boundary; stay within the hard limit.
        while utf16_length(remaining[:end]) > limit:
            end -= 1
        output.append(remaining[:end])
        remaining = remaining[end:]
    if remaining:
        output.append(remaining)
    return output


def textbook_pages(text):
    if not text.strip() or "\x00" in text:
        raise ValueError("Upload non-empty UTF-8 textbook text")
    matches = list(re.finditer(r"(?m)^===== PAGE (\d+) =====\s*$", text))
    if not matches:
        return [(1, text.strip())]
    if text[: matches[0].start()].strip():
        raise ValueError("Place all text after a page marker, or upload plain text without markers")
    pages = []
    seen = set()
    for i, match in enumerate(matches):
        number = int(match[1])
        if number < 1 or number in seen:
            raise ValueError("Page markers must contain distinct positive page numbers")
        seen.add(number)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[match.end() : end].strip()
        if content:
            pages.append((number, content))
    if not pages:
        raise ValueError("Textbook contains no page text")
    return pages
