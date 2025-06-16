import re

def int2asstime(cs: int) -> str:
    '厘秒整数转换为.ass时轴信息'
    hours = cs // 360000
    cs %= 360000
    minutes = cs // 6000
    cs %= 6000
    seconds = cs // 100
    cs %= 100
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

def parse_time_to_hundredths(time_str):
    match = re.match(r'\[(\d{2}):(\d{2}):(\d{2})\]', time_str)
    minutes, seconds, hundredths = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return minutes * 6000 + seconds * 100 + hundredths

def process_norm2assV1(struc, pretime = 20, posttime = 20):
    '模型输出的实际时值，不再维护'
    result = ''
    for i in range(len(struc)):
        if not result or result[-1]=='\n':
            asstxt = ''
            nowtime = starttime = parse_time_to_hundredths([itemd for itemd in struc[i:] if itemd['type'] != 0][0]['start']) - pretime
        item = struc[i]
        if item['type'] == 0 and item['orig'] == '\n':
            try:
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                pass
            finally:
                endtime = nowtime + posttime
                asstxt = 'Dialogue: 0,'+int2asstime(starttime)+','+int2asstime(endtime)+r',Default,,0,0,0,karaoke,'+asstxt+r'{\k'+str(posttime)+'}'
                result += asstxt+'\n'
        elif 'start' not in item:
            asstxt += item['orig']
        else:
            item_kbefore = parse_time_to_hundredths(item['start']) - nowtime
            if item_kbefore!=0:
                asstxt += r'{\k'+str(item_kbefore)+'}'
            item_kdur = parse_time_to_hundredths(item['end']) - parse_time_to_hundredths(item['start'])
            asstxt += r'{\k'+str(item_kdur)+'}'
            if item['type'] == 2:
                asstxt += ('#|' if item['orig']=='' else item['orig'] + '|<') + item['ruby']
            else:
                asstxt += item['orig']
            nowtime = parse_time_to_hundredths(item['end'])
    return result

def process_norm2assV2(struc, pretime = 20, posttime = 20):
    '仿NicokaraMaker.lrc时值'
    result = ''
    starttime = nowtime = None
    asstxt = r'{\k'+str(pretime)+'}'
    i = 0
    while i < len(struc):
        item = struc[i]
        if not starttime:
            try:
                starttime = parse_time_to_hundredths(item['start']) - pretime
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                asstxt += item['orig']
                i += 1
                continue
        if item['type'] == 0 and item['orig'] == '\n':
            try:
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                pass
            finally:
                endtime = nowtime + posttime
                result += 'Dialogue: 0,'+int2asstime(starttime)+','+int2asstime(endtime)+r',Default,,0,0,0,karaoke,'+asstxt+r'{\k'+str(posttime)+'}'+'\n'
                starttime = nowtime = None
                asstxt = r'{\k'+str(pretime)+'}'
        elif item['type'] == 0 and 'start' not in item:
            try:
                item_kdur = parse_time_to_hundredths(struc[i+1]['start']) - nowtime
                if item_kdur!=0: asstxt += r'{\k'+str(item_kdur)+'}'
                nowtime = parse_time_to_hundredths(struc[i+1]['start'])
            except:
                pass
            finally:
                if item['orig'] in (' ','　') and asstxt[-1] not in ('}',' ','　'):
                    asstxt += r'{\k0}'+item['orig']
                else:
                    asstxt += item['orig']
        else:
            if 'start' in struc[i+1]:
                item_kdur = parse_time_to_hundredths(struc[i+1]['start']) - parse_time_to_hundredths(item['start'])
                nowtime = parse_time_to_hundredths(struc[i+1]['start'])
            else:
                item_kdur = parse_time_to_hundredths(item['end']) - parse_time_to_hundredths(item['start'])
                nowtime = parse_time_to_hundredths(item['end'])
            asstxt += r'{\k'+str(item_kdur)+'}'
            if item['type'] == 2:
                asstxt += ('#|' if item['orig']=='' else item['orig'] + '|<') + item['ruby']
            else:
                asstxt += item['orig']
        i += 1
    return result


if __name__=='__main__':
    input_struc = [{'orig': '歌',
  'type': 2,
  'ruby': 'う',
  'pron': 'u',
  'start': '[00:01:38]',
  'end': '[00:01:40]'},
 {'orig': '',
  'type': 2,
  'ruby': 'た',
  'pron': 'ta',
  'start': '[00:01:54]',
  'end': '[00:01:64]'},
 {'orig': 'え',
  'type': 3,
  'pron': 'e',
  'start': '[00:03:12]',
  'end': '[00:03:14]'},
 {'orig': '\u3000', 'type': 0},
 {'orig': '踊',
  'type': 2,
  'ruby': 'お',
  'pron': 'o',
  'start': '[00:03:35]',
  'end': '[00:03:37]'},
 {'orig': '',
  'type': 2,
  'ruby': 'ど',
  'pron': 'do',
  'start': '[00:04:13]',
  'end': '[00:04:24]'},
 {'orig': 'れ',
  'type': 3,
  'pron': 're',
  'start': '[00:10:24]',
  'end': '[00:10:54]'},
 {'orig': '\n', 'type': 0},
 {'orig': '祈',
  'type': 2,
  'ruby': 'い',
  'pron': 'i',
  'start': '[00:11:17]',
  'end': '[00:11:20]'},
 {'orig': '',
  'type': 2,
  'ruby': 'の',
  'pron': 'no',
  'start': '[00:11:33]',
  'end': '[00:11:40]'},
 {'orig': 'れ',
  'type': 3,
  'pron': 're',
  'start': '[00:11:75]',
  'end': '[00:11:85]'},
 {'orig': '\u3000', 'type': 0},
 {'orig': '届',
  'type': 2,
  'ruby': 'と',
  'pron': 'to',
  'start': '[00:11:96]',
  'end': '[00:12:06]'},
 {'orig': '',
  'type': 2,
  'ruby': 'ど',
  'pron': 'do',
  'start': '[00:12:17]',
  'end': '[00:12:72]'},
 {'orig': 'け',
  'type': 3,
  'pron': 'ke',
  'start': '[00:12:83]',
  'end': '[00:12:93]'},
 {'orig': '\n', 'type': 0}]
    print(process_norm2assV2(input_struc))