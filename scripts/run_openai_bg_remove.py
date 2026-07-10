#!/usr/bin/env python3
"""Batch OpenAI image-background removal for the MEMI photo crops.

This wrapper loads OPENAI_API_KEY from .env, .env.openai, or openai_image.env,
then calls the bundled Codex image_gen.py CLI for each source JPEG. It keeps the
API path centralized in the curated CLI and only handles batching plus local
1080x1920 wallpaper composition.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_CLI = (
    Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    / "skills/.system/imagegen/scripts/image_gen.py"
)
DEFAULT_PROMPT = ROOT / "scripts" / "openai_bg_remove_prompt.txt"


def load_env() -> None:
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / ".env.openai", override=True)
    load_dotenv(ROOT / "openai_image.env", override=True)


def parse_size(raw: str) -> tuple[int, int]:
    try:
        w_raw, h_raw = raw.lower().split("x", 1)
        width, height = int(w_raw), int(h_raw)
    except Exception as exc:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT, for example 1080x1920") from exc
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError("size width and height must be positive")
    return width, height


def rel_or_abs(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Remove backgrounds from JPEG crops with OpenAI Image API, then resize to phone wallpaper PNGs."
    )
    parser.add_argument("--source-dir", default="JPEG", help="Folder of input .jpg files.")
    parser.add_argument(
        "--raw-output-dir",
        default="JPEG_NO_BG_OPENAI_RAW",
        help="Intermediate transparent PNGs returned by the OpenAI image edit.",
    )
    parser.add_argument(
        "--wallpaper-output-dir",
        default="JPEG_NO_BG_OPENAI",
        help="Final 1080x1920 transparent wallpaper PNG folder.",
    )
    parser.add_argument("--prompt-file", default=str(DEFAULT_PROMPT), help="Background-removal prompt file.")
    parser.add_argument("--model", default=os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5"))
    parser.add_argument("--quality", default=os.getenv("OPENAI_IMAGE_QUALITY", "high"))
    parser.add_argument("--api-size", default=os.getenv("OPENAI_IMAGE_API_SIZE", "1024x1536"))
    parser.add_argument(
        "--wallpaper-size",
        type=parse_size,
        default=parse_size(os.getenv("OPENAI_IMAGE_WALLPAPER_SIZE", "1080x1920")),
    )
    parser.add_argument("--max-wallpaper-width", type=int, default=1000)
    parser.add_argument("--max-wallpaper-height", type=int, default=1740)
    parser.add_argument("--limit", type=int, help="Process only the first N images.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing raw and wallpaper outputs.")
    parser.add_argument("--dry-run", action="store_true", help="Print Image API requests without calling the API.")
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Only compose wallpapers from existing raw OpenAI PNGs.",
    )
    parser.add_argument("--image-cli", default=str(DEFAULT_IMAGE_CLI), help="Path to bundled image_gen.py.")
    return parser


def require_inputs(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    source_dir = rel_or_abs(args.source_dir)
    raw_output_dir = rel_or_abs(args.raw_output_dir)
    wallpaper_output_dir = rel_or_abs(args.wallpaper_output_dir)
    prompt_file = rel_or_abs(args.prompt_file)
    image_cli = rel_or_abs(args.image_cli)

    if not source_dir.exists():
        raise SystemExit(f"Source folder not found: {source_dir}")
    if not prompt_file.exists():
        raise SystemExit(f"Prompt file not found: {prompt_file}")
    if not image_cli.exists():
        raise SystemExit(f"Bundled image CLI not found: {image_cli}")
    if not args.dry_run and not args.skip_api and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. Add it to .env.openai, openai_image.env, or export it.")

    raw_output_dir.mkdir(parents=True, exist_ok=True)
    wallpaper_output_dir.mkdir(parents=True, exist_ok=True)
    return source_dir, raw_output_dir, wallpaper_output_dir, prompt_file, image_cli


def compose_wallpaper(
    raw_png: Path,
    final_png: Path,
    wallpaper_size: tuple[int, int],
    max_width: int,
    max_height: int,
) -> None:
    wall_w, wall_h = wallpaper_size
    with Image.open(raw_png) as loaded:
        cutout = loaded.convert("RGBA")

    alpha = cutout.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        x0, y0, x1, y1 = bbox
        pad = 24
        cutout = cutout.crop(
            (
                max(0, x0 - pad),
                max(0, y0 - pad),
                min(cutout.width, x1 + pad),
                min(cutout.height, y1 + pad),
            )
        )
    else:
        cutout = Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    scale = min(max_width / cutout.width, max_height / cutout.height)
    new_size = (max(1, int(cutout.width * scale)), max(1, int(cutout.height * scale)))
    cutout = cutout.resize(new_size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (wall_w, wall_h), (0, 0, 0, 0))
    canvas.alpha_composite(cutout, ((wall_w - cutout.width) // 2, (wall_h - cutout.height) // 2))
    final_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(final_png, optimize=True)


def run_edit(args: argparse.Namespace, image_cli: Path, prompt_file: Path, source_jpg: Path, raw_png: Path) -> None:
    cmd = [
        sys.executable,
        str(image_cli),
        "edit",
        "--model",
        args.model,
        "--image",
        str(source_jpg),
        "--prompt-file",
        str(prompt_file),
        "--quality",
        args.quality,
        "--size",
        args.api_size,
        "--background",
        "transparent",
        "--output-format",
        "png",
        "--input-fidelity",
        "high",
        "--out",
        str(raw_png),
    ]
    if args.force:
        cmd.append("--force")
    if args.dry_run:
        cmd.append("--dry-run")

    subprocess.run(cmd, check=True, cwd=ROOT, env=os.environ.copy())


def main() -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args()
    source_dir, raw_output_dir, wallpaper_output_dir, prompt_file, image_cli = require_inputs(args)

    sources = sorted(source_dir.glob("*.jpg"))
    if args.limit is not None:
        sources = sources[: args.limit]
    if not sources:
        raise SystemExit(f"No .jpg files found in {source_dir}")

    for idx, source_jpg in enumerate(sources, start=1):
        raw_png = raw_output_dir / f"{source_jpg.stem}_openai_raw.png"
        final_png = wallpaper_output_dir / f"{source_jpg.stem}_openai_no_bg_1080x1920.png"
        print(f"[{idx}/{len(sources)}] {source_jpg.name}")

        if not args.skip_api:
            if raw_png.exists() and not args.force:
                print(f"  raw exists, skipping API: {raw_png}")
            else:
                run_edit(args, image_cli, prompt_file, source_jpg, raw_png)

        if args.dry_run:
            continue
        if not raw_png.exists():
            raise SystemExit(f"Expected raw OpenAI output missing: {raw_png}")
        if final_png.exists() and not args.force:
            print(f"  wallpaper exists, skipping: {final_png}")
            continue
        compose_wallpaper(
            raw_png,
            final_png,
            args.wallpaper_size,
            args.max_wallpaper_width,
            args.max_wallpaper_height,
        )
        print(f"  wrote {final_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
