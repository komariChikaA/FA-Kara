# song_a 工作文件夹样例

把一首歌的工作文件放在同一个文件夹里。`main.py` 会自动识别这里的歌词和音频；默认优先使用 `i.txt`，不会改名。如果这里只有一个音频文件和一个歌词 `.txt`，并且希望按原项目那样整理成文件夹同名，加上 `--normalize_work_files`：

```txt
song_a/song_a.wav
song_a/song_a.txt
```

然后默认生成：

```txt
song_a/song_a.ass
song_a/song_a_realign.ass
song_a/song_a_rlf.lrc
song_a/song_a_ruby.lrc
```

这些音频、歌词、ASS、LRC 和 `pronunciations.txt` 都会被 `.gitignore` 忽略；这里只提交这个 README 作为目录样例。
