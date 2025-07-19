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
    parser.add_argument('--lang', default='jaen', help='歌词语言')
    parser.add_argument('-f', '--txt_format', default='hrh', help='歌词文本格式')
    parser.add_argument('-cl', '--characters_per_line', type=int, default=0, help='输出文件每行最大字数')
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

    print('Loading files...')
    result_list = []
    with open(input_text_path, 'r', encoding='utf-8') as file:
        if txt_format=='uta':
            utat_str = ''
            for line in file:
                utat_str += line
            file = lrcfmt.utat_process(utat_str)
        for line in file:
            if txt_format=='moe':
                line = lrcfmt.moeg_process_line(line)
            if line.strip():
                result_list.extend(hn.process_haruhi_line(line, lrc_language, sokuon_split, hatsuon_split))
    if result_list[-1]['orig']!='\n':
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

    for item in alignment_tokens:
        if hn.is_english(item):
            continue
        else:
            print(f"alignment_tokens可能包含错误数据{item}")

    end_time = time.time()
    print("Lyrics text analysis executed in", round(end_time - start_time, 3), "seconds")

    audio_file, sr = librosa.load(input_audio_path, sr=None) 
    non_silent_ranges = non_silent_recog(audio_file, sr, silent_window_s, tail_thres_pct, tail_thres_ratio)

    if audio_speed == 1:
        print('Adding timelines...')
        alignment_results = align.align_audio_with_text(audio_file, alignment_tokens, non_silent_ranges, sr)
    else:
        print('Changing the audio speed...')
        start_time = time.time()
        y_processed = librosa.effects.time_stretch(audio_file, rate=audio_speed)
        end_time = time.time()
        print("Audio speed changing executed in", round(end_time - start_time, 3), "seconds")
        print('Adding timelines...')
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
            if result_list[i]['type'] != 0 and result_list[i+1]['type'] == 0:
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

    main_output = process_main(result_list, ruby_tag_offset, bpm, beats_per_bar)
    ruby_output = process_ruby(result_list)
    content = f"{main_output}\n{ruby_output}"
    with open(os.path.join(real_io_path, 'o_ruby.lrc'), 'w', encoding='utf-8') as f:
        f.write(content)
    rlf_output = process_rlf(result_list)
    with open(os.path.join(real_io_path, 'o_rlf.lrc'), 'w', encoding='utf-8') as f:
        f.write(rlf_output)
    ass_output = norm2ass.process_norm2assV2(result_list)
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