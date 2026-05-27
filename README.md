# FA-Kara
一个基于注音歌词文本和人声音频的自动打轴工具，主要参考了[yohane](https://github.com/Japan7/yohane)、[Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara/)。

建议使用此工具处理日语歌曲，但底层模型实际上不限语种。

## 项目理解
FA-Kara 的目标是用“人声音频 + 带注音的歌词文本”自动生成可编辑的卡拉 OK 时间轴。它不是直接从完整歌曲中识别歌词，而是要求用户提前准备好与音频内容一致的歌词文本，并在歌词中提供必要的振假名或辅助读音。程序会把歌词拆成可对齐的发音 token，再用人声音频做 forced alignment，最后把推理得到的时间写回歌词结构。

整体流程大致如下：
1. `main.py` 读取输入文件和命令行参数，组织完整处理流程。
2. `haruraw2norm.py` 解析 `i.txt`，将 `{漢字|かな}`、`[字|romaji]`、假名、英文和数字等内容转换为带 `pron` 的规范结构。
3. `align.py` 使用 torchaudio 的 MMS_FA 模型，将发音 token 和人声音频进行强制对齐。
4. `norm2ass.py` 将对齐后的结构输出为 `o.ass`，主要用于 Aegisub 继续编辑。
5. `norm2lrc.py` 输出 `o_rlf.lrc` 和 `o_ruby.lrc`，方便 RhythmicaLyrics 或 NicoKaraMaker3 后续使用。

项目里“会不会参与音频识别”的关键在于结构中是否有 `pron`。有 `pron` 的歌词元素会被送入对齐模型；没有 `pron` 的显示元素可以只参与输出，不参与识别。

## 使用说明
配置好Python环境后，准备以下两个文件（可参考示例）：
#### 1. 音频
- **格式及命名**: `i.wav`
- **要求**: 从歌曲中分离出的人声，例如可以用[UVR](https://ultimatevocalremover.com/)或MSST生成

#### 2. 歌词
- **格式及命名**: `i.txt`
- **要求**: 需要有振假名注音，可使用RhythmicaLyrics出力->春日向けテキスト；尽可能与音频内容保持一致

将音频、歌词和各个`.py`文件放在同一目录，运行指令：
``` shell
python main.py
```

若运行成功，该目录下会生成三个文件：
- `o.ass`: 可在Aegisub中灵活编辑
- `o_rlf.lrc`: 可在RhythmicaLyrics中灵活编辑
- `o_ruby.lrc`: 可直接在NicoKaraMaker3使用，默认提前150ms

## `i.txt` 格式
普通歌词仍使用原有的春日向けテキスト风格：
``` txt
ずっと{知|し}り{得|え}ないことは{良|い}いこと
[字|romaji]
```

其中：
- `{漢字|かな}` 表示振假名注音，会拆成可对齐的日语发音。
- `[字|romaji]` 表示辅助读音，可用于手动指定不容易自动推断的发音。
- 普通假名、英文、数字会按现有逻辑转换为发音 token。

### 注释段
本项目新增了 `i.txt` 注释段。注释段用于在歌曲中显示几秒说明文字，例如间奏提示、剧情提示、台词提示等。它不会被读入音频识别，也不会写入 `o_rlf.lrc` 或 `o_ruby.lrc`，只会写入 `o.ass`。

写法如下：
``` txt
@comment[3.0] 显示文字
@注释[2.5] 也可以使用中文标记
```

方括号内是显示时长，单位为秒。`3.0` 表示显示 3 秒，也可以写成 `3`、`3s`、`3sec`、`3秒`。

注释段的排时规则：
- 注释段会自动放在前一句歌词段结束之后。
- 注释段会避开下一句歌词段，不会主动覆盖下一句歌词。
- 如果到下一句歌词前的空隙不足，程序会自动缩短注释段。
- 如果完全没有可用空隙，程序会跳过该注释段并在终端打印提示。

### 高级选项
运行指令时还可以添加参数。例如一首歌曲的语速偏快，你可以尝试如下指令：
``` shell
python main.py -v 0.5 -tl 0.2
```
此时，模型将以0.5倍速[^1]处理音频，并在音频乐句切割时注意到更加精细的静音片段[^2]，这可能有助于提升推理效果。

[^1]: `-v`选项默认值为1，且在使用时无需人工调整时间轴。
[^2]: `-tl`选项默认值为0.8。简单地理解，脚本在进行乐句切割时会以该值（单位：秒）的一半作为精度来识别静音窗口。

更多选项请用`-h`查看。

## 环境配置
参考`requirements.txt`，请根据实际情况调整。

如果你从未接触过Python，可按照如下步骤配置环境：
1. 安装[Python](https://www.python.org/)（推荐版本3.13），并配置好环境变量；
2. 根据操作系统与GPU情况，安装对应的[PyTorch](https://pytorch.org/get-started/locally/)；
3. 再安装其他库，如运行指令`pip install janome librosa nltk pykakasi pyphen pypinyin`；
4. 第一次运行程序时会自动下载模型与字典，这可能需要更多的时间。

## 模型简介
主要使用librosa进行音频预处理，结合Janome、pykakasi、NLTK、Pyphen、PyPinyin标注歌词文本读音，输入基于PyTorch的[MMS_FA](https://arxiv.org/abs/2305.13516)进行推理。

目前的效果尚不尽如人意，欢迎交流。
