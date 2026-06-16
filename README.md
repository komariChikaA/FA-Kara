# FA-Kara

FA-Kara 是一个基于“注音歌词文本 + 人声音频”的自动卡拉 OK 打轴工具。它会把歌词拆成可对齐的发音 token，使用 torchaudio 的 MMS_FA 模型对人声音频做 forced alignment，然后输出可继续编辑的 ASS 和 LRC 文件。

本项目主要参考了 [yohane](https://github.com/Japan7/yohane) 和 [Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara/)。建议优先用于日语歌曲，但底层对齐模型不限语种；项目中也包含英文、中文和数字读音处理逻辑。

## 与原项目的差异

这里的“原项目”主要指上面两个参考项目以及本仓库早期的基础实现。当前版本围绕长音频、混合语种歌词和 Aegisub 后期修轴做了更多工作流补强：

- 使用 `torchaudio` 的 `MMS_FA` 做 forced alignment，并保留可继续编辑的 ASS/LRC 输出流程。
- 增加英文、中文、数字和自定义英文发音表 `pronunciations.txt` 的处理，方便处理串烧、翻唱合集和混合语种歌词。
- 增加 `@comment[...]` 注释段，只输出到 ASS，不参与音频对齐，适合标题、间奏提示或台词说明。
- 增加长音频分块能力：支持 `--chunk_seconds` 自动分块，也支持在歌词文本中用 `@chunk[...]` 手动锚定音频位置。
- 增加局部重对轴功能：可以从现有 ASS 文件选中 Aegisub 行号或歌词 Dialogue 行号，只重跑某一句或某一段；选区歌词会优先使用 ASS 中已经修改过的文字。
- 局部重对轴支持省略 `--realign_time`，程序会自动用选区上一句结束时间和下一句开始时间推导搜索范围；也可以手动指定 `18:35-19:20` 这类时间段。
- 默认生成 `<工作文件夹名>.ass`、`<工作文件夹名>_rlf.lrc`、`<工作文件夹名>_ruby.lrc`，也可以通过参数指定输入音频、输入歌词和输出文件名，兼顾 Aegisub、RhythmicaLyrics 和 NicoKaraMaker3 的后续编辑流程。

## 工作流程

1. `main.py` 读取命令行参数、输入音频和输入歌词；默认使用工作文件夹内唯一的音频和歌词文本，并规范成工作文件夹同名文件。
2. `haruraw2norm.py` 解析歌词文本，将 `{漢字|かな}`、`[字|romaji]`、假名、英文、数字等内容转换为内部结构。
3. 只有带 `pron` 的歌词元素会进入音频识别和对齐；注释段、分块锚点等控制信息不会参与识别。
4. `align.py` 使用 MMS_FA 将发音 token 对齐到人声音频。
5. `norm2ass.py` 输出 ASS 文件，默认是 `<工作文件夹名>.ass`，用于 Aegisub 等字幕工具继续编辑。
6. `norm2lrc.py` 输出 LRC 文件，默认是 `<工作文件夹名>_rlf.lrc` 和 `<工作文件夹名>_ruby.lrc`，用于 RhythmicaLyrics 或 NicoKaraMaker3。

## 输入文件

运行前准备一个工作文件夹；推荐一个歌曲一个文件夹。工作文件夹内如果只有一个 `.wav` 和一个歌词 `.txt`，程序会自动把它们当作输入，并在运行开始时重命名为和工作文件夹同名：

| 文件 | 说明 |
| --- | --- |
| `<工作文件夹名>.wav` | 从歌曲中分离出来的人声音频。可以用 UVR、MSST 等工具生成。 |
| `<工作文件夹名>.txt` | 带注音的歌词文本。需要尽量和人声音频内容一致。 |

这些本地工作文件已经被 `.gitignore` 忽略，不建议提交到仓库。

`-p/--work_dir` 指定工作文件夹。默认情况下，音频、歌词、输出 ASS/LRC、`pronunciations.txt` 和局部重对轴文件都会从同一个工作文件夹读取或写入。

例如：

``` shell
python main.py -p songs/song_a
```

如果 `songs/song_a` 里一开始只有 `vocal.wav` 和 `lyrics.txt`，运行开始时会自动整理为：

``` txt
songs/song_a/song_a.txt
songs/song_a/song_a.wav
songs/song_a/song_a.ass
songs/song_a/song_a_realign.ass
songs/song_a/song_a_rlf.lrc
songs/song_a/song_a_ruby.lrc
songs/song_a/pronunciations.txt
```

如果目标文件名已经存在，程序会停止并提示，不会覆盖已有文件。

也可以直接指定其他文件名：

``` shell
python main.py -it song.txt -ia song.wav -o song.ass --output_rlf song_rlf.lrc --output_ruby song_ruby.lrc
```

如果没有指定 `-ia`，程序会先在本次任务的工作文件夹内尝试寻找和歌词同名的音频，再回退到 `i.wav`、`i.mp3`；如果仍找不到，但工作文件夹内只有一个音频文件，会自动使用并重命名为 `<工作文件夹名>.<原扩展名>`。

## 快速开始

安装依赖后，在项目目录运行：

``` shell
python main.py
```

运行成功后会生成：

| 文件 | 用途 |
| --- | --- |
| `<工作文件夹名>.ass` | ASS 字幕文件，可在 Aegisub 中继续编辑。 |
| `<工作文件夹名>_rlf.lrc` | RhythmicaLyrics 可编辑的 LRC 文件。 |
| `<工作文件夹名>_ruby.lrc` | NicoKaraMaker3 可用的 ruby LRC 文件，默认提前 150ms。 |

输出文件名可用 `-o/--output_ass`、`--output_rlf`、`--output_ruby` 分别指定。

## 歌词文本格式

普通歌词使用春日向けテキスト风格：

``` txt
ずっと{知|し}り{得|え}ないことは{良|い}いこと
```

常用写法：

``` txt
{漢字|かな}
[字|romaji]
```

- `{漢字|かな}` 表示振假名注音，会被拆成可对齐的日语发音。
- `[字|romaji]` 表示手动辅助读音，适合处理自动推断不准的字词。
- 普通假名、英文和数字会按项目现有逻辑转换为发音 token。
- 空行和普通换行会作为歌词段落边界处理。

## 英文单词发音

英文会优先查 CMU 发音词典。专名、生造词、缩写或歌词里的特殊读法可能查不到，程序会在终端输出：

``` txt
Unknown English words found. Add them to pronunciations.txt if the guessed pronunciation is wrong:
  Komariver=<romaji>
```

这时可以在输入输出目录创建 `pronunciations.txt`，每行写一个自定义发音：

``` txt
Gucci=guchi
YOASOBI=yoasobi
can't=kant
```

等号右边使用本项目的 romaji/token 写法；`#` 后面可以写注释。只需要写自动识别不准的词。单句里临时处理也可以继续使用 `[显示文字|romaji]`，例如 `[Komariver|komariva]`。

`pronunciations.txt` 默认从本次任务工作文件夹查找；如果多首歌想共用同一份发音表，可以传入相对路径或绝对路径，例如：

``` shell
python main.py -p songs -it song_a/lyrics.txt -ia vocal.wav -o song_a.ass --pronunciation_file ../pronunciations.txt
```

## 注释段

注释段用于在歌曲中显示几秒说明文字，例如标题、间奏提示、剧情提示或台词提示。注释段不会参与音频识别，也不会写入 RLF/ruby LRC，只会输出到 ASS 文件。

写法：

``` txt
@comment[3.0] 显示文字
@注释[2.5] 也可以使用中文标记
```

方括号内是显示时长，单位为秒。以下写法都可以：

``` txt
@comment[3] 显示 3 秒
@comment[3.0] 显示 3 秒
@comment[3s] 显示 3 秒
@comment[3sec] 显示 3 秒
@comment[3秒] 显示 3 秒
```

排时规则：

- 注释段会自动放在前一句歌词段结束之后。
- 注释段会避开下一句歌词段，不会主动覆盖下一句歌词。
- 如果到下一句歌词前的空隙不足，程序会自动缩短注释段。
- 如果完全没有可用空隙，程序会跳过该注释段并在终端打印提示。

## 长音频分块

长音频不建议整段一次性送入 MMS_FA。对于 Live、组曲或一小时以上音频，可以开启分块推理来降低内存和显存压力。

自动分块：

``` shell
python main.py --chunk_seconds 300
```

`--chunk_seconds` 表示每个推理块的目标非静音时长，单位为秒。`300` 大约是每 5 分钟一块。自动分块会按检测到的非静音片段切音频，并尽量在歌词行边界切分 token。

如果歌词密度不均匀、长间奏很多，建议在歌词文本中加入手动分块锚点：

``` txt
第一段歌词
...
@chunk[12:34]
第二段从这里之后开始
...
@chunk[25:10]
第三段从这里之后开始
```

`@chunk[12:34]` 表示该行之后的歌词大约从音频 `12:34` 附近开始。它不会参与音频识别，也不会输出到 ASS/LRC，只用于把歌词和音频切成更稳的对齐块。

支持的时间格式：

- `75` 或 `75秒`
- `12:34`
- `01:02:03`

## 局部重对轴

如果已经生成并手动检查过 ASS 文件，但中间某一段轴不准，可以只重跑指定歌词行和指定音频时间段，不必整首重新对齐。

可以省略 `--realign_time`，程序会自动使用选区上一句歌词的结束时间和下一句歌词的开始时间作为搜索范围：

``` shell
python main.py --realign 227
```

如果这个 `#227` 是 Aegisub 左侧事件列表显示的编号，使用：

``` shell
python main.py --realign 227 --realign_mode event
```

多句也可以自动取范围。例如按 Aegisub 左侧事件编号重对 `#244` 到 `#246`，会自动使用 `#243` 的结束时间到 `#247` 的开始时间：

``` shell
python main.py --realign 244-246 --realign_mode event
```

也可以手动指定音频时间范围。例如重对 ASS 文件中第 227 到 245 句歌词，并指定这段人声在音频 `18:35` 到 `19:20`：

``` shell
python main.py --realign 227-245 --realign_time 18:35-19:20
```

局部重对轴也支持用 `-v` / `--audio_speedx` 降速推理。数值越小越慢，通常 `0.5` 会比默认更细，但耗时更长：

``` shell
python main.py --realign 227-245 --realign_time 18:35-19:20 -v 0.5
```

默认会读取 `--output_ass` 指定的 ASS 文件（默认 `<工作文件夹名>.ass`），输出到 `<工作文件夹名>_realign.ass`，不会覆盖原文件。`--realign` 默认按 karaoke `Dialogue` 歌词行编号；如果你看到的编号是 Aegisub 事件列表里的行号，改用：

``` shell
python main.py --realign 227-245 --realign_mode event --realign_time 18:35-19:20
```

`--realign_time` 支持的时间格式和分块锚点一致，例如：

- `75-90`
- `18:35-19:20`
- `00:18:35-00:19:20`

局部重对轴会从选中的 ASS 歌词行提取显示文本并重新生成发音 token。也就是说，如果 ASS 中这几句歌词已经被你改过，程序会优先按 ASS 里的文字重新对齐。成功后如果发现 ASS 歌词和输入歌词对应行不同，会额外写出 `<input_text>_realign.txt`，方便把修改同步回歌词文本。

常用覆盖写法：

``` shell
python main.py --realign 227-245 --realign_time 18:35-19:20 --realign_inplace
```

如果确认要同时把 ASS 中修改过的歌词同步覆盖回输入歌词：

``` shell
python main.py --realign 227-245 --realign_time 18:35-19:20 --realign_inplace --realign_update_text
```

## 批量队列

如果 `songs/` 下有多个歌曲工作文件夹，可以让程序按队列逐首打轴。它不会并行处理，也不会一次性把所有歌曲内容读入内存；父进程只逐个扫描子目录，每首歌会启动一个单独子进程，等这一首完成并释放内存后再处理下一首。

``` shell
python main.py --batch_songs
```

默认会扫描项目目录下的 `songs/`。每个子文件夹只要能找到歌词文本和音频，就会按单首工作流处理；例如：

``` txt
songs/song_a/song_a.txt
songs/song_a/song_a.wav
songs/song_b/lyrics.txt
songs/song_b/vocal.wav
```

`song_b` 会在开始处理时自动整理成 `song_b.txt` 和 `song_b.wav`，然后输出 `song_b.ass`、`song_b_rlf.lrc`、`song_b_ruby.lrc`。没有音频/歌词配对的目录会被跳过，例如仓库里的 `songs/song_a/README.md` 样例目录。

指定其他歌曲根目录：

``` shell
python main.py --batch_songs --songs_dir D:\Karaoke\songs
```

批量模式默认某首失败就停止；如果希望失败后继续下一首：

``` shell
python main.py --batch_songs --batch_continue_on_error
```

批量模式会复用音频倍速、分块、语言、尾音等全局参数，但不接受 `-it`、`-ia`、`-o` 和局部重对轴参数；每首歌都使用它自己的工作文件夹默认命名。

## 常用参数

``` shell
python main.py -h
```

常用选项：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-p`, `--path_io`, `--work_dir` | 当前项目目录 | 工作文件夹，支持绝对路径或相对路径。 |
| `-ia`, `--input_audio` | 自动 | 输入音频文件；未指定时自动选择同名音频、`i.wav`/`i.mp3` 或工作文件夹内唯一音频，并在自动模式下规范为 `<工作文件夹名>.<扩展名>`。 |
| `-it`, `--input_text` | 自动 | 输入歌词文本文件；未指定时自动选择 `<工作文件夹名>.txt`、`i.txt` 或工作文件夹内唯一歌词文本，并规范为 `<工作文件夹名>.txt`。 |
| `-o`, `--output_ass` | `<工作文件夹名>.ass` | 输出 ASS 文件。 |
| `--output_rlf` | `<工作文件夹名>_rlf.lrc` | 输出 RhythmicaLyrics LRC 文件。 |
| `--output_ruby` | `<工作文件夹名>_ruby.lrc` | 输出 ruby LRC 文件。 |
| `--pronunciation_file` | `pronunciations.txt` | 自定义英文发音表；默认读取本次任务工作文件夹内的文件，也可传相对路径或绝对路径。 |
| `-v`, `--audio_speedx` | `1` | 推理时使用的音频倍速。 |
| `-tl`, `--tail_limit_window` | `0.8` | 全曲静音检测窗口时长，单位为秒。 |
| `-tp`, `--tail_thres_pct` | `10` | 尾音阈值百分位数。 |
| `-tr`, `--tail_thres_ratio` | `0.1` | 尾音阈值比例。 |
| `--offset` | `-150` | 输出 ruby LRC 的 Offset 标签。 |
| `--bpm` | `60` | 导唱指示灯使用的 BPM。 |
| `--bpb` | `3` | 导唱指示灯的符号个数。 |
| `--lang` | `auto` | 歌词语言，支持自动判断。 |
| `-f`, `--txt_format` | `hrh` | 输入歌词格式。 |
| `-cl`, `--characters_per_line` | `0` | 输出文件每行最大字数，`0` 表示不自动切行。 |
| `-cs`, `--chunk_seconds` | `0` | 自动分块目标时长，`0` 表示关闭自动分块。 |
| `--batch_songs` | 关闭 | 顺序处理 `songs/` 下的所有歌曲工作文件夹，一次只处理一首。 |
| `--songs_dir` | `songs` | 批量处理的歌曲根目录；也可以用 `--work_dir` 指定。 |
| `--batch_continue_on_error` | 关闭 | 批量处理时某首失败后继续下一首。 |
| `--realign` | 无 | 局部重对轴的 ASS 行范围，例如 `227-245`。 |
| `--realign_time` | 无 | 局部重对轴使用的音频时间范围，例如 `18:35-19:20`；省略时自动用选区上一句结束到下一句开始。 |
| `--realign_mode` | `karaoke` | ASS 范围编号方式；`karaoke` 为歌词 Dialogue 行，`event` 为 Aegisub 事件行。 |
| `--realign_ass` | `--output_ass` | 局部重对轴读取的 ASS 文件。 |
| `--realign_output` | `<工作文件夹名>_realign.ass` | 局部重对轴输出的 ASS 文件。 |
| `--realign_inplace` | 关闭 | 直接覆盖 `--realign_ass` 指定的 ASS 文件。 |
| `--realign_update_text` | 关闭 | 用选中 ASS 歌词同步覆盖输入歌词对应行。 |
| `--realign_text_output` | `<工作文件夹名>_realign.txt` | 未开启 `--realign_update_text` 时写出的同步歌词文件。 |

示例：

``` shell
python main.py -v 0.5 -tl 0.2
```

上面的命令会以 0.5 倍速处理音频，并用更精细的静音窗口辅助尾音处理。

## 环境配置

参考 `requirements.txt` 安装依赖，并根据系统和 GPU 情况安装合适版本的 PyTorch。

基础步骤：

1. 安装 Python，推荐 Python 3.13。
2. 根据系统和显卡安装 PyTorch。
3. 安装其他依赖：

``` shell
pip install janome librosa nltk pykakasi pyphen pypinyin
```

第一次运行时，程序可能会自动下载模型和字典，耗时会比较长。

## 注意事项

- 输入音频应尽量是干净的人声轨，伴奏残留越少，对齐越稳定。
- 歌词文本要尽量和人声音频内容一致，漏词、错词、重复段都会影响后续时间轴。
- 长音频建议使用 `--chunk_seconds` 或 `@chunk[...]`，不要强行整段一次性对齐。
- 自动分块只是近似策略；对 Live、串烧、长间奏音频，手动锚点通常更可靠。
- 生成的 ASS 文件建议再用 Aegisub 检查和微调。
