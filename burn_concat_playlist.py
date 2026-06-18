import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from burn_prepared_ass import (
    burn_ass,
    default_ass_output_path,
    default_output_path,
    find_prepared_ass,
    find_source_media,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def read_order_file(path):
    entries = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append((line_no, line))
    if not entries:
        raise ValueError(f"Order file is empty: {path}")
    return entries


def resolve_playlist(order_file, songs_dir, output_ext):
    entries = read_order_file(order_file)
    folders = {folder.name: folder for folder in songs_dir.iterdir() if folder.is_dir()}
    playlist = []
    seen = set()
    missing = []

    for line_no, folder_name in entries:
        folder = folders.get(folder_name)
        if folder is None:
            missing.append(f"{order_file}:{line_no}: {folder_name}")
            continue
        if folder in seen:
            raise ValueError(f"Duplicate folder in order file: {folder_name}")
        seen.add(folder)

        ass_path = find_prepared_ass(folder)
        source_media, source_kind = find_source_media(folder)
        if source_kind == "video":
            output_path = default_output_path(source_media, output_ext=output_ext)
            burn_source_path = source_media
        else:
            output_path = default_ass_output_path(ass_path, output_ext=output_ext)
            burn_source_path = None

        playlist.append(
            {
                "folder": folder,
                "ass": ass_path,
                "source": source_media,
                "source_kind": source_kind,
                "burn_source": burn_source_path,
                "output": output_path,
            }
        )

    if missing:
        raise FileNotFoundError("Could not find ordered song folder(s):\n" + "\n".join(missing))
    return playlist


def escape_ffconcat_path(path):
    text = path.resolve().as_posix()
    return "'" + text.replace("'", "'\\''") + "'"


def write_concat_list(path, playlist):
    lines = ["ffconcat version 1.0"]
    for item in playlist:
        lines.append(f"file {escape_ffconcat_path(item['output'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def print_playlist(playlist):
    for index, item in enumerate(playlist, start=1):
        print(f"{index:02d}. {item['folder'].name}")
        print(f"    ass: {item['ass'].name}")
        if item["source_kind"] == "video":
            print(f"    source: {item['source'].name}")
        else:
            sources = ", ".join(path.name for path in item["source"])
            print(f"    source: {sources}")
        print(f"    output: {item['output'].name}")


def burn_playlist(playlist, args):
    for index, item in enumerate(playlist, start=1):
        output_path = item["output"]
        if output_path.exists() and not args.overwrite:
            print(f"[{index:02d}] skip existing: {output_path}")
            continue
        print(f"[{index:02d}] burn: {item['folder'].name}")
        burn_ass(
            ass_path=item["ass"],
            video_path=item["burn_source"],
            output_path=output_path,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
            crf=args.crf,
            preset=args.preset,
            audio_mode=args.audio_mode,
            overwrite=args.overwrite,
            image_fps=args.image_fps,
        )


def concat_playlist(playlist, output_path, args):
    missing_outputs = [item["output"] for item in playlist if not item["output"].is_file()]
    if missing_outputs:
        names = "\n".join(str(path) for path in missing_outputs)
        raise FileNotFoundError("Cannot concat because generated output(s) are missing:\n" + names)

    output_path = output_path.resolve()
    if output_path.exists() and not args.overwrite:
        raise FileExistsError(f"Output already exists: {output_path}. Use --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    list_path = output_path.parent / f".fa_kara_ordered_{uuid.uuid4().hex}.ffconcat"
    write_concat_list(list_path, playlist)
    command = [
        args.ffmpeg,
        "-hide_banner",
        "-y" if args.overwrite else "-n",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
    ]
    if args.reencode:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                args.concat_preset,
                "-crf",
                str(args.concat_crf),
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if args.concat_audio_mode == "copy":
            command.extend(["-c:a", "copy"])
        else:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
    else:
        command.extend(["-c", "copy"])
    command.extend(["-movflags", "+faststart", str(output_path)])

    try:
        subprocess.run(command, check=True)
    finally:
        if not args.keep_list:
            try:
                list_path.unlink()
            except FileNotFoundError:
                pass


def main():
    parser = argparse.ArgumentParser(description="Burn and concatenate karaoke videos in a fixed playlist order.")
    parser.add_argument("--order-file", type=Path, default=Path("songs/playlist_order.txt"))
    parser.add_argument("--songs-dir", type=Path, default=Path("songs"))
    parser.add_argument("--output", type=Path, default=Path("merged_kara_ordered.mp4"))
    parser.add_argument("--dry-run", action="store_true", help="Only print the resolved order.")
    parser.add_argument("--burn-only", action="store_true", help="Only burn per-song videos.")
    parser.add_argument("--concat-only", action="store_true", help="Only concatenate existing per-song videos.")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--crf", type=int, default=18)
    parser.add_argument("--preset", default="medium")
    parser.add_argument("--audio-mode", choices=("copy", "aac"), default="copy")
    parser.add_argument("--image-fps", type=float, default=30)
    parser.add_argument("--output-ext", default=".mp4")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--reencode", action="store_true", help="Re-encode while concatenating.")
    parser.add_argument("--concat-crf", type=int, default=18)
    parser.add_argument("--concat-preset", default="medium")
    parser.add_argument("--concat-audio-mode", choices=("copy", "aac"), default="copy")
    parser.add_argument("--keep-list", action="store_true")
    args = parser.parse_args()

    if args.burn_only and args.concat_only:
        parser.error("--burn-only and --concat-only cannot be used together")

    songs_dir = args.songs_dir.resolve()
    if not songs_dir.is_dir():
        raise FileNotFoundError(f"Songs directory not found: {songs_dir}")

    playlist = resolve_playlist(args.order_file, songs_dir, args.output_ext)
    print_playlist(playlist)
    if args.dry_run:
        print(f"Dry run only. {len(playlist)} song(s) resolved.")
        return

    if not args.concat_only:
        burn_playlist(playlist, args)
    if not args.burn_only:
        concat_playlist(playlist, args.output, args)
        print(f"Wrote merged video to {args.output.resolve()}")


if __name__ == "__main__":
    main()
