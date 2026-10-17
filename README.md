# UniHex

UniHex (`uniconv`) is a command-line file converter. It connects file formats
through a directed conversion graph: formats are nodes and available conversion
operations are edges. For a requested conversion, it finds the shortest
available chain and runs each step through temporary files. This enables
multi-step conversions when there is no direct converter (for example,
Markdown to PDF may use Markdown → HTML → PDF).

The converters available on a machine depend on its installed Python packages
and system tools. Use `--list` and `--list-from` to see the actual formats and
conversion targets available in your environment.

## Requirements

- Python 3.10 or newer.
- Optional Python packages and command-line tools for additional formats and
  conversions. UniHex checks for these at startup; unavailable integrations
  are not registered.

Run the program from the repository root:

```sh
python3 main.py --help
```

## Quick start

Convert a file to a chosen format:

```sh
python3 main.py photo.png --to webp
```

This creates `photo.webp` beside the original file. Select an output path with
`--output`:

```sh
python3 main.py report.docx --to pdf --output exports/report.pdf
```

Convert multiple files into one target format:

```sh
python3 main.py *.png --to webp --output converted/
```

Inspect available formats and conversions:

```sh
python3 main.py --list
python3 main.py --list-from csv
python3 main.py --info report.docx
```

## Usage

```text
python3 main.py [OPTIONS] [INPUT ...]
```

| Option | Description |
| --- | --- |
| `-t`, `--to FORMAT` | Target format, such as `pdf`, `webp`, or `mp3`. |
| `-o`, `--output PATH` | Output file for one input, or output directory for multiple inputs. |
| `-q`, `--quality N` | Image quality for supported JPEG, WebP, AVIF, or HEIF output. |
| `--dpi N` | Resolution for PDF rasterization or supported image output. |
| `--page N` | Zero-based page index when rasterizing a PDF. |
| `--bitrate RATE` | Audio bitrate for FFmpeg, e.g. `320k`. |
| `--fps RATE` | Video/GIF frame rate for FFmpeg. |
| `--size WIDTHxHEIGHT` | Video dimensions for FFmpeg, e.g. `1280x720`. |
| `--vcodec CODEC` | Video codec for FFmpeg, e.g. `libx264`. |
| `--acodec CODEC` | Audio codec for FFmpeg, e.g. `aac`. |
| `--start TIME` | Start time for FFmpeg input, e.g. `00:00:10`. |
| `--duration TIME` | Conversion duration for FFmpeg, e.g. `00:00:30`. |
| `--no-audio` | Remove audio from FFmpeg media output. |
| `--no-video` | Remove video from FFmpeg media output. |
| `--encoding NAME` | Text encoding for text-like inputs (default: UTF-8). |
| `--indent N` | JSON indentation width (default: 2). |
| `--delimiter CHAR` | CSV delimiter (default: comma); TSV always uses tabs. |
| `--list` | List formats known to this installation. |
| `--list-from FORMAT` | List formats reachable from the given source format. |
| `--info` | Show each input's detected format, size, and reachable target count. |
| `-v`, `--verbose` | Print each step in a multi-step conversion. |
| `--version` | Print the program version. |
| `-h`, `--help` | Show command help. |

### Output path rules

- With one input and no `--output`, `--to` is required and the output is
  written beside the input using its original stem and the target extension.
- With one input, `--output` can be a filename. Its extension determines the
  output format; make it match `--to` if both are provided.
- To use `--output` as a directory for one input, the directory must already
  exist or the path must end in a path separator. `--to` is required in this
  case.
- With multiple inputs, `--to` is required. `--output` is treated as the
  output directory (default: the current directory), which is created if
  needed. Each output uses the input stem and target extension.
- Shell wildcards such as `*.png` are expanded by your shell.

Use quoted paths containing spaces:

```sh
python3 main.py "my photos/photo one.png" --to jpg --output "exports/photo one.jpg"
```

### Examples

Images:

```sh
python3 main.py image.png --to jpg --quality 88
python3 main.py page.pdf --to png --page 0 --dpi 200
```

Audio and video:

```sh
python3 main.py recording.wav --to mp3 --bitrate 192k
python3 main.py clip.mov --to mp4 --vcodec libx264 --acodec aac
python3 main.py clip.mp4 --to mp4 --start 00:00:05 --duration 00:00:20
python3 main.py clip.mp4 --to gif --fps 15 --size 640x360
```

Documents and data:

```sh
python3 main.py notes.md --to html
python3 main.py notes.md --to pdf --output notes.pdf
python3 main.py table.csv --to json
python3 main.py records.jsonl --to csv
python3 main.py workbook.xlsx --to csv
```

Archives:

```sh
python3 main.py backup.zip --to tar.gz
```

Archive conversions extract and repackage the archive contents; they do not
convert the files inside the archive.

## Conversion families

The following integrations register conversion edges when their dependencies
are available. The exact detected formats and reachable targets are
environment-dependent; consult `--list` and `--list-from FORMAT` for the
runtime's definitive list.

| Family | Formats and behavior | Dependency |
| --- | --- | --- |
| Raster images | Formats recognized by Pillow, including common PNG, JPEG, GIF, BMP, TIFF, WebP, and PDF-capable builds. Images can be converted between registered formats. | `Pillow` |
| Audio/video | Audio: MP3, WAV, FLAC, OGG, Opus, AAC, M4A, WMA, AIFF, ALAC, AC3, AU, AMR, MKA, CAF, WV, MP2, RA, and APE. Video: MP4, MKV, AVI, MOV, WebM, FLV, WMV, MPG, M4V, 3GP, 3G2, TS, MTS, VOB, OGV, ASF, F4V, MXF, and GIF. Conversions use FFmpeg. | `ffmpeg` |
| Office documents | DOC/DOCX, ODT, RTF, PPT/PPTX, ODP, XLS/XLSX, ODS, TXT, HTML, CSV, and PDF output where supported by LibreOffice. | `soffice` or `libreoffice` |
| Structured data | JSON, JSONL, CSV, TSV, XML, and INI converters; YAML and TOML where read/write dependencies are available. XLSX and Parquet data conversions require pandas plus the corresponding Excel or Parquet engine. | Python built-ins; optional `PyYAML`, `tomli`, `tomli-w`, `pandas`, `openpyxl`, `pyarrow` or `fastparquet` |
| Documents | With Pandoc: Markdown, plain text, HTML, DOCX, ODT, RTF, EPUB, LaTeX, reStructuredText, Org, Typst, Jupyter Notebook, Textile, MediaWiki, DocBook, and OPML readers/writers. Without Pandoc, native Markdown/HTML/text bridges are available, with an optional Markdown renderer. | `pandoc`; optional `markdown` |
| DOCX/text | DOCX to plain text and plain text to DOCX. | `python-docx` |
| PDF | PDF to PNG/JPG/PPM/PSD (selected page), PDF to text, text to PDF, and HTML to PDF when the matching integration is installed. | `PyMuPDF` (`fitz`), `pypdf` or `PyPDF2`, `reportlab`, `weasyprint` |
| Archives | Repackage between ZIP, TAR, TAR.GZ, TAR.BZ2, and TAR.XZ. | Python standard library |
| Single-file compression | Recompress between GZ, BZ2, and XZ streams. These are compression formats, not archive extraction/conversion. | Python standard library |

Pandoc and LibreOffice provide broad document conversions. When multiple
converters can handle the same pair, the registry's registration order
determines which converter is used. A shortest graph route is selected;
conversion through intermediate formats can lose formatting or other
format-specific information.

## Optional dependencies

Install only the integrations you need. These examples use pip for Python
packages; install system tools using your operating system's package manager.

```sh
python3 -m pip install Pillow
python3 -m pip install PyYAML tomli-w
python3 -m pip install pandas openpyxl pyarrow
python3 -m pip install python-docx
python3 -m pip install PyMuPDF pypdf reportlab weasyprint
python3 -m pip install Markdown
```

TOML reading uses Python's built-in `tomllib` on Python 3.11+, or `tomli` on
older supported Python versions. TOML writing uses `tomli-w`. Parquet support
requires either `pyarrow` or `fastparquet`; XLSX support requires `openpyxl`
along with pandas.

Install these external programs separately and ensure they are on `PATH`:

- [FFmpeg](https://ffmpeg.org/) for audio/video conversion.
- [LibreOffice](https://www.libreoffice.org/) for office document conversion.
- [Pandoc](https://pandoc.org/) for broad document-format conversion.

## How format discovery works

UniHex detects the input format from its filename extension, not by inspecting
file contents. Extensions are normalized case-insensitively and common aliases
are supported, such as `jpeg` → `jpg`, `tif` → `tiff`, `yml` → `yaml`,
`markdown` → `md`, and `tgz` → `tar.gz`. Compound archive extensions such as
`.tar.gz`, `.tar.bz2`, and `.tar.xz` are recognized.

`--list` reports the formats known to the current installation. To see all
reachable targets from a source format:

```sh
python3 main.py --list-from docx
```

If a requested target is missing, check the relevant dependency and rerun the
list command. A conversion is possible only when a directed path exists from
the detected source format to the target format.

## Extending UniHex

The converter registry is a directed graph. To add a conversion, implement a
function that accepts a source `Path`, destination `Path`, and options
dictionary, then register the edge:

```python
def example_converter(src, dst, options):
    # Read src, convert it, and write the result to dst.
    ...

registry.add("source-format", "target-format", example_converter)
```

Use `registry.add(..., reverse=...)` to register a distinct reverse
conversion, or `registry.add_pair(...)` when the same converter function is
valid in both directions. Converters should raise `ConversionError` with a
useful message when conversion cannot complete.

## Troubleshooting

- **No conversion path:** Check spelling and aliases, then run
  `--list-from SOURCE_FORMAT` and install the dependency for the missing
  integration.
- **Format not listed:** Optional dependencies and system binaries are
  detected when UniHex starts. Install the dependency, ensure external tools
  are on `PATH`, then run the command again.
- **Input format cannot be detected:** Give the input a filename extension;
  format detection is extension-based.
- **Conversion fails for a particular file:** The converter or destination
  may not support that file's specific content, even if the format pair is
  listed. Check the error printed by UniHex and the installed converter's
  capabilities.
- **Multi-file conversions:** UniHex continues processing remaining inputs
  after a file fails and returns a nonzero exit status if any conversion
  fails.
