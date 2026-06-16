# song_a 工作文件夹样例

把一首歌的工作文件放在同一个文件夹里。第一次运行时，如果这里只有一个音频文件和一个歌词 `.txt`，程序会自动把它们整理成和文件夹同名：

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
