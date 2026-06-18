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

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
}

AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".aiff",
    ".aif",
}

AUDIO_EXTENSIONS_REQUIRING_AAC_IN_MP4 = {
    ".wav",
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".aiff",
    ".aif",
}

GENERATED_VIDEO_SUFFIXES = (
    "_kara",
    "_hardsub",
    "_hardcoded",
    "_subbed",
    "_burned",
)

PREPARED_ASS_SUFFIX = "_prepared"
DEFAULT_EVENT_FORMAT = [
    "layer",
    "start",
    "end",
    "style",
    "name",
    "marginl",
    "marginr",
    "marginv",
    "effect",
    "text",
]


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


def probe_media_duration(media_path, ffprobe="ffprobe"):
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(media_path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError(f"Could not determine duration for {media_path}")
    return duration


def ass_time_to_seconds(value):
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid ASS timestamp: {value}")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def read_ass_duration(ass_path):
    in_events = False
    event_fields = DEFAULT_EVENT_FORMAT
    max_end = 0.0

    for raw_line in ass_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        lower = line.lower()

        if lower == "[events]":
            in_events = True
            continue
        if line.startswith("[") and lower != "[events]":
            in_events = False
            continue
        if not in_events:
            continue

        if lower.startswith("format:"):
            event_fields = [field.strip().lower() for field in line.split(":", 1)[1].split(",")]
            continue

        if not (lower.startswith("dialogue:") or lower.startswith("comment:")):
            continue

        values = line.split(":", 1)[1].split(",", len(event_fields) - 1)
        if len(values) < len(event_fields):
            continue
        try:
            end_index = event_fields.index("end")
        except ValueError as exc:
            raise ValueError(f"Could not find End field in [Events] Format of {ass_path}") from exc
        max_end = max(max_end, ass_time_to_seconds(values[end_index]))

    if max_end <= 0:
        raise ValueError(f"Could not determine subtitle duration from {ass_path}")
    return max_end


def list_source_videos(folder):
    return [
        path
        for path in sorted(Path(folder).iterdir())
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTENSIONS
        and not is_generated_video(path)
    ]


def list_source_images(folder):
    return [
        path
        for path in sorted(Path(folder).iterdir())
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]


def list_audio_files(folder):
    return [
        path
        for path in sorted(Path(folder).iterdir())
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]


def find_single_video(folder):
    videos = list_source_videos(folder)
    if len(videos) != 1:
        names = ", ".join(path.name for path in videos) or "none"
        raise ValueError(f"Expected exactly one source video in {folder}, found {len(videos)}: {names}")
    return videos[0]


def find_source_media(folder):
    videos = list_source_videos(folder)
    if len(videos) == 1:
        return videos[0], "video"
    if len(videos) > 1:
        names = ", ".join(path.name for path in videos)
        raise ValueError(f"Expected exactly one source video in {folder}, found {len(videos)}: {names}")

    images = list_source_images(folder)
    if images:
        return images, "image"

    raise ValueError(
        f"Expected one source video, or no videos plus at least one background image in {folder}; "
        "found no images"
    )


def find_optional_single_audio(folder):
    audio_files = list_audio_files(folder)
    if len(audio_files) > 1:
        names = ", ".join(path.name for path in audio_files)
        raise ValueError(f"Expected at most one audio file for image background mode in {folder}, found {len(audio_files)}: {names}")
    return audio_files[0] if audio_files else None


def find_prepared_ass(folder):
    prepared = [path for path in sorted(Path(folder).glob("*.ass")) if is_prepared_ass(path)]
    if len(prepared) != 1:
        names = ", ".join(path.name for path in prepared) or "none"
        raise ValueError(f"Expected exactly one *_prepared.ass in {folder}, found {len(prepared)}: {names}")
    return prepared[0]


def default_output_path(video_path, output_ext=".mp4"):
    output_ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    return video_path.with_name(f"{video_path.stem}_kara{output_ext}")


def default_ass_output_path(ass_path, output_ext=".mp4"):
    output_ext = output_ext if output_ext.startswith(".") else f".{output_ext}"
    stem = ass_path.stem
    if stem.lower().endswith(PREPARED_ASS_SUFFIX):
        stem = stem[: -len(PREPARED_ASS_SUFFIX)]
    return ass_path.with_name(f"{stem}_kara{output_ext}")


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


def build_image_filter(ass_resolution, temp_ass_name, image_fps):
    ass_width, ass_height = ass_resolution
    return ",".join(
        [
            f"fps={format_seconds(image_fps)}",
            f"scale={ass_width}:{ass_height}:force_original_aspect_ratio=decrease:flags=lanczos",
            f"pad={ass_width}:{ass_height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            f"subtitles=filename='{temp_ass_name}'",
        ]
    )


def build_audio_args(audio_mode, source_path=None, output_path=None):
    if audio_mode == "aac":
        return ["-c:a", "aac", "-b:a", "192k"]
    if audio_mode != "copy":
        raise ValueError("--audio-mode must be copy or aac")

    if (
        source_path
        and output_path
        and output_path.suffix.lower() == ".mp4"
        and source_path.suffix.lower() in AUDIO_EXTENSIONS_REQUIRING_AAC_IN_MP4
    ):
        return ["-c:a", "aac", "-b:a", "192k"]
    return ["-c:a", "copy"]


def format_seconds(value):
    return f"{value:.3f}".rstrip("0").rstrip(".")


def quote_ffconcat_path(value):
    return "'" + str(value).replace("\\", "/").replace("'", "'\\''") + "'"


def write_image_concat_file(concat_path, image_paths, image_duration):
    lines = ["ffconcat version 1.0"]
    for image_path in image_paths:
        lines.append(f"file {quote_ffconcat_path(image_path.name)}")
        lines.append(f"duration {format_seconds(image_duration)}")
    lines.append(f"file {quote_ffconcat_path(image_paths[-1].name)}")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    image_fps=30,
):
    if image_fps <= 0:
        raise ValueError("--image-fps must be positive")

    ass_path = Path(ass_path).resolve()
    if not ass_path.is_file():
        raise FileNotFoundError(f"ASS file not found: {ass_path}")
    folder = ass_path.parent
    source_path = None
    image_paths = []
    if video_path:
        candidate_path = normalize_video_path(video_path, folder)
        suffix = candidate_path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            source_path = candidate_path
            source_kind = "video"
        elif suffix in IMAGE_EXTENSIONS:
            image_paths = [candidate_path]
            source_path = candidate_path
            source_kind = "image"
        else:
            raise ValueError(f"Unsupported source media type: {candidate_path}")
    else:
        source_media, source_kind = find_source_media(folder)
        if source_kind == "video":
            source_path = source_media.resolve()
        else:
            image_paths = [path.resolve() for path in source_media]
            source_path = image_paths[0]

    if source_kind == "video":
        if not source_path.is_file():
            raise FileNotFoundError(f"Source media file not found: {source_path}")
        if source_path.parent != folder:
            raise ValueError("The ASS file and source media must be in the same folder.")
    else:
        if not image_paths:
            raise ValueError("Image background mode requires at least one image.")
        for image_path in image_paths:
            if not image_path.is_file():
                raise FileNotFoundError(f"Background image file not found: {image_path}")
            if image_path.parent != folder:
                raise ValueError("The ASS file and background images must be in the same folder.")

    if output_path:
        output_path = normalize_output_path(output_path, folder)
    elif source_kind == "image":
        output_path = default_ass_output_path(ass_path).resolve()
    else:
        output_path = default_output_path(source_path).resolve()
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Use --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ass_resolution = read_ass_resolution(ass_path)
    temp_ass_name = f".fa_kara_burn_{uuid.uuid4().hex}.ass"
    temp_ass_path = folder / temp_ass_name
    temp_concat_path = None

    try:
        shutil.copyfile(ass_path, temp_ass_path)
        command = [ffmpeg, "-hide_banner", "-y" if overwrite else "-n"]

        if source_kind == "video":
            video_resolution = probe_video_resolution(source_path, ffprobe=ffprobe)
            video_filter = build_video_filter(ass_resolution, temp_ass_name, video_resolution)
            command.extend(
                [
                    "-i",
                    source_path.name,
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
            )
            command.extend(build_audio_args(audio_mode))
            print(
                f"Burning {ass_path.name} into {source_path.name} "
                f"({video_resolution[0]}x{video_resolution[1]} -> {ass_resolution[0]}x{ass_resolution[1]})"
            )
        else:
            audio_path = find_optional_single_audio(folder)
            duration = probe_media_duration(audio_path, ffprobe=ffprobe) if audio_path else read_ass_duration(ass_path)
            video_filter = build_image_filter(ass_resolution, temp_ass_name, image_fps)
            if len(image_paths) == 1:
                command.extend(
                    [
                        "-loop",
                        "1",
                        "-framerate",
                        format_seconds(image_fps),
                        "-i",
                        image_paths[0].name,
                    ]
                )
            else:
                image_duration = duration / len(image_paths)
                temp_concat_name = f".fa_kara_images_{uuid.uuid4().hex}.ffconcat"
                temp_concat_path = folder / temp_concat_name
                write_image_concat_file(temp_concat_path, image_paths, image_duration)
                command.extend(["-f", "concat", "-safe", "0", "-i", temp_concat_name])
            if audio_path:
                command.extend(["-i", audio_path.name])
            command.extend(
                [
                    "-t",
                    format_seconds(duration),
                    "-vf",
                    video_filter,
                    "-map",
                    "0:v:0",
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
            if audio_path:
                command.extend(["-map", "1:a:0"])
                command.extend(build_audio_args(audio_mode, source_path=audio_path, output_path=output_path))
            else:
                command.append("-an")
            audio_label = audio_path.name if audio_path else "no audio"
            image_label = (
                image_paths[0].name
                if len(image_paths) == 1
                else f"{len(image_paths)} images, {format_seconds(duration / len(image_paths))}s each"
            )
            print(
                f"Burning {ass_path.name} over {image_label} "
                f"({ass_resolution[0]}x{ass_resolution[1]}, {format_seconds(duration)}s, audio: {audio_label})"
            )

        output_arg = output_path.name if output_path.parent == folder else str(output_path)
        command.extend(["-movflags", "+faststart", output_arg])

        run_command(command, cwd=folder)
    finally:
        try:
            temp_ass_path.unlink()
        except FileNotFoundError:
            pass
        if temp_concat_path:
            try:
                temp_concat_path.unlink()
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
    image_fps,
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
            source_media, source_kind = find_source_media(folder)
            if source_kind == "image":
                output_path = default_ass_output_path(ass_path, output_ext=output_ext)
                burn_source_path = None
            else:
                output_path = default_output_path(source_media, output_ext=output_ext)
                burn_source_path = source_media
            if output_path.exists() and not overwrite:
                print(f"Skipped existing output: {output_path}")
                skipped += 1
                continue
            burn_ass(
                ass_path=ass_path,
                video_path=burn_source_path,
                output_path=output_path,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                crf=crf,
                preset=preset,
                audio_mode=audio_mode,
                overwrite=overwrite,
                image_fps=image_fps,
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
            "If there is no source video but one or more images, the images are used as the background."
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
    parser.add_argument("--image-fps", type=float, default=30, help="FPS used when the source is a still image.")
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
            image_fps=args.image_fps,
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
        image_fps=args.image_fps,
    )
    print(f"Wrote hard-subbed video to {result}")


if __name__ == "__main__":
    main()
