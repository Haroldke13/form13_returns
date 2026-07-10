# OpenAI Background Removal Setup

This workspace is prepared to rerun the cropped MEMI photos through the OpenAI Image API and save transparent Android wallpaper PNGs.

## API key

Add your key to one of these files:

```bash
cp openai_image_env.example .env.openai
nano .env.openai
```

The runner also reads the existing root `.env`. It does not print the key.

## Dry run

Validate the request shape without calling the API:

```bash
.openai-image-venv/bin/python scripts/run_openai_bg_remove.py --limit 1 --dry-run
```

## Run one image

```bash
.openai-image-venv/bin/python scripts/run_openai_bg_remove.py --limit 1 --force
```

Outputs:

- raw OpenAI transparent PNG: `JPEG_NO_BG_OPENAI_RAW/`
- final Android wallpaper PNG: `JPEG_NO_BG_OPENAI/`

## Run all images

```bash
.openai-image-venv/bin/python scripts/run_openai_bg_remove.py --force
```

## Replace the current JPEG_NO_BG folder

Only do this after reviewing `JPEG_NO_BG_OPENAI/`:

```bash
.openai-image-venv/bin/python scripts/run_openai_bg_remove.py \
  --wallpaper-output-dir JPEG_NO_BG \
  --force
```

## Prompt

The edit prompt is in `scripts/openai_bg_remove_prompt.txt`.
