import argparse
import bisect
import librosa
import numpy as np
import os
import re
import subprocess
import sys
import time

import align
# import ass2lrc
import haruraw2norm as hn
import lrcfmt
import norm2ass
from norm2lrc import *

COMMENT_TYPE = 6
CHUNK_TYPE = 7
LYRIC_TYPES = {1, 2, 3, 4, 5}
COMMENT_RE = re.compile(r'^\s*@(comment|note|注释)\[(?P<duration>[^\]]+)\]\s*(?P<text>.*?)\s*$')
CHUNK_RE = re.compile(r'^\s*@(chunk|split|分块)\[(?P<time>[^\]]+)\]\s*$')
ASS_K_TAG_RE = re.compile(r'{\\[kK][fo]?[-+]?\d+}')
ASS_TAG_RE = re.compile(r'{[^{}]*}')
AUDIO_EXTENSIONS = ('.wav', '.mp3', '.flac', '.m4a', '.ogg')

def non_silent_recog(audio_file, sr = None, frame_second = 1, threspct = 10, thresrto = .1):
    '识别非静音片段'
    if sr is None:
        raise ValueError("sr is required for non_silent_recog")
    frame_length = max(1, int(sr * frame_second))
    hop_length = max(1, frame_length // 2)  # 50% 重叠
    audio = np.asarray(audio_file, dtype=np.float32)
    pad_width = frame_length // 2
    padded = np.pad(audio, (pad_width, pad_width), mode='constant')
    squared = padded.astype(np.float64, copy=False) ** 2
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    frame_starts = np.arange(0, len(padded) - frame_length + 1, hop_length)
    frame_energy = cumulative[frame_starts + frame_length] - cumulative[frame_starts]
    energy = np.sqrt(frame_energy / frame_length)
    threshold = np.percentile(energy, 100-threspct) * thresrto
    non_silent_frames = energy > threshold
    times = np.arange(len(energy)) * hop_length / sr # 转换为时间点
    segments = [] # 合并连续片段
    start = None
    for i, (t, active) in enumerate(zip(times, non_silent_frames)):
        if active and start is None:
            start = max(t-frame_second/4, 0)
        elif not active and start is not None:
            segments.append((start, t+frame_second/4))
            start = None
    if start is not None:
        segments.append((start, times[-1]))
    return segments

def parse_comment_duration(raw_duration):
    '解析 @comment[...] 中以秒为单位的时长'
    value = raw_duration.strip().lower()
    for suffix in ('seconds', 'second', 'secs', 'sec', 's', '秒'):
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
            break
    try:
        seconds = float(value)
    except ValueError as exc:
        raise ValueError(f"注释段时长格式错误：{raw_duration}") from exc
    if seconds <= 0:
        raise ValueError(f"注释段时长必须大于 0：{raw_duration}")
    return int(round(seconds * 100))

def parse_comment_line(line, line_no):
    '读取不会进入音频识别的注释段：@comment[3.0] 文本'
    match = COMMENT_RE.match(line)
    if not match:
        return None
    text = match.group('text').strip()
    if not text:
        raise ValueError(f"第 {line_no} 行注释段缺少显示文字")
    return [{
        'orig': text,
        'type': COMMENT_TYPE,
        'duration': parse_comment_duration(match.group('duration')),
    }]

def parse_chunk_time(raw_time):
    '解析 @chunk[...] 中的音频时间，支持秒、mm:ss、hh:mm:ss'
    value = raw_time.strip().lower()
    for suffix in ('seconds', 'second', 'secs', 'sec', 's', '秒'):
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
            break
    parts = value.split(':')
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"分块时间格式错误：{raw_time}") from exc
    if seconds <= 0:
        raise ValueError(f"分块时间必须大于 0：{raw_time}")
    return seconds

def parse_chunk_line(line, line_no):
    '读取手动分块锚点：@chunk[01:23]'
    match = CHUNK_RE.match(line)
    if not match:
        return None
    return [{
        'orig': '',
        'type': CHUNK_TYPE,
        'time': parse_chunk_time(match.group('time')),
        'line_no': line_no,
    }]

def is_timed_lyric(item):
    return item.get('type') in LYRIC_TYPES and item.get('start') and item.get('end')

def previous_dialogue_tail(result_list, index):
    for item in reversed(result_list[:index]):
        if item.get('type') == 0 and item.get('orig') == '\n' and item.get('start'):
            return parse_time_to_hundredths(item['start'])
        if is_timed_lyric(item):
            return parse_time_to_hundredths(item['end'])
    return None

def preview_comment_text(text, max_length=20):
    return text if len(text) <= max_length else text[:max_length] + '...'

def get_alignment_sentence_spans(result_list):
    '返回按歌词行切分的 token 区间，用于自动分块'
    spans = []
    token_count = 0
    current_start = None
    for item in result_list:
        if item.get('pron'):
            if current_start is None:
                current_start = token_count
            token_count += 1
        if item.get('type') in (COMMENT_TYPE, CHUNK_TYPE) or item.get('type') == 0 and item.get('orig') == '\n':
            if current_start is not None and current_start < token_count:
                spans.append((current_start, token_count))
                current_start = None
    if current_start is not None and current_start < token_count:
        spans.append((current_start, token_count))
    return spans

def get_manual_chunk_boundaries(result_list):
    '返回手动分块锚点对应的 token 下标和音频时间'
    boundaries = []
    token_count = 0
    for item in result_list:
        if item.get('type') == CHUNK_TYPE:
            boundaries.append({
                'token_index': token_count,
                'time': item['time'],
                'line_no': item.get('line_no'),
            })
        elif item.get('pron'):
            token_count += 1
    return boundaries

def assign_comment_timelines(result_list, pretime=20, posttime=20):
    '将注释段放到前一句歌词 ASS 段之后，并尽量避开下一句歌词'
    last_reserved_end = None
    for i, item in enumerate(result_list):
        if item.get('type') != COMMENT_TYPE:
            continue

        next_start = None
        for next_item in result_list[i + 1:]:
            if is_timed_lyric(next_item):
                next_start = parse_time_to_hundredths(next_item['start'])
                break

        prev_end = previous_dialogue_tail(result_list, i)
        start_time = (prev_end + posttime) if prev_end is not None else 0
        if last_reserved_end is not None:
            start_time = max(start_time, last_reserved_end)

        end_time = start_time + item['duration']
        if next_start is not None:
            latest_end = max(next_start - pretime, 0)
            if end_time > latest_end:
                end_time = latest_end
                if end_time <= start_time:
                    item['skip'] = True
                    print(f"Skipped comment segment '{preview_comment_text(item['orig'])}' because there is no gap before the next lyric.")
                    continue
                print(f"Shortened comment segment '{preview_comment_text(item['orig'])}' to avoid overlapping the next lyric.")

        item['start'] = format_hundredths_to_time_str(start_time)
        item['end'] = format_hundredths_to_time_str(end_time)
        last_reserved_end = end_time

def parse_index_range(raw_range):
    match = re.match(r'^\s*#?(\d+)\s*$', raw_range or '')
    if match:
        value = int(match.group(1))
        return value, value
    match = re.match(r'^\s*#?(\d+)\s*(?:-|~|:|to|到|至)\s*#?(\d+)\s*$', raw_range or '', re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid range: {raw_range}. Use a form like 227-245.")
    start_index = int(match.group(1))
    end_index = int(match.group(2))
    if start_index <= 0 or end_index <= 0 or end_index < start_index:
        raise ValueError(f"Invalid range: {raw_range}. Range indexes must be positive and increasing.")
    return start_index, end_index

def parse_realign_time_value(raw_time):
    value = raw_time.strip().lower()
    for suffix in ('seconds', 'second', 'secs', 'sec', 's', '秒'):
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
            break
    parts = value.split(':')
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"Invalid time value: {raw_time}") from exc
    if seconds < 0:
        raise ValueError(f"Time value cannot be negative: {raw_time}")
    return seconds

def parse_realign_time_range(raw_range):
    match = re.match(r'^\s*(.+?)\s*(?:-|~|to|到|至)\s*(.+?)\s*$', raw_range or '', re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid audio range: {raw_range}. Use a form like 17:49-18:20 or 00:17:49-00:18:20.")
    start_time = parse_realign_time_value(match.group(1))
    end_time = parse_realign_time_value(match.group(2))
    if end_time <= start_time:
        raise ValueError(f"Invalid audio range: {raw_range}. End time must be after start time.")
    return start_time, end_time

def parse_ass_time_to_seconds(time_str):
    match = re.match(r'^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d{1,2}))?\s*$', time_str)
    if not match:
        raise ValueError(f"Invalid ASS time value: {time_str}")
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    centiseconds = int((match.group(4) or '0').ljust(2, '0')[:2])
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100

def format_seconds_to_ass_time(seconds):
    total_centiseconds = max(0, int(round(seconds * 100)))
    hours = total_centiseconds // 360000
    total_centiseconds %= 360000
    minutes = total_centiseconds // 6000
    total_centiseconds %= 6000
    secs = total_centiseconds // 100
    centiseconds = total_centiseconds % 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"

def format_seconds_for_log(seconds):
    total_centiseconds = int(round(seconds * 100))
    hours = total_centiseconds // 360000
    total_centiseconds %= 360000
    minutes = total_centiseconds // 6000
    total_centiseconds %= 6000
    secs = total_centiseconds // 100
    centiseconds = total_centiseconds % 100
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"
    return f"{minutes}:{secs:02d}.{centiseconds:02d}"

def resolve_io_path(real_io_path, path_value):
    if os.path.isabs(path_value):
        return os.path.normpath(path_value)
    return os.path.normpath(os.path.join(real_io_path, path_value))

def ensure_parent_dir(path):
    parent_dir = os.path.dirname(path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir)

def derive_sibling_path(source_path, suffix, default_ext=None):
    directory, filename = os.path.split(source_path)
    stem, ext = os.path.splitext(filename)
    if not stem:
        stem = filename or 'output'
    if not ext and default_ext:
        ext = default_ext
    return os.path.normpath(os.path.join(directory, f"{stem}{suffix}{ext}"))

def get_work_name(active_io_path):
    normalized = os.path.normpath(active_io_path)
    name = os.path.basename(normalized)
    return name or 'output'

def list_audio_files(active_io_path):
    if not os.path.isdir(active_io_path):
        return []
    paths = []
    for name in os.listdir(active_io_path):
        path = os.path.join(active_io_path, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
            paths.append(os.path.normpath(path))
    return sorted(paths)

def list_lyric_text_files(active_io_path, pronunciation_file):
    if not os.path.isdir(active_io_path):
        return []
    pronunciation_name = os.path.basename(pronunciation_file) if pronunciation_file else ''
    excluded_names = {pronunciation_name.lower(), 'readme.txt'}
    paths = []
    for name in os.listdir(active_io_path):
        path = os.path.join(active_io_path, name)
        lower_name = name.lower()
        if not os.path.isfile(path) or os.path.splitext(name)[1].lower() != '.txt':
            continue
        if lower_name in excluded_names or lower_name.endswith('_realign.txt'):
            continue
        paths.append(os.path.normpath(path))
    return sorted(paths)

def rename_work_file(source_path, target_path, label):
    source_path = os.path.normpath(source_path)
    target_path = os.path.normpath(target_path)
    if os.path.normcase(os.path.abspath(source_path)) == os.path.normcase(os.path.abspath(target_path)):
        return target_path
    if os.path.exists(target_path):
        raise FileExistsError(
            f"Cannot rename {label} file {source_path} to {target_path}: target already exists."
        )
    os.rename(source_path, target_path)
    print(f"Renamed {label} file to {target_path}")
    return target_path

def resolve_default_text_path(active_io_path, pronunciation_file):
    work_name = get_work_name(active_io_path)
    preferred_paths = [
        os.path.join(active_io_path, 'i.txt'),
        os.path.join(active_io_path, f"{work_name}.txt"),
    ]
    for path in preferred_paths:
        if os.path.exists(path):
            return os.path.normpath(path)

    candidates = list_lyric_text_files(active_io_path, pronunciation_file)
    if len(candidates) == 1:
        return candidates[0]

    if candidates:
        names = ', '.join(os.path.basename(path) for path in candidates)
        raise FileNotFoundError(
            "无法自动选择输入歌词。工作文件夹内有多个歌词 txt："
            f"{names}。请使用 -it/--input_text 指定。"
        )
    raise FileNotFoundError(
        "未找到输入歌词文件。请放置 <工作文件夹名>.txt、i.txt，"
        "或使用 -it/--input_text 指定。"
    )

def normalize_default_text_path(active_io_path, input_text_path):
    target_path = os.path.join(active_io_path, f"{get_work_name(active_io_path)}.txt")
    return rename_work_file(input_text_path, target_path, 'lyric text')

def resolve_input_audio_path(active_io_path, user_audio_path, input_text_path):
    if user_audio_path:
        return resolve_io_path(active_io_path, user_audio_path)

    text_dir, text_name = os.path.split(input_text_path)
    text_stem, _ = os.path.splitext(text_name)
    candidates = []
    if text_stem:
        candidates.extend(os.path.join(text_dir, f"{text_stem}{ext}") for ext in AUDIO_EXTENSIONS)
    candidates.extend(resolve_io_path(active_io_path, name) for name in ('i.wav', 'i.mp3'))

    seen = set()
    unique_candidates = []
    for candidate in candidates:
        normalized = os.path.normpath(candidate)
        if normalized not in seen:
            seen.add(normalized)
            unique_candidates.append(normalized)

    for candidate in unique_candidates:
        if os.path.exists(candidate):
            return candidate

    audio_candidates = list_audio_files(active_io_path)
    if len(audio_candidates) == 1:
        return audio_candidates[0]

    if audio_candidates:
        names = ', '.join(os.path.basename(path) for path in audio_candidates)
        raise FileNotFoundError(
            "无法自动选择输入音频。工作文件夹内有多个音频文件："
            f"{names}。请使用 -ia/--input_audio 指定。"
        )

    candidate_text = ', '.join(unique_candidates)
    raise FileNotFoundError(
        "未找到输入音频文件。请使用 -ia/--input_audio 指定，"
        f"或放置以下任一文件：{candidate_text}"
    )

def normalize_default_audio_path(active_io_path, input_audio_path):
    _, ext = os.path.splitext(input_audio_path)
    target_path = os.path.join(active_io_path, f"{get_work_name(active_io_path)}{ext or '.wav'}")
    return rename_work_file(input_audio_path, target_path, 'audio')

def probe_audio_sample_rate(input_audio_path):
    command = [
        'ffprobe',
        '-v',
        'error',
        '-select_streams',
        'a:0',
        '-show_entries',
        'stream=sample_rate',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        input_audio_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"ffprobe failed to inspect audio {input_audio_path}: {message}")
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"ffprobe did not return a sample rate for {input_audio_path}") from exc

def decode_audio_with_ffmpeg(input_audio_path):
    sample_rate = probe_audio_sample_rate(input_audio_path)
    command = [
        'ffmpeg',
        '-hide_banner',
        '-loglevel',
        'error',
        '-i',
        input_audio_path,
        '-map',
        '0:a:0',
        '-vn',
        '-ac',
        '1',
        '-f',
        'f32le',
        '-',
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        message = result.stderr.decode('utf-8', 'replace').strip()
        raise RuntimeError(f"ffmpeg failed to decode audio {input_audio_path}: {message}")
    audio = np.frombuffer(result.stdout, dtype=np.float32).copy()
    if audio.size == 0:
        raise RuntimeError(f"ffmpeg decoded no audio samples from {input_audio_path}")
    return audio, sample_rate

def load_audio_file(input_audio_path):
    try:
        return decode_audio_with_ffmpeg(input_audio_path)
    except Exception as exc:
        print(f"ffmpeg could not decode {input_audio_path}: {exc}")
        print("Loading audio with librosa fallback...")
        return librosa.load(input_audio_path, sr=None)

def split_line_ending(line):
    if line.endswith('\r\n'):
        return line[:-2], '\r\n'
    if line.endswith('\n'):
        return line[:-1], '\n'
    return line, ''

def parse_ass_event_line(line_body):
    if line_body.startswith('Dialogue:'):
        event_type = 'Dialogue'
        rest = line_body[len('Dialogue:'):].lstrip()
    elif line_body.startswith('Comment:'):
        event_type = 'Comment'
        rest = line_body[len('Comment:'):].lstrip()
    else:
        return None
    fields = rest.split(',', 9)
    if len(fields) != 10:
        return None
    return {
        'event_type': event_type,
        'fields': fields,
        'start': fields[1],
        'end': fields[2],
        'effect': fields[8],
        'text': fields[9],
    }

def format_ass_event_line(event_type, fields, newline):
    return f"{event_type}: {','.join(fields[:9])},{fields[9]}{newline}"

def load_ass_events(ass_path):
    with open(ass_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    events = []
    event_no = 0
    karaoke_no = 0
    for line_index, line in enumerate(lines):
        line_body, newline = split_line_ending(line)
        event = parse_ass_event_line(line_body)
        if event is None:
            continue
        event_no += 1
        is_karaoke = event['event_type'] == 'Dialogue' and event['effect'].strip().lower() == 'karaoke'
        if is_karaoke:
            karaoke_no += 1
        event.update({
            'line_index': line_index,
            'line_no': line_index + 1,
            'event_no': event_no,
            'karaoke_no': karaoke_no if is_karaoke else None,
            'is_karaoke': is_karaoke,
            'newline': newline,
        })
        events.append(event)
    return lines, events

def select_ass_karaoke_events(events, index_range, range_mode):
    start_index, end_index = index_range
    if range_mode == 'event':
        event_rows = [event for event in events if start_index <= event['event_no'] <= end_index]
        selected = [event for event in event_rows if event['is_karaoke']]
        skipped = len(event_rows) - len(selected)
        if skipped:
            print(f"Skipped {skipped} non-karaoke ASS event row(s) inside the requested range.")
    else:
        selected = [
            event for event in events
            if event['is_karaoke'] and start_index <= event['karaoke_no'] <= end_index
        ]
    if not selected:
        raise ValueError("The requested ASS range did not contain karaoke Dialogue rows.")
    return selected

def derive_realign_time_range_from_neighbors(events, selected_events):
    if not selected_events:
        raise ValueError("Cannot auto-detect time range because no karaoke rows were selected.")

    karaoke_events = [event for event in events if event['is_karaoke']]
    first_selected = selected_events[0]
    last_selected = selected_events[-1]
    first_index = None
    last_index = None
    for i, event in enumerate(karaoke_events):
        if event['line_index'] == first_selected['line_index']:
            first_index = i
        if event['line_index'] == last_selected['line_index']:
            last_index = i
    if first_index is None or last_index is None:
        raise ValueError("Unable to locate the selected karaoke row(s).")
    if first_index == 0:
        raise ValueError("Cannot auto-detect time range because the selected range starts at the first karaoke row.")
    if last_index == len(karaoke_events) - 1:
        raise ValueError("Cannot auto-detect time range because the selected range ends at the last karaoke row.")

    previous_event = karaoke_events[first_index - 1]
    next_event = karaoke_events[last_index + 1]
    audio_start = parse_ass_time_to_seconds(previous_event['end'])
    audio_end = parse_ass_time_to_seconds(next_event['start'])
    if audio_end <= audio_start:
        raise ValueError(
            "Cannot auto-detect a valid time range: previous karaoke end is not before next karaoke start. "
            "Please pass --realign_time manually."
        )
    return audio_start, audio_end, previous_event, next_event

def ass_unescape_text(text):
    return (text
            .replace(r'\N', '\n')
            .replace(r'\n', '\n')
            .replace(r'\h', ' ')
            .replace(r'\{', '{')
            .replace(r'\}', '}')
            .replace(r'\\', '\\'))

def ass_karaoke_text_to_input_line(ass_text):
    chunks = ASS_K_TAG_RE.split(ass_text)

    result = []
    ruby_surface = None
    ruby_parts = []

    def flush_ruby():
        nonlocal ruby_surface, ruby_parts
        if ruby_surface is not None:
            result.append('{' + ruby_surface + '|' + ''.join(ruby_parts) + '}')
            ruby_surface = None
            ruby_parts = []

    for chunk in chunks:
        chunk = ass_unescape_text(ASS_TAG_RE.sub('', chunk))
        if not chunk:
            continue

        ruby_match = re.match(r'^(.*?)\|<(.+)$', chunk, re.S)
        if ruby_match:
            flush_ruby()
            ruby_surface = ruby_match.group(1)
            ruby_parts = [ruby_match.group(2)]
            continue

        ruby_tail_match = re.match(r'^#\|(.+)$', chunk, re.S)
        if ruby_tail_match and ruby_surface is not None:
            ruby_parts.append(ruby_tail_match.group(1))
            continue

        flush_ruby()
        result.append(chunk)

    flush_ruby()
    return ''.join(result).rstrip('\r\n')

def load_text_lyric_records(input_text_path):
    with open(input_text_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    records = []
    for line_index, line in enumerate(lines):
        line_body, newline = split_line_ending(line)
        if not line_body.strip():
            continue
        if COMMENT_RE.match(line_body) or CHUNK_RE.match(line_body):
            continue
        records.append({
            'line_index': line_index,
            'line_no': line_index + 1,
            'text': line_body,
            'newline': newline,
        })
    return lines, records

def prepare_synced_text(input_text_path, real_io_path, output_text_name, update_text, selected_events, source_texts):
    lines, lyric_records = load_text_lyric_records(input_text_path)
    if not selected_events:
        return None
    first_karaoke_no = selected_events[0]['karaoke_no']
    last_karaoke_no = selected_events[-1]['karaoke_no']
    if last_karaoke_no > len(lyric_records):
        raise ValueError(
            f"ASS karaoke line #{last_karaoke_no} has no matching lyric line in {os.path.basename(input_text_path)}."
        )

    changed = 0
    first_record = lyric_records[first_karaoke_no - 1]
    last_record = lyric_records[last_karaoke_no - 1]
    for event, source_text in zip(selected_events, source_texts):
        record = lyric_records[event['karaoke_no'] - 1]
        if record['text'] == source_text:
            continue
        newline = record['newline'] or '\n'
        lines[record['line_index']] = source_text + newline
        changed += 1

    print(
        f"Matched ASS karaoke #{first_karaoke_no}-#{last_karaoke_no} "
        f"to {os.path.basename(input_text_path)} lines {first_record['line_no']}-{last_record['line_no']}."
    )

    if not changed:
        print("No lyric text changes were needed.")
        return None

    target_path = input_text_path if update_text else resolve_io_path(real_io_path, output_text_name)
    return {
        'lines': lines,
        'target_path': target_path,
        'input_text_path': input_text_path,
        'changed': changed,
        'update_text': update_text,
    }

def write_synced_text(sync_plan):
    if not sync_plan:
        return
    ensure_parent_dir(sync_plan['target_path'])
    with open(sync_plan['target_path'], 'w', encoding='utf-8', newline='') as file:
        file.writelines(sync_plan['lines'])
    changed = sync_plan['changed']
    input_text_name = os.path.basename(sync_plan['input_text_path'])
    if sync_plan['update_text']:
        print(f"Updated {input_text_name} from the selected ASS text ({changed} line(s)).")
    else:
        print(f"Wrote synced lyric text to {sync_plan['target_path']} ({changed} changed line(s)).")

def crop_ranges_to_window(ranges, start_time, end_time):
    cropped = []
    for range_start, range_end in ranges:
        cropped_start = max(range_start, start_time)
        cropped_end = min(range_end, end_time)
        if cropped_end > cropped_start:
            cropped.append((cropped_start, cropped_end))
    if not cropped:
        cropped.append((start_time, end_time))
    return cropped

def crop_ranges_to_limits(ranges, start_time, end_time):
    cropped = []
    for range_start, range_end in ranges:
        cropped_start = max(range_start, start_time)
        cropped_end = min(range_end, end_time)
        if cropped_end > cropped_start:
            cropped.append((cropped_start, cropped_end))
    return cropped

def apply_tail_pronunciation(result_list, tail_correct):
    if tail_correct == 1:
        for i in range(len(result_list)):
            if result_list[i]['type']==0:
                try:
                    if result_list[i-1].get('pron') and result_list[i-1]['type']!=0:
                        pre_vowel = result_list[i-1]['pron'][-1]
                        post_consonant = ''
                        if i < len(result_list)-1:
                            post_i = i + 1
                            while post_i < len(result_list):
                                if 'pron' in result_list[post_i] and len(result_list[post_i]['pron'])>=1:
                                    post_consonant = result_list[post_i]['pron'][0]
                                    break
                                else:
                                    post_i += 1
                        if pre_vowel!=post_consonant and post_consonant not in ('a', 'e', 'i', 'o', 'u'):
                            result_list[i]['pron'] = pre_vowel + 'h'
                except:
                    continue
    elif tail_correct == 2:
        for i in range(len(result_list)):
            if result_list[i]['type']==0:
                try:
                    if len(result_list[i-1]['pron'])>=1 and result_list[i-1]['type']!=0:
                        result_list[i]['pron'] = result_list[i-1]['pron'][-1] + 'h'
                except:
                    continue

def apply_tail_timing_correction(result_list, audio_file, sr, tail_thres_pct, tail_thres_ratio, limit_range=None):
    ns_small = non_silent_recog(audio_file, sr, .02, tail_thres_pct, tail_thres_ratio)
    if limit_range is not None:
        limit_start, limit_end = limit_range
        ns_small = crop_ranges_to_limits(ns_small, limit_start, limit_end)
        if not ns_small:
            ns_small = [(limit_start, limit_end)]
    ns_ends = [int(np.ceil(ns_end * 100)) for _, ns_end in ns_small]
    for i in range(len(result_list)-1):
        if result_list[i].get('type') in LYRIC_TYPES and result_list[i+1].get('type') == 0:
            current_end = parse_time_to_hundredths(result_list[i]['end'])
            next_ind = i + 2
            next_start = np.inf
            while next_ind < len(result_list):
                if 'start' in result_list[next_ind]:
                    next_start = parse_time_to_hundredths(result_list[next_ind]['start'])
                    break
                next_ind += 1
            left_index = bisect.bisect_left(ns_ends, current_end)
            right_index = bisect.bisect_left(ns_ends, next_start)
            if left_index < right_index and left_index < len(ns_ends):
                result_list[i]['end'] = format_hundredths_to_time_str(ns_ends[left_index])
            else:
                interval_covered = False
                for nss_start, nss_end in ns_small:
                    if int(nss_start * 100) > current_end:
                        break
                    if int(nss_start * 100) <= current_end and int(np.ceil(nss_end * 100)) >= next_start:
                        interval_covered = True
                        break
                if interval_covered:
                    result_list[i]['end'] = format_hundredths_to_time_str(max(next_start-2, current_end))

def build_result_list_from_source_lines(source_lines, lrc_language, sokuon_split, hatsuon_split, tail_correct):
    result_list = []
    for source_line in source_lines:
        result_list.extend(hn.process_haruhi_line(source_line + '\n', lrc_language, sokuon_split, hatsuon_split))
    if result_list and result_list[-1].get('type') != COMMENT_TYPE and result_list[-1]['orig']!='\n':
        result_list.append({'orig': '\n', 'type': 0, 'pron': ''})
    apply_tail_pronunciation(result_list, tail_correct)
    return result_list

def collect_alignment_tokens(result_list):
    alignment_tokens = []
    token_to_index_map = {}
    for i, item in enumerate(result_list):
        if 'pron' in item and item['pron']:
            alignment_tokens.append(item['pron'])
            token_to_index_map[len(alignment_tokens) - 1] = i
    return alignment_tokens, token_to_index_map

def align_result_list_timings(result_list, audio_file, sr, non_silent_ranges, audio_speed, chunk_seconds):
    alignment_tokens, token_to_index_map = collect_alignment_tokens(result_list)
    if not alignment_tokens:
        raise ValueError("The selected ASS text did not produce any alignment tokens.")

    for item in alignment_tokens:
        if not hn.is_english(item):
            print(f"alignment_tokens可能包含错误数据{item}")

    sentence_token_spans = get_alignment_sentence_spans(result_list)
    use_chunked_alignment = chunk_seconds > 0
    if audio_speed == 1:
        print('Adding timelines...')
        if use_chunked_alignment:
            alignment_results = align.align_audio_with_text_chunked(
                audio_file,
                alignment_tokens,
                non_silent_ranges,
                sr,
                audio_speed,
                chunk_seconds,
                sentence_token_spans,
                None,
            )
        else:
            alignment_results = align.align_audio_with_text(audio_file, alignment_tokens, non_silent_ranges, sr)
    else:
        print('Changing the audio speed...')
        start_time = time.time()
        y_processed = librosa.effects.time_stretch(audio_file, rate=audio_speed)
        end_time = time.time()
        print("Audio speed changing executed in", round(end_time - start_time, 3), "seconds")
        print('Adding timelines...')
        if use_chunked_alignment:
            alignment_results = align.align_audio_with_text_chunked(
                y_processed,
                alignment_tokens,
                non_silent_ranges,
                sr,
                audio_speed,
                chunk_seconds,
                sentence_token_spans,
                None,
            )
        else:
            alignment_results = align.align_audio_with_text(y_processed, alignment_tokens, non_silent_ranges, sr, audio_speed)

    for i, result in enumerate(alignment_results):
        if i in token_to_index_map:
            original_index = token_to_index_map[i]
            result_list[original_index]['start'] = result['start']
            result_list[original_index]['end'] = result['end']
    return result_list

def parse_generated_dialogues(ass_output):
    dialogues = []
    for line in ass_output.splitlines():
        event = parse_ass_event_line(line)
        if event and event['event_type'] == 'Dialogue':
            dialogues.append(event)
    return dialogues

def load_realign_text_file(path):
    with open(path, 'r', encoding='utf-8') as file:
        return [line.rstrip('\r\n') for line in file if line.strip()]

def clamp_ass_event_times(fields, min_start, max_end):
    start_time = max(parse_ass_time_to_seconds(fields[1]), min_start)
    end_time = min(parse_ass_time_to_seconds(fields[2]), max_end)
    if end_time <= start_time:
        end_time = min(max_end, start_time + 0.01)
    fields[1] = format_seconds_to_ass_time(start_time)
    fields[2] = format_seconds_to_ass_time(end_time)

def build_replacement_ass_lines(selected_events, replacement_dialogues, audio_start, audio_end):
    lines = []
    for i, replacement_event in enumerate(replacement_dialogues):
        template_event = selected_events[i % len(selected_events)]
        fields = template_event['fields'][:]
        fields[1] = replacement_event['fields'][1]
        fields[2] = replacement_event['fields'][2]
        fields[9] = replacement_event['fields'][9]
        clamp_ass_event_times(fields, audio_start, audio_end)
        lines.append(format_ass_event_line(
            template_event['event_type'],
            fields,
            template_event['newline'] or '\n',
        ))
    return lines

def run_partial_realign(args, real_io_path, input_audio_path, input_text_path, lrc_language,
                        sokuon_split, hatsuon_split, tail_correct, silent_window_s,
                        tail_thres_pct, tail_thres_ratio, audio_speed, chunk_seconds):
    ass_input_path = resolve_io_path(real_io_path, args.realign_ass)
    if args.realign_inplace:
        ass_output_path = ass_input_path
    elif args.realign_output:
        ass_output_path = resolve_io_path(real_io_path, args.realign_output)
    else:
        ass_output_path = derive_sibling_path(ass_input_path, '_realign', '.ass')
    index_range = parse_index_range(args.realign)

    ass_lines, ass_events = load_ass_events(ass_input_path)
    selected_events = select_ass_karaoke_events(ass_events, index_range, args.realign_mode)
    if args.realign_time:
        audio_start, audio_end = parse_realign_time_range(args.realign_time)
    else:
        audio_start, audio_end, previous_event, next_event = derive_realign_time_range_from_neighbors(
            ass_events,
            selected_events,
        )
        print(
            "Auto-detected realign range from "
            f"previous #{previous_event['event_no']} end to next #{next_event['event_no']} start."
        )
    external_text_path = resolve_io_path(real_io_path, args.realign_text_file) if args.realign_text_file else None
    if external_text_path:
        source_lines = load_realign_text_file(external_text_path)
        if not source_lines:
            raise ValueError(f"No lyric lines found in {external_text_path}.")
        print(f"Loaded {len(source_lines)} replacement lyric line(s) from {external_text_path}.")
    else:
        source_lines = [ass_karaoke_text_to_input_line(event['text']) for event in selected_events]

    print(
        f"Realigning {len(selected_events)} ASS karaoke line(s) "
        f"against {os.path.basename(input_audio_path)} "
        f"{format_seconds_for_log(audio_start)}-{format_seconds_for_log(audio_end)}."
    )
    text_sync_plan = None
    if external_text_path:
        if args.realign_update_text:
            raise ValueError('--realign_update_text is not supported together with --realign_text_file.')
    else:
        realign_text_output = args.realign_text_output or derive_sibling_path(input_text_path, '_realign', '.txt')
        text_sync_plan = prepare_synced_text(
            input_text_path,
            real_io_path,
            realign_text_output,
            args.realign_update_text,
            selected_events,
            source_lines,
        )

    result_list = build_result_list_from_source_lines(
        source_lines,
        lrc_language,
        sokuon_split,
        hatsuon_split,
        tail_correct,
    )
    unknown_english_words = hn.get_unknown_english_words()
    if unknown_english_words:
        print("Unknown English words found. Add them to pronunciations.txt if the guessed pronunciation is wrong:")
        for word in unknown_english_words:
            print(f"  {word}=<romaji>")

    print('Loading audio...')
    audio_file, sr = load_audio_file(input_audio_path)
    non_silent_ranges = non_silent_recog(audio_file, sr, silent_window_s, tail_thres_pct, tail_thres_ratio)
    realign_ranges = crop_ranges_to_window(non_silent_ranges, audio_start, audio_end)

    result_list = align_result_list_timings(
        result_list,
        audio_file,
        sr,
        realign_ranges,
        audio_speed,
        chunk_seconds,
    )
    result_list = non_silent_head_adjust(result_list, realign_ranges)
    if tail_correct == 3:
        apply_tail_timing_correction(
            result_list,
            audio_file,
            sr,
            tail_thres_pct,
            tail_thres_ratio,
            (audio_start, audio_end),
        )

    ass_output = norm2ass.process_norm2assV2(result_list, 20, 20)
    replacement_dialogues = parse_generated_dialogues(ass_output)
    if not external_text_path and len(replacement_dialogues) != len(selected_events):
        raise ValueError(
            f"Expected {len(selected_events)} regenerated ASS lines, got {len(replacement_dialogues)}. "
            "The selected ASS range may not map one-to-one to lyric lines."
        )

    replacement_lines = build_replacement_ass_lines(
        selected_events,
        replacement_dialogues,
        audio_start,
        audio_end,
    )
    replace_start = selected_events[0]['line_index']
    replace_end = selected_events[-1]['line_index']
    ass_lines[replace_start:replace_end + 1] = replacement_lines

    ensure_parent_dir(ass_output_path)
    with open(ass_output_path, 'w', encoding='utf-8', newline='') as file:
        file.writelines(ass_lines)
    print(f"Wrote realigned ASS to {ass_output_path}")
    write_synced_text(text_sync_plan)
    print('Success!')

def batch_song_has_input(song_path, pronunciation_file):
    text_candidates = list_lyric_text_files(song_path, pronunciation_file)
    preferred_texts = [
        os.path.join(song_path, f"{get_work_name(song_path)}.txt"),
        os.path.join(song_path, 'i.txt'),
    ]
    has_text = any(os.path.exists(path) for path in preferred_texts) or bool(text_candidates)
    has_audio = bool(list_audio_files(song_path))
    return has_text and has_audio

def get_batch_done_ass_path(song_path):
    for ass_name in (f"{get_work_name(song_path)}.ass", 'o.ass'):
        ass_path = os.path.join(song_path, ass_name)
        if os.path.exists(ass_path):
            return os.path.normpath(ass_path)
    return None

def iter_song_work_dirs(songs_root_path, pronunciation_file, skip_done=True):
    with os.scandir(songs_root_path) as entries:
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith('.'):
                continue
            song_path = os.path.normpath(entry.path)
            done_ass_path = get_batch_done_ass_path(song_path)
            if skip_done and done_ass_path:
                print(f"Skipped {song_path}: already has {os.path.basename(done_ass_path)}.")
                continue
            if batch_song_has_input(song_path, pronunciation_file):
                yield song_path
            else:
                print(f"Skipped {song_path}: no usable lyric/audio pair found.")

def append_arg(command, option, value):
    command.extend([option, str(value)])

def build_batch_child_command(args, script_path, song_path):
    command = [sys.executable, '-B', script_path, '-p', song_path]
    append_arg(command, '-x', args.sokuon_split)
    append_arg(command, '-n', args.hatsuon_split)
    append_arg(command, '-v', args.audio_speedx)
    append_arg(command, '-t', args.tail_correct)
    append_arg(command, '-tl', args.tail_limit_window)
    append_arg(command, '-tp', args.tail_thres_pct)
    append_arg(command, '-tr', args.tail_thres_ratio)
    append_arg(command, '--offset', args.offset)
    append_arg(command, '--bpm', args.bpm)
    append_arg(command, '--bpb', args.bpb)
    append_arg(command, '--lang', args.lang)
    append_arg(command, '-f', args.txt_format)
    append_arg(command, '-cl', args.characters_per_line)
    append_arg(command, '-cs', args.chunk_seconds)
    if args.pronunciation_file:
        append_arg(command, '--pronunciation_file', args.pronunciation_file)
    return command

def validate_batch_args(args):
    unsupported = []
    if args.input_audio:
        unsupported.append('--input_audio')
    if args.input_text:
        unsupported.append('--input_text')
    if args.output_ass:
        unsupported.append('--output_ass')
    if args.output_rlf:
        unsupported.append('--output_rlf')
    if args.output_ruby:
        unsupported.append('--output_ruby')
    if args.realign or args.realign_time:
        unsupported.append('--realign/--realign_time')
    if args.realign_ass:
        unsupported.append('--realign_ass')
    if args.realign_output:
        unsupported.append('--realign_output')
    if args.realign_text_file:
        unsupported.append('--realign_text_file')
    if args.realign_inplace:
        unsupported.append('--realign_inplace')
    if args.realign_update_text:
        unsupported.append('--realign_update_text')
    if args.realign_text_output:
        unsupported.append('--realign_text_output')
    if unsupported:
        raise ValueError(
            "Batch songs mode uses each song folder's default files; unsupported options: "
            + ', '.join(unsupported)
        )

def run_batch_songs(args, script_dir):
    validate_batch_args(args)
    songs_root_value = args.songs_dir or args.path_io or 'songs'
    songs_root_path = os.path.normpath(songs_root_value) if os.path.isabs(songs_root_value) else os.path.normpath(os.path.join(script_dir, songs_root_value))
    if not os.path.isdir(songs_root_path):
        raise FileNotFoundError(f"Songs folder not found: {songs_root_path}")

    print(f"Batch songs folder: {songs_root_path}")
    processed = 0
    failed = 0
    script_path = os.path.realpath(__file__)
    for song_path in iter_song_work_dirs(
        songs_root_path,
        args.pronunciation_file,
        skip_done=not args.batch_force,
    ):
        processed += 1
        print(f"\n[{processed}] Processing {song_path}")
        command = build_batch_child_command(args, script_path, song_path)
        result = subprocess.run(command)
        if result.returncode:
            failed += 1
            print(f"Failed {song_path} with exit code {result.returncode}.")
            if not args.batch_continue_on_error:
                raise SystemExit(result.returncode)

    if processed == 0:
        print("No pending song folders with both lyric text and audio were found.")
    elif failed:
        print(f"Batch finished with {failed}/{processed} failed song(s).")
        raise SystemExit(1)
    else:
        print(f"Batch finished successfully: {processed} song(s).")

def main():
    start_time = time.time()
    script_dir = os.path.dirname(os.path.realpath(__file__))
    parser = argparse.ArgumentParser(description='可选参数')
    parser.add_argument('-x', '--sokuon_split', type=int, default=0, help='是否将促音与前一字符拆开')
    parser.add_argument('-n', '--hatsuon_split', type=int, default=1, help='是否将拨音与前一字符拆开')
    parser.add_argument('-v', '--audio_speedx', type=float, default=1, help='推理时使用的音频倍速')
    parser.add_argument('-p', '--path_io', '--work_dir', default='', help='工作文件夹。基于主文件所在目录，支持绝对路径或相对路径')
    parser.add_argument('-ia', '--input_audio', default=None, help='输入音频文件名')
    parser.add_argument('-it', '--input_text', default=None, help='输入歌词文件名。默认自动选择并规范为 <工作文件夹名>.txt')
    parser.add_argument('-o', '--output', '--output_ass', dest='output_ass', default=None, help='输出 ASS 文件名。默认 <工作文件夹名>.ass')
    parser.add_argument('--output_rlf', default=None, help='输出 RhythmicaLyrics LRC 文件名。默认 <工作文件夹名>_rlf.lrc')
    parser.add_argument('--output_ruby', default=None, help='输出 ruby LRC 文件名。默认 <工作文件夹名>_ruby.lrc')
    parser.add_argument('-t', '--tail_correct', type=int, default=3, help='尾音拖长选项。建议取默认值3')
    parser.add_argument('-tl', '--tail_limit_window', type=float, default=0.8, help='全曲静音检测窗口时长，单位：秒')
    parser.add_argument('-tp', '--tail_thres_pct', type=float, default=10, help='尾音阈值百分位数，单位：％。以音频能量前“百分位数”的一定比例作为静音检测阈值')
    parser.add_argument('-tr', '--tail_thres_ratio', type=float, default=0.1, help='尾音阈值比例。以音频能量前百分位数的一定“比例”作为静音检测阈值')
    parser.add_argument('--offset', type=int, default=-150, help='输出ruby歌词文件中Offset标签的偏移值')
    parser.add_argument('--bpm', type=float, default=60, help='歌曲的BPM，导唱指示灯用')
    parser.add_argument('--bpb', type=int, default=3, help='导唱指示灯的符号个数')
    parser.add_argument('--lang', default='auto', help='歌词语言')
    parser.add_argument('-f', '--txt_format', default='hrh', help='歌词文本格式')
    parser.add_argument('-cl', '--characters_per_line', type=int, default=0, help='输出文件每行最大字数')
    parser.add_argument('-cs', '--chunk_seconds', type=float, default=0, help='分块推理目标时长，单位：秒。0表示关闭自动分块')
    parser.add_argument('--pronunciation_file', default='pronunciations.txt', help='自定义英文发音表文件名。默认读取本次任务工作文件夹下的 pronunciations.txt')
    parser.add_argument('--batch_songs', action='store_true', help='顺序处理 songs 目录下的所有歌曲工作文件夹，一次只处理一首')
    parser.add_argument('--songs_dir', default=None, help='批量处理的歌曲根目录。默认使用 --work_dir；未指定 --work_dir 时使用 songs')
    parser.add_argument('--batch_continue_on_error', action='store_true', help='批量处理时某首失败后继续处理下一首')
    parser.add_argument('--batch_force', action='store_true', help='批量处理时不跳过已有 ASS 输出的歌曲文件夹')
    parser.add_argument('--normalize_work_files', action='store_true', help='将唯一输入歌词和音频改名为工作文件夹同名文件')
    parser.add_argument('--realign', '--realign_range', dest='realign', default=None, help='局部重对轴的 ASS 行范围，例如 227-245')
    parser.add_argument('--realign_time', '--realign_audio_range', dest='realign_time', default=None, help='局部重对轴使用的音频时间范围，例如 17:49-18:20；省略时自动用选区上一句结束到下一句开始')
    parser.add_argument('--realign_ass', default=None, help='局部重对轴读取的 ASS 文件名。默认使用 --output_ass')
    parser.add_argument('--realign_output', default=None, help='局部重对轴输出的 ASS 文件名。默认写到 <realign_ass>_realign.ass')
    parser.add_argument('--realign_mode', choices=('karaoke', 'event'), default='karaoke', help='ASS 范围编号方式：karaoke=歌词 Dialogue 行，event=Aegisub 事件行')
    parser.add_argument('--realign_text_file', default=None, help='局部重对轴使用的替换歌词文本文件；用于补缺行或替换为不同数量的歌词行')
    parser.add_argument('--realign_inplace', action='store_true', help='直接覆盖 --realign_ass 指定的 ASS 文件')
    parser.add_argument('--realign_update_text', action='store_true', help='用选中 ASS 歌词同步覆盖输入歌词对应行')
    parser.add_argument('--realign_text_output', default=None, help='未开启 --realign_update_text 时写出的同步歌词文件名。默认写到 <input_text>_realign.txt')
    args = parser.parse_args()
    if args.batch_songs:
        run_batch_songs(args, script_dir)
        return

    sokuon_split = args.sokuon_split
    hatsuon_split = args.hatsuon_split
    audio_speed = args.audio_speedx
    user_path = args.path_io
    user_audio_path = args.input_audio
    user_text_path = args.input_text
    tail_correct = args.tail_correct
    silent_window_s = args.tail_limit_window
    tail_thres_pct = args.tail_thres_pct
    tail_thres_ratio = args.tail_thres_ratio
    ruby_tag_offset = args.offset
    bpm = args.bpm
    beats_per_bar = args.bpb
    lrc_language = args.lang.lower()
    txt_format = args.txt_format.lower()
    output_characters_per_line = args.characters_per_line
    chunk_seconds = args.chunk_seconds
    pronunciation_file = args.pronunciation_file
    
    work_root_path = os.path.normpath(user_path) if os.path.isabs(user_path) else os.path.normpath(os.path.join(script_dir, user_path))
    if not os.path.exists(work_root_path):
        os.makedirs(work_root_path)
    if user_text_path:
        input_text_path = resolve_io_path(work_root_path, user_text_path)
        real_io_path = os.path.dirname(input_text_path) or work_root_path
        auto_text_path = False
    else:
        real_io_path = work_root_path
        input_text_path = resolve_default_text_path(real_io_path, pronunciation_file)
        auto_text_path = True
    if not os.path.exists(real_io_path):
        os.makedirs(real_io_path)
    auto_normalize_work_files = (
        args.normalize_work_files
        and auto_text_path
        and user_audio_path is None
        and len(list_lyric_text_files(real_io_path, pronunciation_file)) == 1
        and len(list_audio_files(real_io_path)) == 1
    )
    if auto_normalize_work_files:
        input_text_path = normalize_default_text_path(real_io_path, input_text_path)
    input_audio_path = resolve_input_audio_path(real_io_path, user_audio_path, input_text_path)
    if auto_normalize_work_files:
        input_audio_path = normalize_default_audio_path(real_io_path, input_audio_path)
    work_name = get_work_name(real_io_path)
    output_ass_name = args.output_ass or f"{work_name}.ass"
    output_rlf_name = args.output_rlf or f"{work_name}_rlf.lrc"
    output_ruby_name = args.output_ruby or f"{work_name}_ruby.lrc"
    output_ass_path = resolve_io_path(real_io_path, output_ass_name)
    output_rlf_path = resolve_io_path(real_io_path, output_rlf_name)
    output_ruby_path = resolve_io_path(real_io_path, output_ruby_name)
    pronunciation_path = resolve_io_path(real_io_path, pronunciation_file) if pronunciation_file else None
    if args.realign_ass is None:
        args.realign_ass = output_ass_name

    print('Loading files...')
    print(f"Working folder: {real_io_path}")
    hn.load_english_pronunciations(pronunciation_path)
    if args.realign or args.realign_time:
        if not args.realign:
            raise ValueError('Partial realign needs --realign.')
        run_partial_realign(
            args,
            real_io_path,
            input_audio_path,
            input_text_path,
            lrc_language,
            sokuon_split,
            hatsuon_split,
            tail_correct,
            silent_window_s,
            tail_thres_pct,
            tail_thres_ratio,
            audio_speed,
            chunk_seconds,
        )
        return
    result_list = []
    with open(input_text_path, 'r', encoding='utf-8') as file:
        if txt_format=='uta':
            utat_str = ''
            for line in file:
                utat_str += line
            file = lrcfmt.utat_process(utat_str)
        for line_no, line in enumerate(file, 1):
            if txt_format=='moe':
                line = lrcfmt.moeg_process_line(line)
            if line.strip():
                comment_items = parse_comment_line(line, line_no)
                if comment_items is not None:
                    result_list.extend(comment_items)
                    continue
                chunk_items = parse_chunk_line(line, line_no)
                if chunk_items is not None:
                    result_list.extend(chunk_items)
                    continue
                result_list.extend(hn.process_haruhi_line(line, lrc_language, sokuon_split, hatsuon_split))
    if result_list and result_list[-1].get('type') != COMMENT_TYPE and result_list[-1]['orig']!='\n':
        result_list.append({'orig': '\n', 'type': 0, 'pron': ''})

    if tail_correct == 1:
        for i in range(len(result_list)):
            if result_list[i]['type']==0:
                try:
                    if result_list[i-1].get('pron') and result_list[i-1]['type']!=0:
                        pre_vowel = result_list[i-1]['pron'][-1]
                        post_consonant = ''
                        if i < len(result_list)-1:
                            post_i = i + 1
                            while post_i < len(result_list):
                                if 'pron' in result_list[post_i] and len(result_list[post_i]['pron'])>=1:
                                    post_consonant = result_list[post_i]['pron'][0]
                                    break
                                else:
                                    post_i += 1
                        if pre_vowel!=post_consonant and post_consonant not in ('a', 'e', 'i', 'o', 'u'):
                            result_list[i]['pron'] = pre_vowel + 'h'
                except:
                    continue
    elif tail_correct == 2:
        for i in range(len(result_list)):
            if result_list[i]['type']==0:
                try: # 合理利用baseline尾音特性
                    if len(result_list[i-1]['pron'])>=1 and result_list[i-1]['type']!=0:
                        result_list[i]['pron'] = result_list[i-1]['pron'][-1] + 'h'
                except:
                    continue

    alignment_tokens = []
    token_to_index_map = {}
    for i, item in enumerate(result_list):
        if 'pron' in item and item['pron']:
            alignment_tokens.append(item['pron'])
            token_to_index_map[len(alignment_tokens) - 1] = i
    sentence_token_spans = get_alignment_sentence_spans(result_list)
    manual_chunk_boundaries = get_manual_chunk_boundaries(result_list)

    for item in alignment_tokens:
        if hn.is_english(item):
            continue
        else:
            print(f"alignment_tokens可能包含错误数据{item}")
    unknown_english_words = hn.get_unknown_english_words()
    if unknown_english_words:
        print("Unknown English words found. Add them to pronunciations.txt if the guessed pronunciation is wrong:")
        for word in unknown_english_words:
            print(f"  {word}=<romaji>")

    end_time = time.time()
    print("Lyrics text analysis executed in", round(end_time - start_time, 3), "seconds")

    audio_file, sr = load_audio_file(input_audio_path)
    non_silent_ranges = non_silent_recog(audio_file, sr, silent_window_s, tail_thres_pct, tail_thres_ratio)

    use_chunked_alignment = chunk_seconds > 0 or bool(manual_chunk_boundaries)
    if audio_speed == 1:
        print('Adding timelines...')
        if use_chunked_alignment:
            alignment_results = align.align_audio_with_text_chunked(
                audio_file,
                alignment_tokens,
                non_silent_ranges,
                sr,
                audio_speed,
                chunk_seconds,
                sentence_token_spans,
                manual_chunk_boundaries,
            )
        else:
            alignment_results = align.align_audio_with_text(audio_file, alignment_tokens, non_silent_ranges, sr)
    else:
        print('Changing the audio speed...')
        start_time = time.time()
        y_processed = librosa.effects.time_stretch(audio_file, rate=audio_speed)
        end_time = time.time()
        print("Audio speed changing executed in", round(end_time - start_time, 3), "seconds")
        print('Adding timelines...')
        if use_chunked_alignment:
            alignment_results = align.align_audio_with_text_chunked(
                y_processed,
                alignment_tokens,
                non_silent_ranges,
                sr,
                audio_speed,
                chunk_seconds,
                sentence_token_spans,
                manual_chunk_boundaries,
            )
        else:
            alignment_results = align.align_audio_with_text(y_processed, alignment_tokens, non_silent_ranges, sr, audio_speed)

    for i, result in enumerate(alignment_results):
        if i in token_to_index_map:
            original_index = token_to_index_map[i]
            result_list[original_index]['start'] = result['start']
            result_list[original_index]['end'] = result['end']

    result_list = non_silent_head_adjust(result_list, non_silent_ranges)
    
    if tail_correct == 3:
        ns_small = non_silent_recog(audio_file, sr, .02, tail_thres_pct, tail_thres_ratio)
        ns_ends = [int(np.ceil(ns_end * 100)) for _, ns_end in ns_small]
        for i in range(len(result_list)-1):
            if result_list[i].get('type') in LYRIC_TYPES and result_list[i+1].get('type') == 0:
                current_end = parse_time_to_hundredths(result_list[i]['end'])
                next_ind = i + 2
                next_start = np.inf
                while next_ind < len(result_list):
                    if 'start' in result_list[next_ind]:
                        next_start = parse_time_to_hundredths(result_list[next_ind]['start'])
                        break
                    next_ind += 1
                left_index = bisect.bisect_left(ns_ends, current_end)
                right_index = bisect.bisect_left(ns_ends, next_start)
                if left_index < right_index and left_index < len(ns_ends):
                    result_list[i]['end'] = format_hundredths_to_time_str(ns_ends[left_index])
                else:
                    interval_covered = False # 检查非静音段是否覆盖整个区间
                    for nss_start, nss_end in ns_small:
                        if int(nss_start * 100) > current_end:
                            break
                        if int(nss_start * 100) <= current_end and int(np.ceil(nss_end * 100)) >= next_start:
                            interval_covered = True
                            break
                    if interval_covered:
                        result_list[i]['end'] = format_hundredths_to_time_str(max(next_start-2, current_end))
    
    if output_characters_per_line > 0:
        split_long_segments(result_list, max_length=output_characters_per_line)

    ass_pretime = 20
    ass_posttime = 20
    assign_comment_timelines(result_list, ass_pretime, ass_posttime)

    main_output = process_main(result_list, ruby_tag_offset, bpm, beats_per_bar)
    ruby_output = process_ruby(result_list)
    content = f"{main_output}\n{ruby_output}"
    ensure_parent_dir(output_ruby_path)
    with open(output_ruby_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"Wrote ruby LRC to {output_ruby_path}")
    rlf_output = process_rlf(result_list)
    ensure_parent_dir(output_rlf_path)
    with open(output_rlf_path, 'w', encoding='utf-8') as f:
        f.write(rlf_output)
    print(f"Wrote RLF LRC to {output_rlf_path}")
    ass_output = norm2ass.process_norm2assV2(result_list, ass_pretime, ass_posttime)
    ass_head = '''[Script Info]
ScriptType: v4.00+
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Source Han Serif,71,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.99999,1.99999,2,11,11,101,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    ensure_parent_dir(output_ass_path)
    with open(output_ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_head+ass_output)
    print(f"Wrote ASS to {output_ass_path}")
    # hrhlrc_output = ''
    # for i in ass_output.splitlines():
    #     hrhlrc_output += ass2lrc.ass2lrc(i, 0)+'\n'
    # with open(os.path.join(real_io_path, 'o_hrh.lrc'), 'w', encoding='utf-8') as f:
    #     f.write(hrhlrc_output)
    print('Success!')

if __name__=='__main__':
    main()
