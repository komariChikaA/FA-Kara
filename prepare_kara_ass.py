import argparse
import re
from pathlib import Path


FALLBACK_STYLES = ("K14", "K16")
FALLBACK_STYLE_SETTINGS = {
    "K14": {"alignment": "1", "marginl": "10", "marginr": "5", "marginv": "210"},
    "K16": {"alignment": "3", "marginl": "5", "marginr": "10", "marginv": "40"},
}

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

CODE_TEXT = 'fxgroup.kara=syl.inline_fx==""'
TEMPLATE_TEXTS = {
    ("code syl all", CODE_TEXT),
    (
        "template syl noblank all fxgroup kara",
        r'!retime("line",-100,0)!{\pos($center,$middle)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}',
    ),
    (
        "template syl all fxgroup kara",
        r'!retime("line",-500,0)!{\pos($center,$middle)\an5\fad(1500,400)}',
    ),
    (
        "template furi all",
        r'!retime("line",-100,0)!{\pos($center,!$middle!)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}',
    ),
    (
        "template furi all",
        r'!retime("line",-500,0)!{\pos($center,!$middle!)\an5\fad(1500,400)}',
    ),
    (
        "template fx no_k",
        r'!retime("line",-500,0)!{\pos($center,!$middle!)\an5\1c&H505050&\3c&HFFFFFFF&}',
    ),
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


def collect_style_names(lines):
    start, end = section_bounds(lines, "V4+ Styles")
    if start is None:
        raise ValueError("Could not find [V4+ Styles] section.")
    style_format = collect_format(lines, "V4+ Styles", DEFAULT_STYLE_FORMAT)
    name_i = field_index(style_format, "name", 0)
    styles = []
    seen = set()
    for line in lines[start + 1 : end]:
        match = re.match(r"\s*Style\s*:\s*(.*)$", line, re.IGNORECASE)
        if not match:
            continue
        fields = split_ass_fields(match.group(1), len(style_format))
        name = fields[name_i].strip() if name_i < len(fields) else ""
        if name and name not in seen:
            styles.append(name)
            seen.add(name)
    if not styles:
        raise ValueError("Could not find any Style lines in [V4+ Styles].")
    return styles


def is_default_or_furigana_style(style):
    lower = style.strip().lower()
    return lower == "default" or lower == "default-furigana" or lower.endswith("-furigana")


def template_styles_from_ass(lines):
    filtered = []
    seen = set()
    for style in collect_style_names(lines):
        if is_default_or_furigana_style(style):
            continue
        if style not in seen:
            filtered.append(style)
            seen.add(style)
    if filtered:
        return filtered, False
    return list(FALLBACK_STYLES), True


def ensure_fallback_styles(lines):
    start, end = section_bounds(lines, "V4+ Styles")
    if start is None:
        raise ValueError("Could not find [V4+ Styles] section.")
    style_format = collect_format(lines, "V4+ Styles", DEFAULT_STYLE_FORMAT)
    name_i = field_index(style_format, "name", 0)
    existing = set()
    source_fields = None
    insert_at = None

    for index in range(start + 1, end):
        match = re.match(r"\s*Style\s*:\s*(.*)$", lines[index], re.IGNORECASE)
        if not match:
            continue
        fields = split_ass_fields(match.group(1), len(style_format))
        while len(fields) < len(style_format):
            fields.append("")
        style_name = fields[name_i].strip()
        existing.add(style_name)
        insert_at = index + 1
        if source_fields is None or style_name.lower() == "default":
            source_fields = fields

    if source_fields is None or insert_at is None:
        raise ValueError("Could not find a source Style line to copy.")

    index_by_name = {name: field_index(style_format, name) for name in DEFAULT_STYLE_FORMAT}
    for style_name in FALLBACK_STYLES:
        if style_name in existing:
            continue
        fields = list(source_fields)
        settings = FALLBACK_STYLE_SETTINGS[style_name]
        fields[index_by_name["name"]] = style_name
        for key, value in settings.items():
            idx = index_by_name.get(key)
            if idx is not None:
                fields[idx] = value
        lines.insert(insert_at, "Style: " + ",".join(fields))
        insert_at += 1
    return lines


def ass_time_to_ms(value):
    match = re.match(r"^(\d+):(\d\d):(\d\d)\.(\d\d)$", value.strip())
    if not match:
        raise ValueError(f"Invalid ASS time: {value}")
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return ((hours * 3600 + minutes * 60 + seconds) * 1000) + centiseconds * 10


def ms_to_ass_time(ms):
    centiseconds = max(0, int(round(ms / 10)))
    hours, centiseconds = divmod(centiseconds, 360000)
    minutes, centiseconds = divmod(centiseconds, 6000)
    seconds, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}"


def event_parts(line):
    match = re.match(r"\s*(Dialogue|Comment)\s*:\s*(.*)$", line, re.IGNORECASE)
    if not match:
        return None, None
    prefix = "Dialogue" if match.group(1).lower() == "dialogue" else "Comment"
    return prefix, match.group(2)


def join_event(prefix, fields):
    return f"{prefix}: {','.join(fields)}"


def parse_event(line, event_format):
    prefix, body = event_parts(line)
    if not prefix:
        return None, None
    fields = split_ass_fields(body, len(event_format))
    while len(fields) < len(event_format):
        fields.append("")
    return prefix, fields


def is_known_template_line(line, event_format):
    prefix, fields = parse_event(line, event_format)
    if prefix != "Comment":
        return False
    effect_i = field_index(event_format, "effect", 8)
    text_i = field_index(event_format, "text", len(event_format) - 1)
    effect = fields[effect_i].strip()
    text = fields[text_i]
    return (effect, text) in TEMPLATE_TEXTS


def template_entry(style, layer, start_ms, end_ms, actor, effect, text):
    return (
        f"Comment: {layer},{ms_to_ass_time(start_ms)},{ms_to_ass_time(end_ms)},"
        f"{style},{actor},0,0,0,{effect},{text}"
    )


def template_block(styles):
    lines = []
    for style in styles:
        lines.append(template_entry(style, 0, 0, 0, "", "code syl all", CODE_TEXT))
        lines.append(
            template_entry(
                style,
                1,
                0,
                0,
                "overlay",
                "template syl noblank all fxgroup kara",
                r'!retime("line",-100,0)!{\pos($center,$middle)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}',
            )
        )
        lines.append(
            template_entry(
                style,
                0,
                0,
                0,
                "",
                "template syl all fxgroup kara",
                r'!retime("line",-500,0)!{\pos($center,$middle)\an5\fad(1500,400)}',
            )
        )
        lines.append(
            template_entry(
                style,
                1,
                18650,
                20650,
                "overlay",
                "template furi all",
                r'!retime("line",-100,0)!{\pos($center,!$middle!)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}',
            )
        )
        lines.append(
            template_entry(
                style,
                0,
                0,
                0,
                "",
                "template furi all",
                r'!retime("line",-500,0)!{\pos($center,!$middle!)\an5\fad(1500,400)}',
            )
        )
        lines.append(
            template_entry(
                style,
                0,
                0,
                0,
                "music",
                "template fx no_k",
                r'!retime("line",-500,0)!{\pos($center,!$middle!)\an5\1c&H505050&\3c&HFFFFFFF&}',
            )
        )
    return lines


def insert_templates(lines, styles):
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    cleaned = [line for line in lines if not is_known_template_line(line, event_format)]
    start, end = section_bounds(cleaned, "Events")
    if start is None:
        raise ValueError("Could not find [Events] section.")
    insert_at = None
    for index in range(start + 1, end):
        if parse_format_fields(cleaned[index]):
            insert_at = index + 1
            break
    if insert_at is None:
        raise ValueError("Could not find [Events] Format line.")
    cleaned[insert_at:insert_at] = template_block(styles)
    return cleaned


def activate_karaoke_comments(lines):
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    effect_i = field_index(event_format, "effect", 8)
    for index, line in enumerate(lines):
        prefix, fields = parse_event(line, event_format)
        if prefix == "Comment" and fields[effect_i].strip().lower() == "karaoke":
            lines[index] = join_event("Dialogue", fields)
    return lines


def remove_empty_kara_tail(lines):
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    start_i = field_index(event_format, "start", 1)
    end_i = field_index(event_format, "end", 2)
    text_i = field_index(event_format, "text", len(event_format) - 1)
    tail_re = re.compile(r"\s*\{\\[kK](?:[fo])?-?(\d+)\}\s*$")

    for index, line in enumerate(lines):
        prefix, fields = parse_event(line, event_format)
        if prefix != "Dialogue":
            continue
        removed = 0
        text = fields[text_i]
        while True:
            match = tail_re.search(text)
            if not match:
                break
            removed += int(match.group(1))
            text = text[: match.start()]
        if removed:
            fields[text_i] = text
            start_ms = ass_time_to_ms(fields[start_i])
            end_ms = ass_time_to_ms(fields[end_i])
            fields[end_i] = ms_to_ass_time(max(start_ms, end_ms - removed * 10))
            lines[index] = join_event(prefix, fields)
    return lines


def apply_style_set(lines, styles):
    if len(styles) < 2:
        return lines
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    style_i = field_index(event_format, "style", 3)
    effect_i = field_index(event_format, "effect", 8)
    count = 0
    style_pair = styles[:2]

    for index, line in enumerate(lines):
        prefix, fields = parse_event(line, event_format)
        if prefix == "Dialogue" and fields[effect_i].strip().lower() == "karaoke":
            fields[style_i] = style_pair[count % 2]
            count += 1
            lines[index] = join_event(prefix, fields)
    return lines


def parse_k_syllables(text):
    return [(int(match.group(1)), match.group(2)) for match in re.finditer(r"\{\\[kK](\d+)\}([^{}]*)", text)]


def apply_smart_leadin(lines, lead_in_ms):
    event_format = collect_format(lines, "Events", DEFAULT_EVENT_FORMAT)
    start_i = field_index(event_format, "start", 1)
    end_i = field_index(event_format, "end", 2)
    style_i = field_index(event_format, "style", 3)
    effect_i = field_index(event_format, "effect", 8)
    text_i = field_index(event_format, "text", len(event_format) - 1)
    groups = {}

    for index, line in enumerate(lines):
        prefix, fields = parse_event(line, event_format)
        if prefix == "Dialogue" and fields[effect_i].strip().lower() == "karaoke":
            groups.setdefault(fields[style_i], []).append((index, fields))

    for group in groups.values():
        group.sort(key=lambda item: ass_time_to_ms(item[1][start_i]))
        previous_end = None
        for position, (line_index, fields) in enumerate(group):
            old_start = ass_time_to_ms(fields[start_i])
            old_end = ass_time_to_ms(fields[end_i])
            if position == 0:
                new_start = max(0, old_start - lead_in_ms)
            else:
                gap = old_start - previous_end
                new_start = previous_end if gap <= lead_in_ms else old_start - lead_in_ms

            syllables = parse_k_syllables(fields[text_i])
            if syllables:
                offset = old_start - new_start
                lead_k = offset // 10
                new_text = f"{{\\k{lead_k}}}" if lead_k > 0 else ""
                new_text += "".join(f"{{\\k{duration}}}{text}" for duration, text in syllables)
                fields[start_i] = ms_to_ass_time(new_start)
                fields[text_i] = new_text
                lines[line_index] = join_event("Dialogue", fields)
            previous_end = old_end
    return lines


def prepare_ass_text(text, lead_in_ms=5000):
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    had_final_newline = normalized.endswith("\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    styles, used_fallback = template_styles_from_ass(lines)
    if used_fallback:
        lines = ensure_fallback_styles(lines)

    lines = insert_templates(lines, styles)
    lines = activate_karaoke_comments(lines)
    lines = remove_empty_kara_tail(lines)
    lines = apply_style_set(lines, styles)
    lines = apply_smart_leadin(lines, lead_in_ms)

    output = "\n".join(lines)
    if had_final_newline or not output.endswith("\n"):
        output += "\n"
    return output


def read_ass(path):
    data = Path(path).read_bytes()
    has_bom = data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    newline = "\r\n" if b"\r\n" in data else "\n"
    return text, has_bom, newline


def write_ass(path, text, has_bom, newline):
    output = text.replace("\n", newline)
    encoding = "utf-8-sig" if has_bom else "utf-8"
    Path(path).write_text(output, encoding=encoding, newline="")


def default_output_path(input_path):
    path = Path(input_path)
    return path.with_name(f"{path.stem}_prepared{path.suffix or '.ass'}")


def is_generated_ass(path):
    generated_suffixes = ("_prepared", "_fx", "_effect", "_effects")
    return any(path.stem.lower().endswith(suffix) for suffix in generated_suffixes)


def prepare_file(input_path, output_path, lead_in_ms):
    text, has_bom, newline = read_ass(input_path)
    prepared = prepare_ass_text(text, lead_in_ms)
    write_ass(output_path, prepared, has_bom, newline)


def iter_song_ass_files(songs_dir):
    for path in sorted(Path(songs_dir).rglob("*.ass")):
        if path.is_file() and not is_generated_ass(path):
            yield path


def batch_prepare_songs(songs_dir, lead_in_ms, in_place=False, overwrite=False):
    songs_dir = Path(songs_dir)
    if not songs_dir.exists():
        raise FileNotFoundError(f"Songs directory not found: {songs_dir}")

    processed = 0
    skipped = 0
    failed = 0
    for input_path in iter_song_ass_files(songs_dir):
        output_path = input_path if in_place else default_output_path(input_path)
        if output_path.exists() and output_path != input_path and not overwrite:
            print(f"Skipped existing output: {output_path}")
            skipped += 1
            continue
        try:
            prepare_file(input_path, output_path, lead_in_ms)
        except Exception as exc:
            print(f"Failed {input_path}: {exc}")
            failed += 1
            continue
        print(f"Wrote prepared ASS to {output_path}")
        processed += 1

    print(f"Batch finished: {processed} processed, {skipped} skipped, {failed} failed.")
    if processed:
        print("Final step: open prepared ASS files in Aegisub and run Automation > Apply karaoke template.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Prepare an ASS file before the final Aegisub Karaoke Templater step. "
            "This runs add_kara_templates, empty-tail cleanup, STYLEset, and 5spre logic."
        )
    )
    parser.add_argument("input_ass", nargs="?", help="Input ASS file.")
    parser.add_argument("output_ass", nargs="?", help="Output ASS file. Defaults to *_prepared.ass.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input ASS file.")
    parser.add_argument("--lead-in-ms", type=int, default=5000, help="Lead-in duration used by 5spre logic.")
    parser.add_argument("--batch-songs", action="store_true", help="Process all .ass files under songs/.")
    parser.add_argument("--songs-dir", default="songs", help="Songs directory used with --batch-songs.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing *_prepared.ass files in batch mode.")
    args = parser.parse_args()

    if args.batch_songs:
        if args.output_ass:
            parser.error("output_ass cannot be used with --batch-songs")
        batch_prepare_songs(
            args.songs_dir,
            args.lead_in_ms,
            in_place=args.in_place,
            overwrite=args.overwrite,
        )
        return

    if not args.input_ass:
        parser.error("input_ass is required unless --batch-songs is used")

    input_path = Path(args.input_ass)
    if args.in_place:
        output_path = input_path
    else:
        output_path = Path(args.output_ass) if args.output_ass else default_output_path(input_path)

    prepare_file(input_path, output_path, args.lead_in_ms)
    print(f"Wrote prepared ASS to {output_path}")
    print("Final step: open this ASS in Aegisub and run Automation > Apply karaoke template.")


if __name__ == "__main__":
    main()
