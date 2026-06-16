import argparse
import json
import shutil
import subprocess
import uuid
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

GENERATED_VIDEO_SUFFIXES = (
    "_kara",
    "_hardsub",
    "_hardcoded",
    "_subbed",
    "_burned",
)

PREPARED_ASS_SUFFIX = "_prepared"


def is_generated_video(path):
    stem = path.stem.lower()
    return any(stem.endswith(suffix) for suffix in GENERATED_VIDEO_SUFFIXES)


def is_prepared_ass(path):
    return path.suffix.lower() == ".ass" and path.stem.lower().endswith(PREPARED_ASS_SUFFIX)


def read_ass_resolution(ass_path):
    play_res_x = None
    play_res_y = None
    for raw_line in ass_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if line.lower().startswith("playresx:"):
            play_res_x = int(line.split(":", 1)[1].strip())
        elif line.lower().startswith("playresy:"):
            play_res_y = int(line.split(":", 1)[1].strip())
        if play_res_x and play_res_y:
            break
    if not play_res_x or not play_res_y:
        raise ValueError(f"Could not find PlayResX/PlayResY in {ass_path}")
    return play_res_x, play_res_y


def run_command(command, cwd=None):
    return subprocess.run(command, cwd=cwd, check=True)


def probe_video_resolution(video_path, ffprobe="ffprobe"):
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(video_path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"No video stream found in {video_path}")
    width = int(streams[0]["width"])
    height = int(streams[0]["height"])
    return width, height


def find_single_video(folder):
    videos = [
        path
        for path in sorted(Path(folder).iterdir())
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTENSIONS
        and not is_generated_video(path)
    ]
    if len(videos) != 1:
        names = ", ".join(path.name for path in videos) or "none"
        raise ValueError(f"Expected exactly one source video in {folder}, found {len(videos)}: {names}")
    return videos[0]


def find_prepared_ass(folder):
    prepared = [path for path in sorted(Path(folder).glob("*.ass")) if is_prepared_ass(path)]
    if len(prepared) != 1:
        names = ", ".join(path.name for path in prepared) or "none"
        raise ValueError(f"Expected exactly one *_prepared.ass in {folder}, found {len(prepared)}: {names}")
    return prepared[0]


def default_output_path(video_path, output_ext=".mp4"):
    output_ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    return video_path.with_name(f"{video_path.stem}_kara{output_ext}")


def normalize_video_path(video_path, folder):
    video_path = Path(video_path)
    if video_path.is_absolute():
        return video_path.resolve()

    folder_relative = folder / video_path
    if folder_relative.is_file():
        return folder_relative.resolve()
    return video_path.resolve()


def normalize_output_path(output_path, folder):
    output_path = Path(output_path)
    if output_path.is_absolute():
        return output_path.resolve()

    if output_path.parent == Path("."):
        return (folder / output_path).resolve()

    cwd_relative = output_path.resolve()
    if cwd_relative.parent == folder:
        return cwd_relative

    return (folder / output_path).resolve()


def build_video_filter(ass_resolution, temp_ass_name, video_resolution):
    ass_width, ass_height = ass_resolution
    video_width, video_height = video_resolution
    filters = []
    if (video_width, video_height) != (ass_width, ass_height):
        filters.append(f"scale={ass_width}:{ass_height}:flags=lanczos")
        filters.append("setsar=1")
    filters.append(f"subtitles=filename='{temp_ass_name}'")
    return ",".join(filters)


def burn_ass(
    ass_path,
    video_path=None,
    output_path=None,
    ffmpeg="ffmpeg",
    ffprobe="ffprobe",
    crf=18,
    preset="medium",
    audio_mode="copy",
    overwrite=False,
):
    ass_path = Path(ass_path).resolve()
    if not ass_path.is_file():
        raise FileNotFoundError(f"ASS file not found: {ass_path}")
    folder = ass_path.parent
    video_path = normalize_video_path(video_path, folder) if video_path else find_single_video(folder).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if video_path.parent != folder:
        raise ValueError("The ASS file and video must be in the same folder.")

    if output_path:
        output_path = normalize_output_path(output_path, folder)
    else:
        output_path = default_output_path(video_path).resolve()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Use --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ass_resolution = read_ass_resolution(ass_path)
    video_resolution = probe_video_resolution(video_path, ffprobe=ffprobe)
    temp_ass_name = f".fa_kara_burn_{uuid.uuid4().hex}.ass"
    temp_ass_path = folder / temp_ass_name

    try:
        shutil.copyfile(ass_path, temp_ass_path)
        video_filter = build_video_filter(ass_resolution, temp_ass_name, video_resolution)
        command = [
            ffmpeg,
            "-hide_banner",
            "-y" if overwrite else "-n",
            "-i",
            video_path.name,
            "-vf",
            video_filter,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            str(crf),
            "-pix_fmt",
            "yuv420p",
        ]
        if audio_mode == "copy":
            command.extend(["-c:a", "copy"])
        elif audio_mode == "aac":
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            raise ValueError("--audio-mode must be copy or aac")
        output_arg = output_path.name if output_path.parent == folder else str(output_path)
        command.extend(["-movflags", "+faststart", output_arg])

        print(
            f"Burning {ass_path.name} into {video_path.name} "
            f"({video_resolution[0]}x{video_resolution[1]} -> {ass_resolution[0]}x{ass_resolution[1]})"
        )
        run_command(command, cwd=folder)
    finally:
        try:
            temp_ass_path.unlink()
        except FileNotFoundError:
            pass

    return output_path


def iter_song_prepared_ass(songs_dir):
    for folder in sorted(path for path in Path(songs_dir).iterdir() if path.is_dir()):
        prepared = [path for path in sorted(folder.glob("*.ass")) if is_prepared_ass(path)]
        if prepared:
            yield folder, prepared


def batch_burn_songs(
    songs_dir,
    ffmpeg,
    ffprobe,
    crf,
    preset,
    audio_mode,
    overwrite,
    output_ext,
    continue_on_error,
):
    songs_dir = Path(songs_dir).resolve()
    if not songs_dir.exists():
        raise FileNotFoundError(f"Songs directory not found: {songs_dir}")

    processed = 0
    skipped = 0
    failed = 0
    for folder, prepared in iter_song_prepared_ass(songs_dir):
        if len(prepared) != 1:
            print(f"Skipped {folder}: expected one *_prepared.ass, found {len(prepared)}")
            skipped += 1
            continue
        ass_path = prepared[0]
        try:
            video_path = find_single_video(folder)
            output_path = default_output_path(video_path, output_ext=output_ext)
            if output_path.exists() and not overwrite:
                print(f"Skipped existing output: {output_path}")
                skipped += 1
                continue
            burn_ass(
                ass_path=ass_path,
                video_path=video_path,
                output_path=output_path,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                crf=crf,
                preset=preset,
                audio_mode=audio_mode,
                overwrite=overwrite,
            )
        except Exception as exc:
            print(f"Failed {folder}: {exc}")
            failed += 1
            if not continue_on_error:
                raise
            continue
        processed += 1

    print(f"Batch finished: {processed} processed, {skipped} skipped, {failed} failed.")


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Hard-burn a *_prepared.ass subtitle into the only source video in the same folder. "
            "If ASS PlayRes differs from the video resolution, the video is scaled to the ASS resolution first."
        )
    )
    parser.add_argument("ass_or_folder", nargs="?", help="A *_prepared.ass file, or a folder containing one.")
    parser.add_argument("output_video", nargs="?", help="Output video path for single-file mode.")
    parser.add_argument("--batch-songs", action="store_true", help="Process every song folder under songs/.")
    parser.add_argument("--songs-dir", default="songs", help="Songs directory used with --batch-songs.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg executable.")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe executable.")
    parser.add_argument("--crf", type=int, default=18, help="x264 CRF value. Lower is higher quality.")
    parser.add_argument("--preset", default="medium", help="x264 preset.")
    parser.add_argument("--audio-mode", choices=("copy", "aac"), default="copy", help="Audio handling.")
    parser.add_argument("--output-ext", default=".mp4", help="Batch output extension, default .mp4.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output videos.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue batch after one folder fails.")
    args = parser.parse_args()

    if args.batch_songs:
        if args.ass_or_folder or args.output_video:
            parser.error("ass_or_folder/output_video cannot be used with --batch-songs")
        batch_burn_songs(
            songs_dir=args.songs_dir,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            crf=args.crf,
            preset=args.preset,
            audio_mode=args.audio_mode,
            overwrite=args.overwrite,
            output_ext=args.output_ext,
            continue_on_error=args.continue_on_error,
        )
        return

    if not args.ass_or_folder:
        parser.error("ass_or_folder is required unless --batch-songs is used")

    target = Path(args.ass_or_folder)
    ass_path = find_prepared_ass(target) if target.is_dir() else target
    output_path = Path(args.output_video) if args.output_video else None
    result = burn_ass(
        ass_path=ass_path,
        output_path=output_path,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
        crf=args.crf,
        preset=args.preset,
        audio_mode=args.audio_mode,
        overwrite=args.overwrite,
    )
    print(f"Wrote hard-subbed video to {result}")


if __name__ == "__main__":
    main()
