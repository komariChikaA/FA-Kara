# FA-Kara

FA-Kara 是一个面向卡拉 OK 字幕制作的自动打轴工具。它以“注音歌词文本 + 人声音频”为输入，使用 `torchaudio` 的 MMS_FA forced alignment 模型生成可编辑的 ASS 和 LRC，再配合 Aegisub 的 Karaoke Templater、ffmpeg 完成特效字幕压制和成片合并。

项目主要参考 [yohane](https://github.com/Japan7/yohane) 和 [Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara/)。当前版本主要强化了日语歌曲、混合语种歌词、长音频分块、局部重对轴、Aegisub 后处理、批量压制和批量合并流程。

## 主要功能

- 从人声音频和注音歌词自动生成 ASS 字幕。
- 同时输出 RhythmicaLyrics 用 LRC 和 NicoKaraMaker3 用 ruby LRC。
- 支持 `{漢字|かな}`、`[显示文字|romaji]`、英文、中文、数字和自定义发音表。
- 支持 `@comment[...]` 注释段，只写入 ASS，不参与对齐。
- 支持 `@chunk[...]` 手动分块和 `--chunk_seconds` 自动分块，适合长音频或串烧。
- 支持局部重对轴，只重跑 ASS 中指定的一句或一段。
- 支持批量处理 `songs/` 下所有歌曲工作文件夹。
- 提供 `prepare_kara_ass.py` 自动准备 Aegisub Karaoke Templater 所需模板行。
- 提供 `burn_prepared_ass.py` 将 ASS 特效硬压进视频；没有视频时也可用单张图片作为背景生成视频。
- 提供 `concat_generated_videos.py` 按原视频创建时间合并 `_kara` 成片。

## 推荐工作流

单首歌的完整流程通常是：

1. 准备一个歌曲文件夹，放入人声音频和注音歌词。
2. 运行 `main.py` 自动生成基础 ASS/LRC。
3. 用 `prepare_kara_ass.py` 生成模板准备版 ASS。
4. 在 Aegisub 中打开准备版 ASS，运行 `Automation > Apply karaoke template`，保存最终特效 ASS。
5. 用 `burn_prepared_ass.py` 把最终 ASS 硬压进同文件夹视频。
6. 多首歌都压好后，用 `concat_generated_videos.py` 合并成合集视频。

最短示例：

``` shell
python main.py -p songs/song_a
python prepare_kara_ass.py songs/song_a/song_a.ass
```

然后在 Aegisub 中打开 `songs/song_a/song_a_prepared.ass`，运行 `Automation > Apply karaoke template` 并保存。确认同文件夹下有唯一源视频，或没有视频但有背景图片后压制：

``` shell
python burn_prepared_ass.py songs/song_a
```

合并所有已生成的 `_kara.mp4`：

``` shell
python concat_generated_videos.py --dry-run
python concat_generated_videos.py merged_kara.mp4
```

## 目录约定

推荐一个歌曲一个文件夹，统一放在 `songs/` 下：

``` txt
songs/
  song_a/
    song_a.txt
    song_a.wav
    song_a.mp4
  song_b/
    lyrics.txt
    vocal.wav
    source.mp4
```

`main.py` 会优先使用工作文件夹里的同名输入。如果文件夹里只有一个歌词 `.txt` 和一个音频文件，也会自动识别，并在运行开始时规范为工作文件夹同名文件。

常见输出：

| 文件 | 用途 |
| --- | --- |
| `<歌曲名>.ass` | 基础 ASS，可在 Aegisub 中检查和微调。 |
| `<歌曲名>_rlf.lrc` | RhythmicaLyrics 可编辑 LRC。 |
| `<歌曲名>_ruby.lrc` | NicoKaraMaker3 用 ruby LRC，默认 Offset 为 `-150`。 |
| `<歌曲名>_prepared.ass` | 模板准备版 ASS，交给 Aegisub Karaoke Templater 展开。 |
| `<原视频名>_kara.mp4` | 已硬压字幕的视频。 |

本地歌曲文件、音频、视频和生成物通常不建议提交到仓库，按 `.gitignore` 管理即可。

## 安装

建议使用 Python 3.13。先根据系统和显卡安装合适版本的 PyTorch，然后安装项目依赖：

``` shell
pip install janome librosa nltk pykakasi pyphen pypinyin
```

视频压制和合并需要 `ffmpeg` 和 `ffprobe`。如果没有加入 `PATH`，可以在相关脚本里用 `--ffmpeg` / `--ffprobe` 指定路径。

第一次运行时，程序可能会下载模型或字典，耗时会比较久。

## 歌词文本格式

基础写法：

``` txt
ずっと{知|し}り{得|え}ないことは{良|い}いこと
```

常用标记：

``` txt
{漢字|かな}
[显示文字|romaji]
@comment[3.0] 显示 3 秒的说明文字
@chunk[12:34]
```

说明：

- `{漢字|かな}` 用于日语振假名，显示汉字，按假名参与对齐。
- `[显示文字|romaji]` 用于手动指定读音，适合专名、生造词、英文特殊读法。
- 普通假名、英文、中文和数字会按项目内置规则转换为发音 token。
- 空行会作为歌词段落边界。
- `@comment[...]` 只输出到 ASS，不参与音频识别，也不写入 LRC。
- `@chunk[...]` 只作为长音频分块锚点，不输出到 ASS/LRC。

注释段示例：

``` txt
@comment[3] 第一章
@注释[2.5] 间奏提示
```

分块锚点示例：

``` txt
第一段歌词
...
@chunk[12:34]
第二段歌词从这里之后开始
```

支持的时间格式包括 `75`、`75秒`、`12:34`、`01:02:03`。

## 自定义英文发音

如果英文单词查不到或自动读音不准，终端会提示：

``` txt
Unknown English words found. Add them to pronunciations.txt if the guessed pronunciation is wrong:
  Komariver=<romaji>
```

在歌曲工作文件夹创建 `pronunciations.txt`：

``` txt
Gucci=guchi
YOASOBI=yoasobi
can't=kant
```

多首歌共用同一份发音表：

``` shell
python main.py -p songs/song_a --pronunciation_file ..\pronunciations.txt
```

## 生成 ASS 和 LRC

默认从当前目录找输入：

``` shell
python main.py
```

指定歌曲工作文件夹：

``` shell
python main.py -p songs/song_a
```

显式指定输入输出：

``` shell
python main.py -p songs/song_a -it lyrics.txt -ia vocal.wav -o song_a.ass
python main.py -p songs/song_a -it lyrics.txt -ia vocal.wav -o song_a.ass --output_rlf song_a_rlf.lrc --output_ruby song_a_ruby.lrc
```

降低音频速度，通常能让细节更稳，但会更慢：

``` shell
python main.py -p songs/song_a -v 0.5
```

限制每行最大显示字符数：

``` shell
python main.py -p songs/song_a -cl 18
```

长音频自动分块：

``` shell
python main.py -p songs/live_song --chunk_seconds 300
```

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `-p`, `--path_io`, `--work_dir` | 当前项目目录 | 工作文件夹。 |
| `-it`, `--input_text` | 自动 | 输入歌词文本。 |
| `-ia`, `--input_audio` | 自动 | 输入人声音频。 |
| `-o`, `--output_ass` | `<工作文件夹名>.ass` | 输出 ASS。 |
| `--output_rlf` | `<工作文件夹名>_rlf.lrc` | 输出 RLF LRC。 |
| `--output_ruby` | `<工作文件夹名>_ruby.lrc` | 输出 ruby LRC。 |
| `--pronunciation_file` | `pronunciations.txt` | 自定义英文发音表。 |
| `-v`, `--audio_speedx` | `1` | 推理时的音频倍速。 |
| `-cs`, `--chunk_seconds` | `0` | 自动分块目标时长，`0` 表示关闭。 |
| `-cl`, `--characters_per_line` | `0` | 自动切行字符数，`0` 表示不切。 |
| `--lang` | `auto` | 歌词语言。 |
| `--offset` | `-150` | ruby LRC 的 Offset。 |
| `--bpm` | `60` | 导唱指示灯 BPM。 |
| `--bpb` | `3` | 导唱指示灯符号个数。 |

## 批量打轴

处理 `songs/` 下所有歌曲文件夹：

``` shell
python main.py --batch_songs
```

失败后继续下一首：

``` shell
python main.py --batch_songs --batch_continue_on_error
```

重跑已经生成过 ASS 的歌曲：

``` shell
python main.py --batch_songs --batch_force
```

指定其他歌曲根目录：

``` shell
python main.py --batch_songs --songs_dir D:\Karaoke\songs
```

批量模式会复用全局参数，例如：

``` shell
python main.py --batch_songs --chunk_seconds 300 -v 0.5 --batch_continue_on_error
```

批量模式不接受 `-it`、`-ia`、`-o` 和局部重对轴参数。每首歌都会用自己的工作文件夹默认命名。

## 局部重对轴

如果某几句已经在 ASS 中手动修过文字或时间，只想重跑这一段，可以使用局部重对轴。

按 karaoke 歌词行编号重对一行：

``` shell
python main.py -p songs/song_a --realign 227
```

按 Aegisub 事件列表编号重对一行：

``` shell
python main.py -p songs/song_a --realign 227 --realign_mode event
```

重对一段，并手动指定音频范围：

``` shell
python main.py -p songs/song_a --realign 227-245 --realign_time 18:35-19:20
```

直接覆盖原 ASS：

``` shell
python main.py -p songs/song_a --realign 227-245 --realign_time 18:35-19:20 --realign_inplace
```

同时把 ASS 中修改过的歌词同步回输入歌词：

``` shell
python main.py -p songs/song_a --realign 227-245 --realign_time 18:35-19:20 --realign_inplace --realign_update_text
```

如果省略 `--realign_time`，程序会自动用选区上一句结束时间和下一句开始时间推导搜索范围。`--realign_time` 支持 `75-90`、`18:35-19:20`、`00:18:35-00:19:20`。

## Aegisub 模板准备

`prepare_kara_ass.py` 用于把基础 ASS 转成模板准备版 ASS。它会插入 karaoke template 配置、清理空 `\k` 尾音、交替套用 `K14` / `K16` 样式，并把歌词行提前指定时间。

单文件：

``` shell
python prepare_kara_ass.py songs/song_a/song_a.ass
python prepare_kara_ass.py songs/song_a/song_a.ass songs/song_a/song_a_prepared.ass
```

调整提前量，默认是 5000 ms：

``` shell
python prepare_kara_ass.py songs/song_a/song_a.ass --lead-in-ms 3000
```

批量准备 `songs/` 下所有 ASS：

``` shell
python prepare_kara_ass.py --batch-songs
python prepare_kara_ass.py --batch-songs --overwrite
python prepare_kara_ass.py --batch-songs --songs-dir D:\Karaoke\songs
```

直接覆盖原 ASS：

``` shell
python prepare_kara_ass.py songs/song_a/song_a.ass --in-place
```

建议优先生成 `*_prepared.ass` 检查无误后再覆盖。最后仍需要在 Aegisub 中打开准备版 ASS，运行：

``` txt
Automation > Apply karaoke template
```

## 硬压字幕到视频

`burn_prepared_ass.py` 使用 ffmpeg/libass 把 ASS 字幕硬压进同文件夹下的视频。脚本会读取 ASS 的 `PlayResX` / `PlayResY`；如果视频分辨率不同，会先把视频缩放到 ASS 分辨率。

如果歌曲文件夹里没有源视频，但有一张或多张 `jpg` / `png` / `webp` / `bmp` / `tif` 图片，脚本会自动把图片作为背景图生成视频。单张图片会铺满整段；多张图片会按文件名排序，并把总时长平均分给每张图。图片会保持比例并补边到 ASS 分辨率；同文件夹里有唯一音频时会写入音频并使用音频时长，没有音频时使用 ASS 最后一条事件的结束时间。

注意：如果 `*_prepared.ass` 还没有在 Aegisub 中运行过 `Apply karaoke template`，高级模板特效还没有展开。要压最终特效，请先在 Aegisub 中保存展开后的 ASS。

单文件夹压制：

``` shell
python burn_prepared_ass.py songs/song_a
```

指定 ASS 和输出文件：

``` shell
python burn_prepared_ass.py songs/song_a/song_a_prepared.ass song_a_kara.mp4
```

质量参数：

``` shell
python burn_prepared_ass.py songs/song_a --crf 16 --preset slow
python burn_prepared_ass.py songs/song_a --audio-mode aac
python burn_prepared_ass.py songs/song_a --overwrite
```

图片背景模式无需额外参数；只要文件夹里没有源视频且有图片，就会自动启用。图片模式默认 30fps：

``` shell
python burn_prepared_ass.py songs/song_with_cover
python burn_prepared_ass.py songs/song_with_cover --image-fps 24
```

批量压制：

``` shell
python burn_prepared_ass.py --batch-songs --continue-on-error
python burn_prepared_ass.py --batch-songs --overwrite
python burn_prepared_ass.py --batch-songs --crf 16 --preset slow --continue-on-error
```

指定 ffmpeg：

``` shell
python burn_prepared_ass.py songs/song_a --ffmpeg D:\ffmpeg\bin\ffmpeg.exe --ffprobe D:\ffmpeg\bin\ffprobe.exe
```

批量模式要求每个歌曲文件夹里有唯一的 `*_prepared.ass`，并且有唯一源视频；如果没有源视频，则要求至少有一张背景图片。输出默认为 `源视频名_kara.mp4`；图片模式下按 ASS 名输出，例如 `song_prepared.ass` 会生成 `song_kara.mp4`。如果 MP4 输出遇到 `wav` / `flac` 等不适合直接封装的音频，会自动转成 AAC。

## 合并成片

`concat_generated_videos.py` 会扫描 `songs/` 下的 `_kara` 视频，并按同文件夹原视频的创建时间排序合并。排序依据是原视频，不是 `_kara.mp4` 的生成时间。

预览顺序：

``` shell
python concat_generated_videos.py --dry-run
```

合并：

``` shell
python concat_generated_videos.py merged_kara.mp4
```

覆盖已有输出：

``` shell
python concat_generated_videos.py merged_kara.mp4 --overwrite
```

如果直接拼接失败，改用重编码：

``` shell
python concat_generated_videos.py merged_kara.mp4 --reencode --audio-mode aac
```

更多例子：

``` shell
python concat_generated_videos.py merged_kara.mp4 --songs-dir D:\Karaoke\songs
python concat_generated_videos.py merged_kara.mp4 --generated-suffix _kara --generated-suffix _hardsub
python concat_generated_videos.py merged_kara.mp4 --ffmpeg D:\ffmpeg\bin\ffmpeg.exe
```

## 常见完整命令

单首歌从打轴到压制：

``` shell
python main.py -p songs/song_a
python prepare_kara_ass.py songs/song_a/song_a.ass
```

在 Aegisub 中展开模板并保存后：

``` shell
python burn_prepared_ass.py songs/song_a --crf 16 --preset slow
```

批量打轴、批量准备、批量压制：

``` shell
python main.py --batch_songs --batch_continue_on_error
python prepare_kara_ass.py --batch-songs --overwrite
python burn_prepared_ass.py --batch-songs --continue-on-error
```

全部压好后合并：

``` shell
python concat_generated_videos.py --dry-run
python concat_generated_videos.py merged_kara.mp4
```

## 注意事项

- 输入音频应尽量是干净的人声轨，伴奏残留越少，对齐越稳。
- 歌词文本要尽量和人声音频一致，漏词、错词、重复段都会影响时间轴。
- 长音频建议使用 `--chunk_seconds` 或手动 `@chunk[...]`，不要强行整段一次性对齐。
- 生成的 ASS 建议先在 Aegisub 中检查和微调。
- `prepare_kara_ass.py` 只是模板准备，不会替你执行 Aegisub 的 Karaoke Templater。
- 硬压脚本优先使用同文件夹唯一源视频；没有源视频时可使用一张或多张图片作为背景。已经生成的 `_kara` 视频会被排除，不会当作源视频。
- 合并脚本默认用 `-c copy`，如果视频参数不一致，使用 `--reencode --audio-mode aac`。
