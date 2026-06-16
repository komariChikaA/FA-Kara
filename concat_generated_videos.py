import argparse
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".ts",
    ".m2ts",
    ".flv",
    ".wmv",
}


@dataclass(frozen=True)
class Clip:
    generated_path: Path
    source_path: Path
    source_created_at: float


def normalize_suffixes(values):
    suffixes = values or ["_kara"]
    normalized = []
    for suffix in suffixes:
        suffix = suffix.strip()
        if not suffix:
            continue
        normalized.append(suffix.lower())
    if not normalized:
        raise ValueError("At least one generated suffix is required.")
    return tuple(normalized)


def is_video(path):
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS


def matching_generated_suffix(path, suffixes):
    stem = path.stem.lower()
    for suffix in suffixes:
        if stem.endswith(suffix):
            return suffix
    return None


def is_generated_video(path, suffixes):
    return is_video(path) and matching_generated_suffix(path, suffixes) is not None


def source_stem_for_generated(path, suffixes):
    suffix = matching_generated_suffix(path, suffixes)
    if suffix is None:
        return None
    return path.stem[: -len(suffix)]


def source_created_at(path):
    stat = path.stat()
    return getattr(stat, "st_birthtime", stat.st_ctime)


def find_source_video(generated_path, suffixes):
    source_stem = source_stem_for_generated(generated_path, suffixes)
    if not source_stem:
        raise ValueError(f"Could not infer source video name for {generated_path}")

    exact_matches = [
        path
        for path in sorted(generated_path.parent.iterdir())
        if is_video(path)
        and not is_generated_video(path, suffixes)
        and path.stem == source_stem
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]
    if len(exact_matches) > 1:
        names = ", ".join(path.name for path in exact_matches)
        raise ValueError(f"Multiple source videos match {generated_path.name}: {names}")

    source_videos = [
        path
        for path in sorted(generated_path.parent.iterdir())
        if is_video(path) and not is_generated_video(path, suffixes)
    ]
    if len(source_videos) == 1:
        return source_videos[0]

    names = ", ".join(path.name for path in source_videos) or "none"
    raise ValueError(
        f"Expected one source video beside {generated_path.name}, found {len(source_videos)}: {names}"
    )


def iter_generated_videos(songs_dir, suffixes, output_path):
    output_path = output_path.resolve()
    for path in sorted(songs_dir.rglob("*")):
        if not is_generated_video(path, suffixes):
            continue
        resolved = path.resolve()
        if resolved == output_path:
            continue
        yield resolved


def collect_clips(songs_dir, suffixes, output_path):
    clips = []
    for generated_path in iter_generated_videos(songs_dir, suffixes, output_path):
        source_path = find_source_video(generated_path, suffixes).resolve()
        clips.append(
            Clip(
                generated_path=generated_path,
                source_path=source_path,
                source_created_at=source_created_at(source_path),
            )
        )
    clips.sort(key=lambda clip: (clip.source_created_at, str(clip.source_path).lower()))
    return clips


def escape_ffconcat_path(path):
    text = path.resolve().as_posix()
    return "'" + text.replace("'", "'\\''") + "'"


def write_concat_list(path, clips):
    lines = ["ffconcat version 1.0"]
    for clip in clips:
        lines.append(f"file {escape_ffconcat_path(clip.generated_path)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def run_ffmpeg(
    clips,
    output_path,
    ffmpeg,
    overwrite,
    reencode,
    crf,
    preset,
    audio_mode,
    keep_list,
):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    list_path = output_path.parent / f".fa_kara_concat_{uuid.uuid4().hex}.ffconcat"
    write_concat_list(list_path, clips)

    command = [
        ffmpeg,
        "-hide_banner",
        "-y" if overwrite else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
    ]
    if reencode:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                preset,
                "-crf",
                str(crf),
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if audio_mode == "copy":
            command.extend(["-c:a", "copy"])
        elif audio_mode == "aac":
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            raise ValueError("--audio-mode must be copy or aac")
    else:
        command.extend(["-c", "copy"])

    command.extend(["-movflags", "+faststart", str(output_path)])

    try:
        subprocess.run(command, check=True)
    finally:
        if not keep_list:
            try:
                list_path.unlink()
            except FileNotFoundError:
                pass


def print_order(clips):
    for index, clip in enumerate(clips, start=1):
        created = datetime.fromtimestamp(clip.source_created_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{index:02d}. {created} | {clip.generated_path}")
        print(f"    source: {clip.source_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Concatenate generated karaoke videos under songs/, sorted by the creation time "
            "of their original source videos."
        )
    )
    parser.add_argument("output_video", nargs="?", default="merged_kara.mp4", help="Output merged video path.")
    parser.add_argument("--songs-dir", default="songs", help="Root songs directory.")
    parser.add_argument(
        "--generated-suffix",
        action="append",
        help="Generated video suffix to include. Can be repeated. Default: _kara",
    )
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite output video if it exists.")
    parser.add_argument("--dry-run", action="store_true", help="Only print the merge order.")
    parser.add_argument(
        "--reencode",
        action="store_true",
        help="Re-encode while concatenating. Use this if stream-copy concat fails.",
    )
    parser.add_argument("--crf", type=int, default=18, help="x264 CRF used with --reencode.")
    parser.add_argument("--preset", default="medium", help="x264 preset used with --reencode.")
    parser.add_argument(
        "--audio-mode",
        choices=("copy", "aac"),
        default="copy",
        help="Audio handling used with --reencode.",
    )
    parser.add_argument("--keep-list", action="store_true", help="Keep the temporary ffconcat list file.")
    args = parser.parse_args()

    songs_dir = Path(args.songs_dir).resolve()
    if not songs_dir.exists():
        raise FileNotFoundError(f"Songs directory not found: {songs_dir}")

    output_path = Path(args.output_video).resolve()
    suffixes = normalize_suffixes(args.generated_suffix)
    clips = collect_clips(songs_dir, suffixes, output_path)
    if not clips:
        raise ValueError(f"No generated videos found under {songs_dir}")

    print_order(clips)
    if args.dry_run:
        print(f"Dry run only. {len(clips)} videos would be merged into {output_path}")
        return

    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Use --overwrite to replace it.")

    run_ffmpeg(
        clips=clips,
        output_path=output_path,
        ffmpeg=args.ffmpeg,
        overwrite=args.overwrite,
        reencode=args.reencode,
        crf=args.crf,
        preset=args.preset,
        audio_mode=args.audio_mode,
        keep_list=args.keep_list,
    )
    print(f"Wrote merged video to {output_path}")


if __name__ == "__main__":
    main()
