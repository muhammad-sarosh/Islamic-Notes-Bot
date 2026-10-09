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


def _list_item_marker(line):
    match = re.match(r"^([ \t]*)([-+*]|\d+[.)])\s", line)
    if not match:
        return None
    indent = len(match[1].expandtabs(4))
    ordered = match[2] not in {"-", "+", "*"}
    return indent, ordered


def _normalize_list_spacing(section):
    lines, list_types = [], {}
    for line in section.split("\n"):
        marker = _list_item_marker(line)
        if marker:
            indent, ordered = marker
            while lines and not lines[-1].strip():
                lines.pop()
            if list_types.get(indent) is not None and list_types[indent] != ordered:
                lines.append("")
            lines.append(line)
            list_types = {level: kind for level, kind in list_types.items() if level < indent}
            list_types[indent] = ordered
        else:
            lines.append(line)
            if line.strip():
                indent = len(line) - len(line.lstrip(" \t"))
                indent = len(line[:indent].expandtabs(4))
                list_types = {level: kind for level, kind in list_types.items() if level <= indent}
    return "\n".join(lines)


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
        # Discord format rules require the first point directly below its heading.
        section = re.sub(r"^((?:\*\*.+\*\*|#{1,6}\s+.+))\n(?:[ \t]*\n)+", r"\1\n", section)
        section = _normalize_list_spacing(section)
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
        previous_point_marker = None
        for point in points:
            separator = "\n"
            point_marker = _list_item_marker(point.split("\n", 1)[0])
            if (
                chunk
                and previous_point_marker
                and point_marker
                and previous_point_marker[0] == point_marker[0]
                and previous_point_marker[1] != point_marker[1]
            ):
                separator = "\n\n"
            candidate = chunk + separator + point if chunk else point
            if utf16_length(candidate) <= limit:
                chunk = candidate
                previous_point_marker = point_marker
                continue
            if chunk:
                output.extend(split_long_point(chunk.strip(), limit))
            if utf16_length(point) > limit:
                output.extend(split_long_point(point, limit))
                chunk = ""
                previous_point_marker = None
            else:
                chunk = point
                previous_point_marker = point_marker
        if chunk:
            output.extend(split_long_point(chunk.strip(), limit))
    return output


def split_long_point(point, limit):
    """Split at existing line/sentence boundaries without flattening Markdown."""
    result = []
    while utf16_length(point) > limit:
        size, end = 0, 0
        for char in point:
            width = utf16_length(char)
            if size + width > limit:
                break
            size += width
            end += 1
        prefix = point[:end]
        # Prefer whole Markdown lines, retaining indentation in the next part.
        cut = prefix.rfind("\n") + 1
        if not cut or not prefix[:cut].strip():
            sentences = list(re.finditer(r"(?<=[.!?])[ \t]+", prefix))
            cut = sentences[-1].end() if sentences else prefix.rfind(" ") + 1
            if not cut or not prefix[:cut].strip():
                cut = end
        part = point[:cut].rstrip("\r\n")
        if part:
            result.append(part)
        point = point[cut:]
    if point.strip():
        result.append(point.rstrip())
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
