# ADA PDF Cover

Adds a branded accessibility cover page to every PDF in a folder tree, helping organizations meet ADA Title II compliance for archived public documents.

## Features

- Interactive menu with dry-run preview before committing changes
- Batch processing with live progress bar
- Embeds an SVG logo from the script directory (if present)
- Archives originals to `old pdfs/` preserving subfolder structure
- Separates encrypted PDFs to `encrypted_pdfs/`
- Copies unreadable/error PDFs to `failed_pdfs/`
- Replace mode to update cover pages on already-processed PDFs

## Requirements

- Python 3.10+
- Dependencies are auto-installed on first run:
  - `pypdf`
  - `reportlab`
  - `svglib`
  - `rich`
  - `requests`
  - `lxml`

## Usage

```bash
python add_accessibility_cover.py
```

1. Enter the path to the folder containing PDFs
2. Provide contact details for the cover page
3. Choose **Dry Run** to preview, then **Full Run** to apply

Place a `logo.svg` file next to the script to include your organisation's logo on the cover page.

## License

See [LICENSE](LICENSE) for details.
