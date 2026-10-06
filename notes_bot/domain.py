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
    """Original Send Notes workflow: heading sections, points, sentences, hard limit."""
    if not text.strip():
        raise ValueError("Notes cannot be empty")
    if limit < 2:
        raise ValueError("Message limit must be at least two UTF-16 units")
    heading = re.compile(r"^(?:\*\*.+\*\*|#{1,6}\s+.+)$")
    sections, current = [], []
    for line in text.replace("\r\n", "\n").strip().split("\n"):
        if heading.fullmatch(line.strip()) and current:
            sections.append("\n".join(current).strip())
            current = []
        current.append(line)
    if current:
        sections.append("\n".join(current).strip())
    output = []
    for section in filter(None, sections):
        if utf16_length(section) <= limit:
            output.append(section)
            continue
        lines = section.split("\n")
        title = lines.pop(0) if heading.fullmatch(lines[0].strip()) else ""
        points, point = [], []
        for line in lines:
            if re.match(r"^(?:[-+*]\s|\d+[.)]\s)", line) and point:
                points.append("\n".join(point).strip())
                point = []
            if point or line.strip():
                point.append(line)
        if point:
            points.append("\n".join(point).strip())
        chunk = title
        for point in points:
            candidate = chunk + "\n\n" + point if chunk else point
            if utf16_length(candidate) <= limit:
                chunk = candidate
                continue
            if chunk:
                output.extend(split_long_point(chunk.strip(), limit))
            if utf16_length(point) > limit:
                output.extend(split_long_point(point, limit))
                chunk = ""
            else:
                chunk = point
        if chunk:
            output.extend(split_long_point(chunk.strip(), limit))
    return output


def split_long_point(point, limit):
    result, chunk = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", point):
        clean = sentence.strip()
        candidate = chunk + " " + clean if chunk else clean
        if utf16_length(candidate) <= limit:
            chunk = candidate
            continue
        if chunk:
            result.append(chunk)
        if utf16_length(clean) <= limit:
            chunk = clean
        else:
            piece, size = "", 0
            for char in clean:
                width = utf16_length(char)
                if size + width > limit:
                    result.append(piece)
                    piece, size = "", 0
                piece += char
                size += width
            if piece:
                result.append(piece)
            chunk = ""
    if chunk:
        result.append(chunk)
    return result


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
