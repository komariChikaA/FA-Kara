import argparse
import re
from pathlib import Path


DEFAULT_STYLE_FORMAT = [
    "name",
    "fontname",
    "fontsize",
    "primarycolour",
    "secondarycolour",
    "outlinecolour",
    "backcolour",
    "bold",
    "italic",
    "underline",
    "strikeout",
    "scalex",
    "scaley",
    "spacing",
    "angle",
    "borderstyle",
    "outline",
    "shadow",
    "alignment",
    "marginl",
    "marginr",
    "marginv",
    "encoding",
]

DEFAULT_EVENT_FORMAT = [
    "layer",
    "start",
    "end",
    "style",
    "name",
    "marginl",
    "marginr",
    "marginv",
    "effect",
    "text",
]

MEDIA_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".ogg",
}


def split_ass_fields(text, max_fields):
    fields = []
    start = 0
    for _ in range(max_fields - 1):
        comma = text.find(",", start)
        if comma < 0:
            break
        fields.append(text[start:comma])
        start = comma + 1
    fields.append(text[start:])
    return fields


def parse_format_fields(line):
    match = re.match(r"\s*Format\s*:\s*(.*)$", line, re.IGNORECASE)
    if not match:
        return None
    return [part.strip().lower() for part in match.group(1).split(",")]


def field_index(fields, name, default=None):
    try:
        return fields.index(name)
    except ValueError:
        return default


def section_bounds(lines, section_name):
    start = None
    section_re = re.compile(rf"\s*\[{re.escape(section_name)}\]\s*$", re.IGNORECASE)
    any_section_re = re.compile(r"\s*\[.+\]\s*$")
    for index, line in enumerate(lines):
        if start is None:
            if section_re.match(line):
                start = index
        elif any_section_re.match(line):
            return start, index
    return (start, len(lines)) if start is not None else (None, None)


def collect_format(lines, section_name, fallback):
    start, end = section_bounds(lines, section_name)
    if start is None:
        return fallback
    for line in lines[start + 1 : end]:
        fields = parse_format_fields(line)
        if fields:
            return fields
    return fallback


def ass_time(seconds):
    centiseconds = max(0, int(round(seconds * 100)))
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def find_style(lines, style_format, style_name):
    start, end = section_bounds(lines, "V4+ Styles")
    if start is None:
        raise ValueError("Could not find [V4+ Styles] section.")
    name_i = field_index(style_format, "name", 0)
    for index in range(start + 1, end):
        match = re.match(r"\s*Style\s*:\s*(.*)$", lines[index], re.IGNORECASE)
        if not match:
            continue
        fields = split_ass_fields(match.group(1), len(style_format))
        while len(fields) < len(style_format):
            fields.append("")
        if fields[name_i].strip().lower() == style_name.lower():
            return index, fields
    return None, None


def ensure_intro_style(lines, style_name, font_name):
    style_format = collect_format(lines, "V4+ Styles", DEFAULT_STYLE_FORMAT)
    index_by_name = {name: field_index(style_format, name) for name in DEFAULT_STYLE_FORMAT}
    existing_index, existing_fields = find_style(lines, style_format, style_name)
    source_index, source_fields = find_style(lines, style_format, "K14")
    if source_fields is None:
        source_index, source_fields = find_style(lines, style_format, "Default")
    if source_fields is None:
        raise ValueError("Could not find K14 or Default style to copy.")

    fields = list(existing_fields or source_fields)
    fields[index_by_name["name"]] = style_name
    fields[index_by_name["fontname"]] = font_name
    fields[index_by_name["fontsize"]] = "54"
    fields[index_by_name["alignment"]] = "7"
    fields[index_by_name["marginl"]] = "48"
    fields[index_by_name["marginr"]] = "48"
    fields[index_by_name["marginv"]] = "40"
    fields[index_by_name["outline"]] = "2"
    fields[index_by_name["shadow"]] = "2"
    fields[index_by_name["bold"]] = "-1"
    new_line = "Style: " + ",".join(fields) + "\n"

    if existing_index is not None:
        lines[existing_index] = new_line
        return

    insert_at = source_index + 1
    lines.insert(insert_at, new_line)


def clean_ass_text(value):
    return " ".join(value.replace("{", "（").replace("}", "）").split())


def infer_title(ass_path):
    stem = ass_path.stem
    for suffix in ("_prepared", "_realign"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if ass_path.parent.name and ass_path.parent.name.lower() != "songs":
        return ass_path.parent.name
    return stem


def infer_artist(folder):
    for path in sorted(folder.iterdir(), key=lambda item: item.name):
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        if path.name.endswith("_kara.mp4"):
            continue
        match = re.search(r"歌[:：]\s*(.+?)(?:\s*\[[^\]]+\])?$", path.stem)
        if match:
            return match.group(1).strip()
    return ""


def make_intro_text(title, artist, fade_ms):
    parts = [clean_ass_text(title)]
    if artist:
        parts.append(clean_ass_text(artist))
    body = r"\N".join(part for part in parts if part)
    return rf"{{\fad({fade_ms},{fade_ms})}}{body}"


def remove_existing_intro_events(lines, event_format, style_name):
    start, end = section_bounds(lines, "Events")
    if start is None:
        raise ValueError("Could not find [Events] section.")
    effect_i = field_index(event_format, "effect", 8)
    style_i = field_index(event_format, "style", 3)
    kept = []
    for line in lines[start + 1 : end]:
        match = re.match(r"\s*(Dialogue|Comment)\s*:\s*(.*)$", line, re.IGNORECASE)
        if not match:
            kept.append(line)
            continue
        fields = split_ass_fields(match.group(2), len(event_format))
        while len(fields) < len(event_format):
            fields.append("")
        if fields[effect_i].strip().lower() == "song_intro" or fields[style_i].strip() == style_name:
            continue
        kept.append(line)
    lines[start + 1 : end] = kept


def insert_intro_event(lines, event_format, style_name, text, duration_seconds):
    start, end = section_bounds(lines, "Events")
    if start is None:
        raise ValueError("Could not find [Events] section.")
    format_index = None
    for index in range(start + 1, end):
        if parse_format_fields(lines[index]):
            format_index = index
            break
    if format_index is None:
        raise ValueError("Could not find Events Format line.")

    fields = [""] * len(event_format)
    defaults = {
        "layer": "10",
        "start": ass_time(0),
        "end": ass_time(duration_seconds),
        "style": style_name,
        "name": "SongIntro",
        "marginl": "0",
        "marginr": "0",
        "marginv": "0",
        "effect": "song_intro",
        "text": text,
    }
    for name, value in defaults.items():
        idx = field_index(event_format, name)
        if idx is not None:
            fields[idx] = value
    lines.insert(format_index + 1, "Dialogue: " + ",".join(fields) + "\n")


def update_ass(ass_path, title, artist, style_name, font_name, duration_seconds, fade_ms):
    lines = ass_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines:
        raise ValueError(f"Empty ASS file: {ass_path}")
    ensure_intro_style(lines, style_name, font_name)
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    intro_text = make_intro_text(title, artist, fade_ms)
    remove_existing_intro_events(lines, event_format, style_name)
    insert_intro_event(lines, event_format, style_name, intro_text, duration_seconds)
    ass_path.write_text("".join(lines), encoding="utf-8")


def iter_batch_ass_files(songs_dir, prepared):
    suffix = "_prepared.ass" if prepared else ".ass"
    for folder in sorted((path for path in songs_dir.iterdir() if path.is_dir()), key=lambda path: path.name.casefold()):
        preferred = folder / (folder.name + suffix)
        if preferred.exists():
            yield preferred
            continue
        candidates = [
            path
            for path in folder.glob(f"*{suffix}")
            if not path.name.startswith(".") and "_realign" not in path.stem
        ]
        if len(candidates) == 1:
            yield candidates[0]


def main():
    parser = argparse.ArgumentParser(description="Add a 5-second song title/artist intro to ASS files.")
    parser.add_argument("ass", nargs="?", type=Path, help="ASS file to update in place.")
    parser.add_argument("--batch-songs", action="store_true", help="Update ASS files under songs/.")
    parser.add_argument("--songs-dir", type=Path, default=Path("songs"), help="Songs root for --batch-songs.")
    parser.add_argument("--prepared", action="store_true", help="Batch-update *_prepared.ass instead of base .ass.")
    parser.add_argument("--title", default=None, help="Override title for single-file mode.")
    parser.add_argument("--artist", default=None, help="Override artist for single-file mode. Empty string hides artist.")
    parser.add_argument("--style", default="SongInfo", help="ASS style name to create/update.")
    parser.add_argument("--font", default="HGPGothicE", help="Font face for the intro style.")
    parser.add_argument("--duration", type=float, default=5.0, help="Intro display duration in seconds.")
    parser.add_argument("--fade-ms", type=int, default=600, help="Fade in/out duration in milliseconds.")
    args = parser.parse_args()

    if args.batch_songs:
        ass_files = list(iter_batch_ass_files(args.songs_dir, args.prepared))
        if not ass_files:
            print("No ASS files found.")
            return
    elif args.ass:
        ass_files = [args.ass]
    else:
        parser.error("pass an ASS file or use --batch-songs")

    for ass_path in ass_files:
        title = args.title if args.title is not None else infer_title(ass_path)
        artist = args.artist if args.artist is not None else infer_artist(ass_path.parent)
        update_ass(ass_path, title, artist, args.style, args.font, args.duration, args.fade_ms)
        artist_log = f" / {artist}" if artist else ""
        print(f"updated: {ass_path} ({title}{artist_log})")


if __name__ == "__main__":
    main()
