# ADA PDF Cover

Adds a branded accessibility cover page to every PDF in a folder tree, helping organizations meet ADA Title II compliance for archived public documents.

## Features

- Interactive menu with dry-run preview before committing changes
- Batch processing with live progress bar
- Configurable header and text colors (hex)
- Logo support: provide a URL, place `logo.svg` next to the script, or omit entirely
- Archives originals to `_old_pdfs/` preserving subfolder structure
- Separates encrypted PDFs to `_encrypted_pdfs/`
- Copies unreadable/error PDFs to `_failed_pdfs/`
- Replace mode to update cover pages on already-processed PDFs

## Requirements

- Python 3.9+
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

### Logo

Place a `logo.svg` file next to the script to include your organisation's logo on the cover page. Alternatively, provide a URL to an SVG in the Configure menu. If neither is available, the cover page is generated without a logo.

### Colors

Use the Configure menu to set custom header banner and body text colors via hex codes (e.g. `#043659`).

## License

See [LICENSE](LICENSE) for details.
