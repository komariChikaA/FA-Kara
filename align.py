import math
import time

import torch
import torchaudio


def _prepare_waveform(audio_file_path, sr=None):
    if isinstance(audio_file_path, str):
        waveform, sample_rate = torchaudio.load(audio_file_path)
    else:
        waveform = torch.tensor(audio_file_path).float()
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        sample_rate = sr
    return waveform, sample_rate


def _format_time(time_sec):
    minutes, remainder = divmod(max(time_sec, 0), 60)
    seconds, centiseconds = divmod(remainder, 1)
    return f"[{int(minutes):02d}:{int(seconds):02d}:{math.floor(centiseconds * 100):02d}]"


def _normalise_ranges(non_silent_ranges, original_duration):
    ranges = []
    for start_sec, end_sec in non_silent_ranges or []:
        start_sec = max(float(start_sec), 0)
        end_sec = min(float(end_sec), original_duration)
        if end_sec > start_sec:
            ranges.append((start_sec, end_sec))
    if not ranges and original_duration > 0:
        ranges.append((0, original_duration))
    return ranges


def _concat_ranges(waveform, sample_rate, ranges, speed):
    total_samples = waveform.shape[1]
    segments = []
    for start_sec, end_sec in ranges:
        start_sample = int(start_sec * sample_rate / speed)
        end_sample = min(int(end_sec * sample_rate / speed), total_samples)
        if end_sample > start_sample:
            segments.append(waveform[:, start_sample:end_sample])
    if not segments:
        return waveform[:, :0]
    return torch.cat(segments, dim=1)


def _map_to_original_time(adjusted_time, ranges):
    cumulative_duration = 0.0
    for start_sec, end_sec in ranges:
        segment_duration = end_sec - start_sec
        if adjusted_time < cumulative_duration + segment_duration:
            return start_sec + (adjusted_time - cumulative_duration)
        cumulative_duration += segment_duration
    return ranges[-1][1] if ranges else adjusted_time


def _error_results(valid_tokens):
    return [{'token': token, 'start': '[error]', 'end': '[error]'} for token in valid_tokens]


def _align_tokens_in_ranges(waveform, sample_rate, valid_tokens, ranges, speed, bundle, model, tokenizer, aligner, device):
    if not valid_tokens:
        return []

    ranged_waveform = _concat_ranges(waveform, sample_rate, ranges, speed)
    if ranged_waveform.shape[1] == 0:
        return _error_results(valid_tokens)

    ranged_waveform = ranged_waveform.mean(0, keepdim=True)
    if sample_rate != bundle.sample_rate:
        ranged_waveform = torchaudio.functional.resample(ranged_waveform, sample_rate, bundle.sample_rate)

    try:
        with torch.inference_mode():
            emission, _ = model(ranged_waveform.to(device))
            tokens = tokenizer(valid_tokens)
            token_spans = aligner(emission[0], tokens)
    except Exception as exc:
        print(f"Error during alignment chunk: {exc}")
        return _error_results(valid_tokens)
    finally:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    frame_duration = 1.0 / bundle.sample_rate * 320 * speed
    results = []
    for i, spans in enumerate(token_spans):
        if not spans:
            results.append({
                'token': valid_tokens[i],
                'start': '[error]',
                'end': '[error]'
            })
            continue

        adjusted_start = spans[0].start * frame_duration
        adjusted_end = spans[-1].end * frame_duration
        original_start = _map_to_original_time(adjusted_start, ranges)
        original_end = _map_to_original_time(adjusted_end, ranges)

        results.append({
            'token': valid_tokens[i],
            'start': _format_time(original_start),
            'end': _format_time(original_end),
            'original_start': original_start,
            'original_end': original_end
        })
    return results


def _split_long_ranges(ranges, chunk_seconds):
    if chunk_seconds <= 0:
        return ranges
    split_ranges = []
    for start_sec, end_sec in ranges:
        cursor = start_sec
        while end_sec - cursor > chunk_seconds:
            split_ranges.append((cursor, cursor + chunk_seconds))
            cursor += chunk_seconds
        if end_sec > cursor:
            split_ranges.append((cursor, end_sec))
    return split_ranges


def _range_duration(ranges):
    return sum(end_sec - start_sec for start_sec, end_sec in ranges)


def _partition_ranges_by_count(ranges, count):
    if count <= 1 or len(ranges) <= 1:
        return [ranges]

    total_duration = _range_duration(ranges)
    chunks = []
    current = []
    accumulated = 0.0
    next_target = total_duration / count

    for i, range_item in enumerate(ranges):
        current.append(range_item)
        accumulated += range_item[1] - range_item[0]

        remaining_ranges = len(ranges) - i - 1
        remaining_chunks = count - len(chunks) - 1
        if (len(chunks) < count - 1 and accumulated >= next_target and
                remaining_ranges >= remaining_chunks):
            chunks.append(current)
            current = []
            next_target = total_duration * (len(chunks) + 1) / count

    if current:
        chunks.append(current)
    return chunks


def _partition_tokens_evenly(total_tokens, count):
    chunks = []
    previous = 0
    for i in range(1, count):
        boundary = round(total_tokens * i / count)
        boundary = max(previous + 1, min(boundary, total_tokens - (count - i)))
        chunks.append((previous, boundary))
        previous = boundary
    chunks.append((previous, total_tokens))
    return chunks


def _partition_tokens_by_sentence_spans(total_tokens, sentence_token_spans, count):
    if count <= 1:
        return [(0, total_tokens)]
    if not sentence_token_spans:
        return _partition_tokens_evenly(total_tokens, count)

    boundaries = sorted({end for _, end in sentence_token_spans if 0 < end < total_tokens})
    if len(boundaries) + 1 < count:
        return _partition_tokens_evenly(total_tokens, count)

    chunks = []
    previous = 0
    for i in range(1, count):
        target = total_tokens * i / count
        remaining_chunks = count - i - 1
        candidates = [
            boundary for boundary in boundaries
            if boundary > previous and sum(1 for other in boundaries if other > boundary) >= remaining_chunks
        ]
        if not candidates:
            return _partition_tokens_evenly(total_tokens, count)
        boundary = min(candidates, key=lambda value: abs(value - target))
        chunks.append((previous, boundary))
        previous = boundary

    chunks.append((previous, total_tokens))
    return chunks


def _crop_ranges(ranges, start_time, end_time):
    cropped = []
    for start_sec, end_sec in ranges:
        start_crop = max(start_sec, start_time)
        end_crop = min(end_sec, end_time)
        if end_crop > start_crop:
            cropped.append((start_crop, end_crop))
    return cropped


def _build_manual_chunks(total_tokens, ranges, original_duration, manual_chunk_boundaries):
    clean_boundaries = []
    last_token_index = 0
    last_time = 0.0
    for boundary in sorted(manual_chunk_boundaries or [], key=lambda item: item['token_index']):
        token_index = int(boundary['token_index'])
        chunk_time = float(boundary['time'])
        if not (0 < token_index < total_tokens):
            print(f"Ignored chunk marker on line {boundary.get('line_no')} because it has no lyrics on one side.")
            continue
        if token_index <= last_token_index or chunk_time <= last_time:
            print(f"Ignored chunk marker on line {boundary.get('line_no')} because chunk markers must be increasing.")
            continue
        if chunk_time >= original_duration:
            print(f"Ignored chunk marker on line {boundary.get('line_no')} because it is outside the audio duration.")
            continue
        clean_boundaries.append((token_index, chunk_time))
        last_token_index = token_index
        last_time = chunk_time

    if not clean_boundaries:
        return []

    token_bounds = [0] + [token_index for token_index, _ in clean_boundaries] + [total_tokens]
    time_bounds = [0.0] + [chunk_time for _, chunk_time in clean_boundaries] + [original_duration]

    chunks = []
    for i in range(len(token_bounds) - 1):
        chunk_ranges = _crop_ranges(ranges, time_bounds[i], time_bounds[i + 1])
        if not chunk_ranges:
            chunk_ranges = [(time_bounds[i], time_bounds[i + 1])]
        chunks.append({
            'token_start': token_bounds[i],
            'token_end': token_bounds[i + 1],
            'ranges': chunk_ranges,
        })
    return chunks


def _build_auto_chunks(total_tokens, ranges, sentence_token_spans, chunk_seconds):
    if chunk_seconds <= 0:
        return []

    split_ranges = _split_long_ranges(ranges, chunk_seconds)
    total_duration = _range_duration(split_ranges)
    if total_duration <= chunk_seconds:
        return []

    max_token_chunks = len(sentence_token_spans) if sentence_token_spans else total_tokens
    desired_count = math.ceil(total_duration / chunk_seconds)
    desired_count = max(1, min(desired_count, max_token_chunks, total_tokens, len(split_ranges)))
    if desired_count <= 1:
        return []

    audio_chunks = _partition_ranges_by_count(split_ranges, desired_count)
    token_chunks = _partition_tokens_by_sentence_spans(total_tokens, sentence_token_spans, len(audio_chunks))
    count = min(len(audio_chunks), len(token_chunks))
    if count <= 1:
        return []
    if len(audio_chunks) != count:
        audio_chunks = _partition_ranges_by_count(split_ranges, count)
    if len(token_chunks) != count:
        token_chunks = _partition_tokens_by_sentence_spans(total_tokens, sentence_token_spans, count)

    return [
        {
            'token_start': token_chunks[i][0],
            'token_end': token_chunks[i][1],
            'ranges': audio_chunks[i],
        }
        for i in range(count)
    ]


def _load_alignment_model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model().to(device)
    tokenizer = bundle.get_tokenizer()
    aligner = bundle.get_aligner()
    return bundle, model, tokenizer, aligner, device


def align_audio_with_text(audio_file_path, text_tokens, non_silent_ranges=None, sr=None, speed=1):
    start_time = time.time()
    waveform, sample_rate = _prepare_waveform(audio_file_path, sr)
    original_duration = waveform.shape[1] * speed / sample_rate
    ranges = _normalise_ranges(non_silent_ranges, original_duration)
    valid_tokens = [token for token in text_tokens if token]

    try:
        bundle, model, tokenizer, aligner, device = _load_alignment_model()
        results = _align_tokens_in_ranges(waveform, sample_rate, valid_tokens, ranges, speed, bundle, model, tokenizer, aligner, device)
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        end_time = time.time()
        print("Alignment inference executed in", round(end_time - start_time, 3), "seconds")
        return results
    except Exception as e:
        print(f"Error during alignment: {e}")
        return []


def align_audio_with_text_chunked(
        audio_file_path,
        text_tokens,
        non_silent_ranges=None,
        sr=None,
        speed=1,
        chunk_seconds=0,
        sentence_token_spans=None,
        manual_chunk_boundaries=None):
    start_time = time.time()
    waveform, sample_rate = _prepare_waveform(audio_file_path, sr)
    original_duration = waveform.shape[1] * speed / sample_rate
    ranges = _normalise_ranges(non_silent_ranges, original_duration)
    valid_tokens = [token for token in text_tokens if token]

    if not valid_tokens:
        return []

    chunks = _build_manual_chunks(len(valid_tokens), ranges, original_duration, manual_chunk_boundaries)
    chunk_mode = 'manual'
    if not chunks:
        chunks = _build_auto_chunks(len(valid_tokens), ranges, sentence_token_spans or [], chunk_seconds)
        chunk_mode = 'auto'
    if not chunks:
        return align_audio_with_text(audio_file_path, text_tokens, non_silent_ranges, sr, speed)

    print(f"Chunked alignment enabled ({chunk_mode}, {len(chunks)} chunks).")

    try:
        bundle, model, tokenizer, aligner, device = _load_alignment_model()
        results = _error_results(valid_tokens)
        for i, chunk in enumerate(chunks, 1):
            token_start = chunk['token_start']
            token_end = chunk['token_end']
            chunk_tokens = valid_tokens[token_start:token_end]
            audio_start = chunk['ranges'][0][0]
            audio_end = chunk['ranges'][-1][1]
            print(
                f"Aligning chunk {i}/{len(chunks)}: "
                f"tokens {token_start + 1}-{token_end}, audio {_format_time(audio_start)}-{_format_time(audio_end)}"
            )
            chunk_results = _align_tokens_in_ranges(
                waveform,
                sample_rate,
                chunk_tokens,
                chunk['ranges'],
                speed,
                bundle,
                model,
                tokenizer,
                aligner,
                device,
            )
            for offset, result in enumerate(chunk_results):
                results[token_start + offset] = result

        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        end_time = time.time()
        print("Chunked alignment inference executed in", round(end_time - start_time, 3), "seconds")
        return results
    except Exception as e:
        print(f"Error during chunked alignment: {e}")
        return []
