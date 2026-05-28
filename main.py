import argparse
import bisect
import librosa
import numpy as np
import os
import re
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

def non_silent_recog(audio_file, sr = None, frame_second = 1, threspct = 10, thresrto = .1):
    '识别非静音片段'
    frame_length = int(sr * frame_second)
    hop_length = frame_length // 2  # 50% 重叠
    energy = librosa.feature.rms(y=audio_file, frame_length=frame_length, hop_length=hop_length)[0]
    threshold = np.percentile(energy, 100-threspct) * thresrto
    non_silent_frames = energy > threshold
    times = librosa.frames_to_time(np.arange(len(energy)), sr=sr, hop_length=hop_length) # 转换为时间点
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

def apply_tail_timing_correction(result_list, audio_file, sr, tail_thres_pct, tail_thres_ratio):
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

def run_partial_realign(args, real_io_path, input_audio_path, input_text_path, lrc_language,
                        sokuon_split, hatsuon_split, tail_correct, silent_window_s,
                        tail_thres_pct, tail_thres_ratio, audio_speed, chunk_seconds):
    ass_input_path = resolve_io_path(real_io_path, args.realign_ass)
    ass_output_path = ass_input_path if args.realign_inplace else resolve_io_path(real_io_path, args.realign_output)
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
    source_lines = [ass_karaoke_text_to_input_line(event['text']) for event in selected_events]

    print(
        f"Realigning {len(selected_events)} ASS karaoke line(s) "
        f"against {os.path.basename(input_audio_path)} "
        f"{format_seconds_for_log(audio_start)}-{format_seconds_for_log(audio_end)}."
    )
    text_sync_plan = prepare_synced_text(
        input_text_path,
        real_io_path,
        args.realign_text_output,
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
    audio_file, sr = librosa.load(input_audio_path, sr=None)
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
        apply_tail_timing_correction(result_list, audio_file, sr, tail_thres_pct, tail_thres_ratio)

    ass_output = norm2ass.process_norm2assV2(result_list, 20, 20)
    replacement_dialogues = parse_generated_dialogues(ass_output)
    if len(replacement_dialogues) != len(selected_events):
        raise ValueError(
            f"Expected {len(selected_events)} regenerated ASS lines, got {len(replacement_dialogues)}. "
            "The selected ASS range may not map one-to-one to lyric lines."
        )

    for original_event, replacement_event in zip(selected_events, replacement_dialogues):
        fields = original_event['fields'][:]
        fields[1] = replacement_event['fields'][1]
        fields[2] = replacement_event['fields'][2]
        fields[9] = replacement_event['fields'][9]
        ass_lines[original_event['line_index']] = format_ass_event_line(
            original_event['event_type'],
            fields,
            original_event['newline'] or '\n',
        )

    with open(ass_output_path, 'w', encoding='utf-8', newline='') as file:
        file.writelines(ass_lines)
    print(f"Wrote realigned ASS to {ass_output_path}")
    write_synced_text(text_sync_plan)
    print('Success!')

def main():
    start_time = time.time()
    script_dir = os.path.dirname(os.path.realpath(__file__))
    parser = argparse.ArgumentParser(description='可选参数')
    parser.add_argument('-x', '--sokuon_split', type=int, default=0, help='是否将促音与前一字符拆开')
    parser.add_argument('-n', '--hatsuon_split', type=int, default=1, help='是否将拨音与前一字符拆开')
    parser.add_argument('-v', '--audio_speedx', type=float, default=1, help='推理时使用的音频倍速')
    parser.add_argument('-p', '--path_io', default='', help='输入输出文件目录。基于主文件所在目录，支持绝对路径或相对路径')
    parser.add_argument('-ia', '--input_audio', default=None, help='输入音频文件名')
    parser.add_argument('-it', '--input_text', default='i.txt', help='输入歌词文件名')
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
    parser.add_argument('--pronunciation_file', default='pronunciations.txt', help='自定义英文发音表文件名。默认读取输入输出目录下的 pronunciations.txt')
    parser.add_argument('--realign', '--realign_range', dest='realign', default=None, help='局部重对轴的 ASS 行范围，例如 227-245')
    parser.add_argument('--realign_time', '--realign_audio_range', dest='realign_time', default=None, help='局部重对轴使用的音频时间范围，例如 17:49-18:20；省略时自动用选区上一句结束到下一句开始')
    parser.add_argument('--realign_ass', default='o.ass', help='局部重对轴读取的 ASS 文件名')
    parser.add_argument('--realign_output', default='o_realign.ass', help='局部重对轴输出的 ASS 文件名')
    parser.add_argument('--realign_mode', choices=('karaoke', 'event'), default='karaoke', help='ASS 范围编号方式：karaoke=歌词 Dialogue 行，event=Aegisub 事件行')
    parser.add_argument('--realign_inplace', action='store_true', help='直接覆盖 --realign_ass 指定的 ASS 文件')
    parser.add_argument('--realign_update_text', action='store_true', help='用选中 ASS 歌词同步覆盖输入 i.txt 对应行')
    parser.add_argument('--realign_text_output', default='i_realign.txt', help='未开启 --realign_update_text 时写出的同步歌词文件名')
    args = parser.parse_args()

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
    
    real_io_path = os.path.normpath(user_path) if os.path.isabs(user_path) else os.path.normpath(os.path.join(script_dir, user_path))
    if not os.path.exists(real_io_path):
        os.makedirs(real_io_path)
    input_text_path = os.path.normpath(os.path.join(real_io_path, user_text_path))
    if user_audio_path:
        input_audio_path = os.path.normpath(os.path.join(real_io_path, user_audio_path))
    elif os.path.exists(os.path.normpath(os.path.join(real_io_path, 'i.wav'))):
        input_audio_path = os.path.normpath(os.path.join(real_io_path, 'i.wav'))
    else:
        input_audio_path = os.path.normpath(os.path.join(real_io_path, 'i.mp3'))
    pronunciation_path = os.path.normpath(os.path.join(real_io_path, pronunciation_file)) if pronunciation_file else None

    print('Loading files...')
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

    audio_file, sr = librosa.load(input_audio_path, sr=None) 
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
    with open(os.path.join(real_io_path, 'o_ruby.lrc'), 'w', encoding='utf-8') as f:
        f.write(content)
    rlf_output = process_rlf(result_list)
    with open(os.path.join(real_io_path, 'o_rlf.lrc'), 'w', encoding='utf-8') as f:
        f.write(rlf_output)
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
    with open(os.path.join(real_io_path, 'o.ass'), 'w', encoding='utf-8') as f:
        f.write(ass_head+ass_output)
    # hrhlrc_output = ''
    # for i in ass_output.splitlines():
    #     hrhlrc_output += ass2lrc.ass2lrc(i, 0)+'\n'
    # with open(os.path.join(real_io_path, 'o_hrh.lrc'), 'w', encoding='utf-8') as f:
    #     f.write(hrhlrc_output)
    print('Success!')

if __name__=='__main__':
    main()
