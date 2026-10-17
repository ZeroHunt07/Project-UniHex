#!/usr/bin/env python3
"""
uniconv - a universal, extensible file converter.

Design
------
Every file format is a *node* in a directed graph. Every converter is an
*edge*. To convert A -> B we BFS for the shortest chain of converters and
run them in sequence through temporary files. That means A -> B works even
when no direct converter exists (e.g. md -> pdf becomes md -> html -> pdf).

Adding support for a new format is one call to `registry.add(...)`.

Usage
-----
    python uniconv.py photo.heic -t png
    python uniconv.py track.wav -t mp3 --bitrate 320k
    python uniconv.py report.docx -t pdf
    python uniconv.py data.csv -t xlsx -o out/
    python uniconv.py *.png -t webp -o converted/ -q 90
    python uniconv.py --list
    python uniconv.py --list-from csv
    python uniconv.py --info mystery.bin
"""

from __future__ import annotations

import argparse
import bz2
import csv as csv_mod
import gzip
import importlib.util
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict, deque
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

__version__ = "1.0.0"

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class ConversionError(Exception):
    """Raised when a conversion cannot be performed."""


class MissingDependency(ConversionError):
    """Raised when a required external library/binary is unavailable."""


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------

ALIASES: Dict[str, str] = {
    "jpeg": "jpg", "jpe": "jpg", "jfif": "jpg", "tif": "tiff",
    "htm": "html", "xhtml": "html", "yml": "yaml", "markdown": "md",
    "mdown": "md", "mkd": "md", "text": "txt", "wave": "wav",
    "mpeg": "mpg", "mp4v": "mp4", "tgz": "tar.gz", "tbz": "tar.bz2",
    "tbz2": "tar.bz2", "txz": "tar.xz", "ndjson": "jsonl",
    "aif": "aiff", "oga": "ogg", "weba": "ogg",
}

COMPOUND_EXTENSIONS = ("tar.gz", "tar.bz2", "tar.xz")


def normalize(fmt: str) -> str:
    """Lowercase, strip leading dot, resolve aliases."""
    fmt = fmt.lower().strip().lstrip(".")
    return ALIASES.get(fmt, fmt)


def detect_format(path: os.PathLike | str) -> str:
    """Infer a normalized format name from a file path."""
    name = Path(path).name.lower()
    for compound in COMPOUND_EXTENSIONS:
        if name.endswith("." + compound):
            return compound
    suffix = Path(name).suffix.lstrip(".")
    if not suffix:
        raise ConversionError(
            f"Cannot detect the format of {str(path)!r} (no file extension)."
        )
    return normalize(suffix)


# --------------------------------------------------------------------------
# The conversion graph
# --------------------------------------------------------------------------

ConverterFn = Callable[[Path, Path, Dict[str, Any]], None]
Edge = Tuple[str, str, ConverterFn]


class Registry:
    """Holds the format graph and executes conversions."""

    def __init__(self) -> None:
        self._converters: Dict[Tuple[str, str], ConverterFn] = {}
        self._out: Dict[str, Set[str]] = defaultdict(set)

    # -- registration ------------------------------------------------------

    def add(
        self,
        src: str,
        dst: str,
        fn: ConverterFn,
        *,
        reverse: Optional[ConverterFn] = None,
        overwrite: bool = True,
    ) -> None:
        src, dst = normalize(src), normalize(dst)
        if not overwrite and (src, dst) in self._converters:
            return
        self._converters[(src, dst)] = fn
        self._out[src].add(dst)
        if reverse is not None:
            self.add(dst, src, reverse, overwrite=overwrite)

    def add_pair(self, a: str, b: str, fn: ConverterFn) -> None:
        self.add(a, b, fn)
        self.add(b, a, fn)

    def has(self, src: str, dst: str) -> bool:
        return (normalize(src), normalize(dst)) in self._converters

    # -- introspection -----------------------------------------------------

    def formats(self) -> Set[str]:
        out = set(self._out)
        for _, dst in self._converters:
            out.add(dst)
        return out

    def reachable(self, start: str) -> Set[str]:
        start = normalize(start)
        seen: Set[str] = set()
        queue = deque([start])
        while queue:
            node = queue.popleft()
            for nxt in self._out.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        seen.discard(start)
        return seen

    def path(self, src: str, dst: str) -> Optional[List[Edge]]:
        """Shortest chain of converters from `src` to `dst` (BFS)."""
        src, dst = normalize(src), normalize(dst)
        if src == dst:
            return []
        seen = {src}
        queue = deque([(src, [])])
        while queue:
            node, trail = queue.popleft()
            for nxt in sorted(self._out.get(node, ())):
                if nxt in seen:
                    continue
                edge = (node, nxt, self._converters[(node, nxt)])
                if nxt == dst:
                    return trail + [edge]
                seen.add(nxt)
                queue.append((nxt, trail + [edge]))
        return None

    # -- execution ---------------------------------------------------------

    def convert(
        self,
        src: os.PathLike | str,
        dst: os.PathLike | str,
        options: Optional[Dict[str, Any]] = None,
        verbose: bool = False,
    ) -> List[str]:
        """Convert `src` to `dst`. Returns the list of formats traversed."""
        options = dict(options or {})
        src_path, dst_path = Path(src), Path(dst)
        src_fmt, dst_fmt = detect_format(src_path), detect_format(dst_path)

        dst_path.parent.mkdir(parents=True, exist_ok=True)

        if src_path.resolve() == dst_path.resolve():
            return [src_fmt]

        if src_fmt == dst_fmt:
            shutil.copyfile(src_path, dst_path)
            return [src_fmt]

        chain = self.path(src_fmt, dst_fmt)
        if chain is None:
            raise ConversionError(
                f"No conversion path from .{src_fmt} to .{dst_fmt}. "
                f"Run `--list-from {src_fmt}` to see reachable formats."
            )

        traversed = [src_fmt]
        with tempfile.TemporaryDirectory(prefix="uniconv-") as tmp:
            current = src_path
            for index, (a, b, fn) in enumerate(chain):
                is_last = index == len(chain) - 1
                out = dst_path if is_last else Path(tmp) / f"{index:02d}.{b}"
                if verbose:
                    print(f"    [{index + 1}/{len(chain)}] {a} -> {b}", file=sys.stderr)
                fn(current, out, options)
                current = out
                traversed.append(b)
        return traversed


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _has_binary(name: str) -> bool:
    return shutil.which(name) is not None


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _copy_text(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
    text = Path(src).read_text(encoding=opts.get("encoding", "utf-8"), errors="replace")
    Path(dst).write_text(text, encoding="utf-8")


def _read_text(path: Path, opts: Dict[str, Any]) -> str:
    return Path(path).read_text(encoding=opts.get("encoding", "utf-8"), errors="replace")


# ==========================================================================
# 1. Office documents via LibreOffice (broad, low-priority edges)
# ==========================================================================

OFFICE_FORMATS = [
    "docx", "doc", "odt", "rtf", "pptx", "ppt", "odp",
    "xlsx", "xls", "ods", "txt", "html", "csv",
]
OFFICE_TARGETS = ["pdf", "docx", "odt", "html", "txt", "xlsx", "csv", "pptx", "rtf"]


def register_office(reg: Registry) -> None:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return

    def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        target = detect_format(dst)
        with tempfile.TemporaryDirectory(prefix="uniconv-lo-") as tmp:
            proc = subprocess.run(
                [exe, "--headless", "--norestore", "--convert-to", target,
                 "--outdir", tmp, str(src)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                raise ConversionError(f"libreoffice failed: {proc.stderr.strip()[:300]}")
            produced = sorted(Path(tmp).iterdir())
            if not produced:
                raise ConversionError("libreoffice produced no output file")
            shutil.move(str(produced[0]), str(dst))

    for src_fmt in OFFICE_FORMATS:
        for dst_fmt in OFFICE_TARGETS:
            if normalize(src_fmt) != normalize(dst_fmt):
                reg.add(src_fmt, dst_fmt, convert)


# ==========================================================================
# 2. Audio / video via ffmpeg
# ==========================================================================

AUDIO_FORMATS = [
    "mp3", "wav", "flac", "ogg", "opus", "aac", "m4a", "wma", "aiff",
    "alac", "ac3", "au", "amr", "mka", "caf", "wv", "mp2", "ra", "ape",
]
VIDEO_FORMATS = [
    "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "mpg", "m4v",
    "3gp", "3g2", "ts", "mts", "vob", "ogv", "asf", "f4v", "mxf", "gif",
]


def _ffmpeg_convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise MissingDependency("ffmpeg not found on PATH - install it from ffmpeg.org")

    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]

    if opts.get("start") is not None:
        cmd += ["-ss", str(opts["start"])]
    if opts.get("duration") is not None:
        cmd += ["-t", str(opts["duration"])]
    if opts.get("vcodec"):
        cmd += ["-c:v", str(opts["vcodec"])]
    if opts.get("acodec"):
        cmd += ["-c:a", str(opts["acodec"])]
    if opts.get("bitrate"):
        cmd += ["-b:a", str(opts["bitrate"])]
    if opts.get("fps"):
        cmd += ["-r", str(opts["fps"])]
    if opts.get("size"):
        cmd += ["-s", str(opts["size"])]
    if opts.get("no_audio"):
        cmd += ["-an"]
    if opts.get("no_video"):
        cmd += ["-vn"]

    target = detect_format(dst)
    if target == "gif" and "fps" not in opts:
        cmd += ["-vf", "fps=12,scale=480:-1:flags=lanczos"]

    cmd.append(str(dst))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise ConversionError(f"ffmpeg failed: {proc.stderr.strip()[:400]}")


def register_media(reg: Registry) -> None:
    if not _has_binary("ffmpeg"):
        return
    formats = sorted({normalize(f) for f in AUDIO_FORMATS + VIDEO_FORMATS})
    for a in formats:
        for b in formats:
            if a != b:
                reg.add(a, b, _ffmpeg_convert)


# ==========================================================================
# 3. Raster images via Pillow
# ==========================================================================


def register_images(reg: Registry) -> None:
    if not _has_module("PIL"):
        return
    from PIL import Image

    ext_map: Dict[str, str] = {}
    for ext, fmt in Image.registered_extensions().items():
        ext_map.setdefault(normalize(ext.lstrip(".")), fmt)

    formats = sorted(ext_map)

    def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        target = detect_format(dst)
        pil_fmt = ext_map.get(target)
        if pil_fmt is None:
            raise ConversionError(f"Pillow cannot write .{target} files")

        with Image.open(src) as im:
            im.load()

            if pil_fmt in ("JPEG", "BMP") and im.mode not in ("RGB", "L", "CMYK", "1"):
                im = im.convert("RGB")
            if pil_fmt == "GIF" and im.mode not in ("P", "L"):
                im = im.convert("P", palette=Image.ADAPTIVE)
            if pil_fmt == "PDF" and im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")

            kwargs: Dict[str, Any] = {}
            if "quality" in opts and pil_fmt in ("JPEG", "WEBP", "AVIF", "HEIF"):
                kwargs["quality"] = int(opts["quality"])
            if "dpi" in opts and pil_fmt in ("JPEG", "PNG", "TIFF", "PDF"):
                kwargs["dpi"] = (int(opts["dpi"]), int(opts["dpi"]))

            im.save(dst, format=pil_fmt, **kwargs)

    for a in formats:
        for b in formats:
            if a != b:
                reg.add(a, b, convert, overwrite=False)


# ==========================================================================
# 4. Structured data (json hub + full matrix)
# ==========================================================================

DATA_FORMATS = ["json", "jsonl", "yaml", "toml", "csv", "tsv", "xml", "ini",
                "xlsx", "parquet"]


def _read_json(p: Path, o: Dict[str, Any]) -> Any:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _write_json(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    Path(p).write_text(
        json.dumps(obj, indent=o.get("indent", 2), ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _read_jsonl(p: Path, o: Dict[str, Any]) -> Any:
    return [json.loads(line) for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    rows = obj if isinstance(obj, list) else [obj]
    Path(p).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows),
        encoding="utf-8",
    )


def _read_yaml(p: Path, o: Dict[str, Any]) -> Any:
    import yaml
    return yaml.safe_load(Path(p).read_text(encoding="utf-8"))


def _write_yaml(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    import yaml
    Path(p).write_text(
        yaml.safe_dump(obj, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _read_toml(p: Path, o: Dict[str, Any]) -> Any:
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        import tomli as tomllib  # type: ignore
    return tomllib.loads(Path(p).read_text(encoding="utf-8"))


def _write_toml(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    import tomli_w
    if not isinstance(obj, dict):
        obj = {"value": obj}
    Path(p).write_text(tomli_w.dumps(obj), encoding="utf-8")


def _read_delimited(p: Path, o: Dict[str, Any], delimiter: str) -> Any:
    with open(p, newline="", encoding=o.get("encoding", "utf-8")) as fh:
        return [dict(row) for row in csv_mod.DictReader(fh, delimiter=delimiter)]


def _write_delimited(obj: Any, p: Path, o: Dict[str, Any], delimiter: str) -> None:
    rows = obj if isinstance(obj, list) else [obj]
    rows = [r if isinstance(r, dict) else {"value": r} for r in rows]
    if not rows:
        Path(p).write_text("", encoding="utf-8")
        return
    fieldnames = list(dict.fromkeys(k for row in rows for k in row))
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv_mod.DictWriter(fh, fieldnames=fieldnames, delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _stringify(v) for k, v in row.items()})


def _read_csv(p: Path, o: Dict[str, Any]) -> Any:
    return _read_delimited(p, o, o.get("delimiter", ","))


def _write_csv(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    _write_delimited(obj, p, o, o.get("delimiter", ","))


def _read_tsv(p: Path, o: Dict[str, Any]) -> Any:
    return _read_delimited(p, o, "\t")


def _write_tsv(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    _write_delimited(obj, p, o, "\t")


def _xml_to_obj(element) -> Any:
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        return text or None
    result: Dict[str, Any] = {}
    for child in children:
        value = _xml_to_obj(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


def _obj_to_xml(tag: str, value: Any):
    import xml.etree.ElementTree as ET
    element = ET.Element(str(tag))
    if isinstance(value, dict):
        for key, val in value.items():
            element.append(_obj_to_xml(str(key), val))
    elif isinstance(value, (list, tuple)):
        for item in value:
            element.append(_obj_to_xml("item", item))
    elif value is not None:
        element.text = str(value)
    return element


def _read_xml(p: Path, o: Dict[str, Any]) -> Any:
    import xml.etree.ElementTree as ET
    root = ET.parse(p).getroot()
    return {root.tag: _xml_to_obj(root)}


def _write_xml(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    import xml.etree.ElementTree as ET
    if isinstance(obj, dict) and len(obj) == 1:
        tag, value = next(iter(obj.items()))
        root = _obj_to_xml(str(tag), value)
    else:
        root = _obj_to_xml("root", obj)
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)


def _read_ini(p: Path, o: Dict[str, Any]) -> Any:
    import configparser
    parser = configparser.ConfigParser()
    parser.read(p, encoding="utf-8")
    return {section: dict(parser[section]) for section in parser.sections()}


def _write_ini(obj: Any, p: Path, o: Dict[str, Any]) -> None:
    import configparser
    parser = configparser.ConfigParser()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, dict):
                parser[key] = {k: _stringify(v) for k, v in value.items()}
            else:
                parser["DEFAULT"][key] = _stringify(value)
    with open(p, "w", encoding="utf-8") as fh:
        parser.write(fh)


def _make_data_converter(reader, writer) -> ConverterFn:
    def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        writer(reader(src, opts), dst, opts)
    return convert


def register_data(reg: Registry) -> None:
    readers: Dict[str, Callable] = {"json": _read_json, "jsonl": _read_jsonl,
                                    "csv": _read_csv, "tsv": _read_tsv,
                                    "xml": _read_xml, "ini": _read_ini}
    writers: Dict[str, Callable] = {"json": _write_json, "jsonl": _write_jsonl,
                                    "csv": _write_csv, "tsv": _write_tsv,
                                    "xml": _write_xml, "ini": _write_ini}

    if _has_module("yaml"):
        readers["yaml"], writers["yaml"] = _read_yaml, _write_yaml
    if _has_module("tomllib") or _has_module("tomli"):
        readers["toml"] = _read_toml
    if _has_module("tomli_w"):
        writers["toml"] = _write_toml

    if _has_module("pandas"):
        import pandas as pd

        def _read_xlsx(p: Path, o: Dict[str, Any]) -> Any:
            return pd.read_excel(p).to_dict(orient="records")

        def _write_xlsx(obj: Any, p: Path, o: Dict[str, Any]) -> None:
            rows = obj if isinstance(obj, list) else [obj]
            pd.DataFrame(rows).to_excel(p, index=False)

        def _read_parquet(p: Path, o: Dict[str, Any]) -> Any:
            return pd.read_parquet(p).to_dict(orient="records")

        def _write_parquet(obj: Any, p: Path, o: Dict[str, Any]) -> None:
            rows = obj if isinstance(obj, list) else [obj]
            pd.DataFrame(rows).to_parquet(p, index=False)

        if _has_module("openpyxl"):
            readers["xlsx"], writers["xlsx"] = _read_xlsx, _write_xlsx
        if _has_module("pyarrow") or _has_module("fastparquet"):
            readers["parquet"], writers["parquet"] = _read_parquet, _write_parquet

    formats = sorted(set(readers) & set(writers))
    for a in formats:
        for b in formats:
            if a != b:
                reg.add(a, b, _make_data_converter(readers[a], writers[b]))


# ==========================================================================
# 5. Documents
# ==========================================================================

PANDOC_READERS = {
    "md": "markdown", "txt": "markdown", "html": "html", "docx": "docx",
    "odt": "odt", "rtf": "rtf", "epub": "epub", "tex": "latex", "rst": "rst",
    "org": "org", "typ": "typst", "ipynb": "ipynb", "textile": "textile",
    "mediawiki": "mediawiki", "docbook": "docbook", "opml": "opml",
}
PANDOC_WRITERS = dict(PANDOC_READERS, txt="plain")


def register_documents(reg: Registry) -> None:
    if _has_binary("pandoc"):
        exe = shutil.which("pandoc")

        def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
            reader = PANDOC_READERS.get(detect_format(src))
            writer = PANDOC_WRITERS.get(detect_format(dst))
            if not reader or not writer:
                raise ConversionError("pandoc cannot handle this format pair")
            cmd = [exe, "-f", reader, "-t", writer, str(src), "-o", str(dst)]
            if opts.get("standalone"):
                cmd.insert(1, "-s")
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise ConversionError(f"pandoc failed: {proc.stderr.strip()[:300]}")

        formats = sorted(set(PANDOC_READERS) & set(PANDOC_WRITERS))
        for a in formats:
            for b in formats:
                if a != b:
                    reg.add(a, b, convert)
        return

    # ---- Native fallbacks when pandoc is unavailable ---------------------

    def md_to_html(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        text = _read_text(src, opts)
        try:
            import markdown
            body = markdown.markdown(text, extensions=["extra", "tables", "fenced_code"])
        except ImportError:
            body = _mini_markdown(text)
        Path(dst).write_text(
            "<!doctype html>\n<html><head><meta charset='utf-8'></head>\n"
            f"<body>\n{body}\n</body></html>\n",
            encoding="utf-8",
        )

    def html_to_text(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        parser = _TextExtractor()
        parser.feed(_read_text(src, opts))
        text = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts))
        Path(dst).write_text(text.strip() + "\n", encoding="utf-8")

    def text_to_html(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        import html as html_mod
        body = html_mod.escape(_read_text(src, opts))
        Path(dst).write_text(
            "<!doctype html>\n<html><head><meta charset='utf-8'></head>\n"
            f"<body><pre>{body}</pre></body></html>\n",
            encoding="utf-8",
        )

    reg.add("md", "html", md_to_html)
    reg.add("txt", "html", text_to_html)
    reg.add("html", "txt", html_to_text)
    reg.add("md", "txt", _copy_text)


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "head", "title"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "blockquote", "pre", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def _inline_markdown(text: str) -> str:
    import html as html_mod
    text = html_mod.escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _mini_markdown(text: str) -> str:
    """Very small Markdown subset renderer (fallback when `markdown` is absent)."""
    import html as html_mod
    out: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if block.startswith("```"):
            code = block.strip("`").strip("\n")
            out.append(f"<pre><code>{html_mod.escape(code)}</code></pre>")
        elif re.match(r"^#{1,6}\s", block):
            level = len(block) - len(block.lstrip("#"))
            out.append(f"<h{level}>{_inline_markdown(block[level:].strip())}</h{level}>")
        elif all(line.strip().startswith(("- ", "* ")) for line in block.splitlines()):
            items = "".join(f"<li>{_inline_markdown(l.strip()[2:])}</li>"
                            for l in block.splitlines())
            out.append(f"<ul>{items}</ul>")
        else:
            out.append(f"<p>{_inline_markdown(block.replace(chr(10), ' '))}</p>")
    return "\n".join(out)


# ==========================================================================
# 6. DOCX <-> text
# ==========================================================================


def register_docx(reg: Registry) -> None:
    if not _has_module("docx"):
        return
    import docx

    def docx_to_text(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        document = docx.Document(str(src))
        Path(dst).write_text(
            "\n".join(p.text for p in document.paragraphs), encoding="utf-8"
        )

    def text_to_docx(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        document = docx.Document()
        for line in _read_text(src, opts).splitlines():
            document.add_paragraph(line)
        document.save(str(dst))

    reg.add("docx", "txt", docx_to_text, overwrite=False)
    reg.add("txt", "docx", text_to_docx, overwrite=False)


# ==========================================================================
# 7. PDF edges
# ==========================================================================


def register_pdf(reg: Registry) -> None:
    # --- PDF -> raster (PyMuPDF) ---
    if _has_module("fitz"):
        import fitz

        def pdf_to_image(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
            document = fitz.open(str(src))
            page_index = int(opts.get("page", 0))
            if page_index >= document.page_count:
                raise ConversionError(
                    f"page {page_index} out of range (document has {document.page_count})"
                )
            page = document[page_index]
            pixmap = page.get_pixmap(dpi=int(opts.get("dpi", 150)))
            pixmap.save(str(dst))
            document.close()

        for target in ("png", "jpg", "ppm", "psd"):
            reg.add("pdf", target, pdf_to_image, overwrite=False)

    # --- PDF -> text (pypdf / PyPDF2) ---
    PdfReader = None
    if _has_module("pypdf"):
        from pypdf import PdfReader  # type: ignore
    elif _has_module("PyPDF2"):
        from PyPDF2 import PdfReader  # type: ignore

    if PdfReader is not None:
        def pdf_to_text(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
            reader = PdfReader(str(src))
            pages = [page.extract_text() or "" for page in reader.pages]
            Path(dst).write_text("\n\n".join(pages), encoding="utf-8")

        reg.add("pdf", "txt", pdf_to_text, overwrite=False)

    # --- text -> PDF (reportlab) ---
    if _has_module("reportlab"):
        def text_to_pdf(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas

            text = _read_text(src, opts)
            pdf = canvas.Canvas(str(dst), pagesize=A4)
            width, height = A4
            y = height - 50
            pdf.setFont("Helvetica", 10)
            for line in text.splitlines():
                chunks = [line[i:i + 100] for i in range(0, len(line), 100)] or [""]
                for chunk in chunks:
                    if y < 50:
                        pdf.showPage()
                        pdf.setFont("Helvetica", 10)
                        y = height - 50
                    pdf.drawString(45, y, chunk)
                    y -= 14
            pdf.save()

        reg.add("txt", "pdf", text_to_pdf, overwrite=False)

    # --- HTML -> PDF (WeasyPrint) ---
    if _has_module("weasyprint"):
        import weasyprint

        def html_to_pdf(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
            weasyprint.HTML(filename=str(src)).write_pdf(str(dst))

        reg.add("html", "pdf", html_to_pdf, overwrite=False)


# ==========================================================================
# 8. Text bridges
# ==========================================================================


def register_text_bridges(reg: Registry) -> None:
    reg.add("txt", "md", _copy_text, overwrite=False)
    reg.add("md", "txt", _copy_text, overwrite=False)
    reg.add("txt", "rst", _copy_text, overwrite=False)
    reg.add("rst", "txt", _copy_text, overwrite=False)
    reg.add("log", "txt", _copy_text, overwrite=False)
    reg.add("txt", "log", _copy_text, overwrite=False)


# ==========================================================================
# 9. Archives
# ==========================================================================

ARCHIVE_FORMATS = ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"]
ARCHIVE_MAP = {"zip": "zip", "tar": "tar", "tar.gz": "gztar",
               "tar.bz2": "bztar", "tar.xz": "xztar"}


def register_archives(reg: Registry) -> None:
    def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        target = detect_format(dst)
        with tempfile.TemporaryDirectory(prefix="uniconv-arc-") as tmp:
            work = Path(tmp)
            extract_dir = work / "extracted"
            extract_dir.mkdir()
            shutil.unpack_archive(str(src), str(extract_dir))
            created = shutil.make_archive(
                str(work / "archive"), ARCHIVE_MAP[target], str(extract_dir)
            )
            shutil.move(created, str(dst))

    for a in ARCHIVE_FORMATS:
        for b in ARCHIVE_FORMATS:
            if a != b:
                reg.add(a, b, convert)


# ==========================================================================
# 10. Single-file compressors
# ==========================================================================

COMPRESSORS = {
    "gz": (gzip.compress, gzip.decompress),
    "bz2": (bz2.compress, bz2.decompress),
    "xz": (lzma.compress, lzma.decompress),
}


def register_compression(reg: Registry) -> None:
    def convert(src: Path, dst: Path, opts: Dict[str, Any]) -> None:
        src_fmt, dst_fmt = detect_format(src), detect_format(dst)
        data = COMPRESSORS[src_fmt][1](Path(src).read_bytes())
        Path(dst).write_bytes(COMPRESSORS[dst_fmt][0](data))

    for a in COMPRESSORS:
        for b in COMPRESSORS:
            if a != b:
                reg.add(a, b, convert)


# ==========================================================================
# Registry assembly
# ==========================================================================


def build_registry() -> Registry:
    reg = Registry()
    register_office(reg)        # broadest / lowest priority first
    register_media(reg)
    register_images(reg)
    register_data(reg)
    register_documents(reg)
    register_docx(reg)
    register_pdf(reg)
    register_text_bridges(reg)
    register_archives(reg)
    register_compression(reg)
    return reg


# ==========================================================================
# CLI
# ==========================================================================


def _build_options(args: argparse.Namespace) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    for key in ("quality", "dpi", "page", "bitrate", "fps", "size",
                "vcodec", "acodec", "start", "duration", "encoding",
                "indent", "delimiter"):
        value = getattr(args, key, None)
        if value is not None:
            options[key] = value
    if getattr(args, "no_audio", False):
        options["no_audio"] = True
    if getattr(args, "no_video", False):
        options["no_video"] = True
    return options


def _print_formats(reg: Registry, start: Optional[str] = None) -> None:
    if start:
        start = normalize(start)
        reachable = sorted(reg.reachable(start))
        print(f"From .{start} -> {len(reachable)} reachable format(s):\n")
        for i in range(0, len(reachable), 8):
            print("  " + "  ".join(f"{f:<10}" for f in reachable[i:i + 8]))
        return

    formats = sorted(reg.formats())
    print(f"uniconv {__version__} - {len(formats)} known formats, "
          f"{len(reg._converters)} direct converters\n")
    for i in range(0, len(formats), 8):
        print("  " + "  ".join(f"{f:<10}" for f in formats[i:i + 8]))


def _print_info(reg: Registry, inputs: List[Path]) -> None:
    for path in inputs:
        try:
            fmt = detect_format(path)
            count = len(reg.reachable(fmt))
            size = path.stat().st_size if path.exists() else 0
            print(f"{path}: format=.{fmt}  size={size} bytes  "
                  f"targets={count}")
        except ConversionError as exc:
            print(f"{path}: {exc}")


def _plan_jobs(
    args: argparse.Namespace, inputs: List[Path], target: Optional[str]
) -> List[Tuple[Path, Path]]:
    jobs: List[Tuple[Path, Path]] = []

    if len(inputs) == 1:
        src = inputs[0]
        if args.output:
            out = Path(args.output)
            if out.is_dir() or str(args.output).endswith(os.sep):
                if not target:
                    raise ConversionError("--to is required when --output is a directory")
                out = out / f"{src.stem}.{target}"
        elif target:
            out = src.with_name(f"{src.stem}.{target}")
        else:
            raise ConversionError("specify --to FORMAT or --output PATH")
        jobs.append((src, out))
        return jobs

    if not target:
        raise ConversionError("--to is required when converting multiple files")
    outdir = Path(args.output) if args.output else Path(".")
    outdir.mkdir(parents=True, exist_ok=True)
    for src in inputs:
        jobs.append((src, outdir / f"{src.stem}.{target}"))
    return jobs


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="uniconv",
        description="Universal file converter - converts between any two formats "
                    "connected by the conversion graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[-1].strip(),
    )
    parser.add_argument("inputs", nargs="*", help="input file(s)")
    parser.add_argument("-t", "--to", help="target format (e.g. png, mp3, pdf)")
    parser.add_argument("-o", "--output", help="output file or directory")
    parser.add_argument("-q", "--quality", type=int, help="quality (JPEG/WebP/AVIF)")
    parser.add_argument("--dpi", type=int, help="raster resolution for PDF/image output")
    parser.add_argument("--page", type=int, help="page index for PDF rasterisation")
    parser.add_argument("--bitrate", help="audio bitrate, e.g. 320k")
    parser.add_argument("--fps", type=float, help="frame rate for video/GIF output")
    parser.add_argument("--size", help="video size, e.g. 1280x720")
    parser.add_argument("--vcodec", help="ffmpeg video codec")
    parser.add_argument("--acodec", help="ffmpeg audio codec")
    parser.add_argument("--start", help="start timestamp (ffmpeg -ss)")
    parser.add_argument("--duration", help="duration (ffmpeg -t)")
    parser.add_argument("--no-audio", action="store_true", help="strip audio")
    parser.add_argument("--no-video", action="store_true", help="strip video")
    parser.add_argument("--encoding", help="text encoding for text-like formats")
    parser.add_argument("--indent", type=int, help="JSON indent width")
    parser.add_argument("--delimiter", help="CSV delimiter")
    parser.add_argument("--list", action="store_true", help="list all known formats")
    parser.add_argument("--list-from", metavar="FMT",
                        help="list formats reachable from FMT")
    parser.add_argument("--info", action="store_true",
                        help="show detected format for each input")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version",
                        version=f"uniconv {__version__}")

    args = parser.parse_args(argv)
    reg = build_registry()

    if args.list or args.list_from:
        _print_formats(reg, args.list_from)
        return 0

    if not args.inputs:
        parser.error("no input files given (use --list to see supported formats)")

    inputs = [Path(p) for p in args.inputs]

    if args.info:
        _print_info(reg, inputs)
        return 0

    target = normalize(args.to) if args.to else None
    options = _build_options(args)

    try:
        jobs = _plan_jobs(args, inputs, target)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failures = 0
    for src, dst in jobs:
        if not src.exists():
            print(f"error: {src} does not exist", file=sys.stderr)
            failures += 1
            continue
        try:
            chain = reg.convert(src, dst, options, verbose=args.verbose)
            label = " -> ".join(chain)
            print(f"OK  {src}  ==>  {dst}   [{label}]")
        except (ConversionError, MissingDependency) as exc:
            print(f"ERR {src}: {exc}", file=sys.stderr)
            failures += 1
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            print(f"ERR {src}: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())