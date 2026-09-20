#!/usr/bin/env python3
"""
NFO Studio — build release-style .nfo files for your videos.

Same engine as the "Plex NFO Viewer" userscript (mediainfo, the four layouts,
FIGlet ASCII headers, the same settings), but standalone: no Plex, no browser.
Point it at files or folders; it shows each NFO and asks where to save it.

    python3 nfo-studio.py                     opens the window (double-click works too)
    python3 nfo-studio.py menu                same thing in the terminal
    python3 nfo-studio.py make  PATH...       build NFOs (files and/or folders)
    python3 nfo-studio.py setup               check / install what it needs (mediainfo)
    python3 nfo-studio.py update              update NFO Studio and mediainfo
    python3 nfo-studio.py config [k=v ...]    show or change settings
    python3 nfo-studio.py fonts | layouts     list the choices
    python3 nfo-studio.py preview [TEXT]      try the ASCII header

    make options:
      -r, --recursive      look inside sub-folders too
      --regenerate         also rebuild videos that already have an NFO
      --save next|DIR      don't ask: save next to each video, or into DIR
      --stdout             print the NFOs, save nothing
      --layout NAME        mediainfo | rules | hash | shaded | minimal (this run only)
      --no-preview         don't print each NFO before asking

Nothing is written without your answer (or an explicit --save).
Needs Python 3.8+ and MediaInfo. No other dependency.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

VERSION = "1.2.0"
UPDATE_URL = "https://raw.githubusercontent.com/PyroDzeus/userscripts/main/nfo-studio.py"
FIGLET_CDN = "https://cdn.jsdelivr.net/npm/figlet@1.11.4/fonts/"
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".webm"}


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "nfo-studio"


CONFIG_DIR = config_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
FONT_DIR = CONFIG_DIR / "fonts"

# Same settings (and names) as the userscript's ⚙ Settings → NFO generator.
DEFAULTS = {
    "layout": "mediainfo",
    "asciiText": "{title}",   # {title} = film / show title, {group} = release group, or any text
    "font": "ANSI Regular",
    "fontUrl": "",            # any .flf URL, overrides the list
    "spacing": "full",        # full = letters spaced as drawn, fitted = packed (figlet -k)
    "subtitle": "",           # optional line under the header
    "greetz": "",             # optional notes section
    "footer": "",             # optional closing line
    "sceneLabels": False,     # RESOLUTiON-style labels
    "lastFolder": "",         # remembered answer for "save in another folder"
}

FONTS = ["ANSI Regular", "ANSI Shadow", "ANSI Compact", "DOS Rebel", "Delta Corps Priest 1", "Bloody",
         "Calvin S", "Electronic", "Sub-Zero", "Standard", "Slant", "Small", "Big", "Doom", "Epic", "Graffiti",
         "Larry 3D", "3D-ASCII", "Colossal", "Georgia11", "Univers", "Star Wars", "Speed", "Poison", "Big Money-ne"]


def load_settings() -> dict:
    s = dict(DEFAULTS)
    try:
        stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if stored.get("v", 0) < 3 and stored.get("layout") == "rules":   # old default -> new default
            stored["layout"] = "mediainfo"
        s.update(stored)
    except (OSError, ValueError):
        pass
    s["v"] = 3
    return s


def save_settings(s: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


settings = load_settings()

# ---------------------------------------------------------------- terminal helpers

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR") and (os.name != "nt" or "WT_SESSION" in os.environ)


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else str(text)


bold = lambda t: c(t, "1")
dim = lambda t: c(t, "2")
green = lambda t: c(t, "32")
yellow = lambda t: c(t, "33")
red = lambda t: c(t, "31")


def ask(prompt, default=""):
    try:
        ans = input(prompt).strip()
    except EOFError:
        return default
    return ans or default


def yes_no(prompt, default=False):
    ans = ask(f"{prompt} {'[Y/n]' if default else '[y/N]'} ").lower()
    return default if not ans else ans.startswith(("y", "o"))   # "o" = oui


def clean_path(raw: str) -> str:
    """Paths dragged into a terminal come quoted or with escaped spaces."""
    raw = raw.strip()
    if len(raw) > 1 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    if os.name != "nt" and "\\" in raw:
        try:
            parts = shlex.split(raw)
            if len(parts) == 1:
                return parts[0]
        except ValueError:
            pass
    return raw


def http_get(url, timeout=15) -> bytes:
    """GET with fallbacks: python.org's Python on macOS often has no CA certificates installed."""
    req = urllib.request.Request(url, headers={"User-Agent": f"nfo-studio/{VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, ssl.SSLError) as e:
        if "CERTIFICATE" not in str(e).upper() and not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
        try:                                   # 1. certifi, if it happens to be installed
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        except ImportError:
            pass
        curl = shutil.which("curl")             # 2. the system's curl, which uses the system certificates
        if curl:
            out = subprocess.run([curl, "-fsSL", "--max-time", str(timeout), url], capture_output=True)
            if out.returncode == 0:
                return out.stdout
            if out.returncode == 22:
                raise urllib.error.HTTPError(url, 404, "not found", None, None)
        raise RuntimeError("secure connection failed — on macOS, run 'Install Certificates.command' "
                           "from your Python folder in Applications") from e


# ---------------------------------------------------------------- FIGlet (same as the userscript)


def parse_flf(src: str) -> dict:
    lines = src.replace("\r", "").split("\n")
    m = re.match(r"^flf2a(\S)\s+(\d+)\s+\d+\s+\d+\s+-?\d+\s+(\d+)", lines[0])
    if not m:
        raise ValueError("not a FIGlet (.flf) font")
    hard, height = m.group(1), int(m.group(2))
    i = 1 + int(m.group(3))
    chars = {}
    for code in range(32, 127):
        rows = []
        for _ in range(height):
            line = (lines[i] if i < len(lines) else "").rstrip()
            end = line[-1:] if line else ""
            while end and line.endswith(end):
                line = line[:-1]
            rows.append(list(line))
            i += 1
        w = max([0] + [len(r) for r in rows])
        chars[code] = [r + [" "] * (w - len(r)) for r in rows]
    return {"hard": hard, "height": height, "chars": chars}


def render_fig(font: dict, text: str, mode: str) -> list:
    out = [[] for _ in range(font["height"])]
    for ch in text:
        g = font["chars"].get(ord(ch)) or font["chars"].get(63) or font["chars"][32]
        if mode == "fitted" and out[0]:
            k = len(g[0])
            for r in range(font["height"]):
                L, R = out[r], g[r]
                trail = 0
                while trail < len(L) and L[len(L) - 1 - trail] == " ":
                    trail += 1
                lead = 0
                while lead < len(R) and R[lead] == " ":
                    lead += 1
                k = min(k, trail + lead)
            new = []
            for r in range(font["height"]):
                L, R = out[r], g[r]
                keep = L[:len(L) - k] if k else L[:]
                over = [(R[j] if j < len(R) else " ") if cc == " " else cc for j, cc in enumerate(L[len(L) - k:] if k else [])]
                new.append(keep + over + R[k:])
            out = new
        else:
            out = [L + g[r] for r, L in enumerate(out)]
    return ["".join(r).replace(font["hard"], " ").rstrip() for r in out]


_font_mem = {}


def font_url() -> str:
    return settings["fontUrl"].strip() or FIGLET_CDN + urllib.parse.quote(settings["font"]) + ".flf"


def embedded_font(name):
    data = FONT_DATA.get(name)
    return zlib.decompress(base64.b85decode("".join(data))).decode("utf-8") if data else None


def load_font() -> dict:
    url = font_url()
    if url in _font_mem:
        return _font_mem[url]
    if not settings["fontUrl"].strip() and settings["font"] in FONT_DATA:     # built in: works offline
        font = parse_flf(embedded_font(settings["font"]))
        _font_mem[url] = font
        return font
    cache = FONT_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", urllib.parse.unquote(url.rsplit("/", 1)[-1])) or "font.flf")
    if settings["fontUrl"].strip():
        cache = FONT_DIR / ("custom_" + hashlib.sha1(url.encode()).hexdigest()[:12] + ".flf")
    if cache.exists():
        src = cache.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            src = http_get(url).decode("utf-8", errors="replace")
        except Exception as e:
            raise RuntimeError(f"font download failed ({e})")
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(src, encoding="utf-8")
    font = parse_flf(src)
    _font_mem[url] = font
    return font


def ascii_header(text: str, width: int) -> dict:
    """Centred ASCII header lines; plain text if it doesn't fit or the font can't load."""
    text = (text or "").strip()
    if not text:
        return {"lines": [], "warn": None}
    font, warn, lines = None, None, None
    try:
        font = load_font()
    except Exception as e:
        warn = str(e)
    fits = lambda ls: all(len(l) <= width for l in ls)
    if font:
        for mode in (settings["spacing"], "fitted"):
            one = render_fig(font, text, mode)
            if fits(one):
                lines = one
                break
            words = text.split()
            if len(words) > 1:
                many = []
                for n, w in enumerate(words):
                    if n:
                        many.append("")
                    many += render_fig(font, w, mode)
                if fits(many):
                    lines = many
                    break
        if not lines:
            warn = "text too wide for this font — shown as plain text"
    if not lines:
        lines = [" ".join(text.upper())]
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    w = max(len(l) for l in lines)
    pad = " " * max(0, (width - w) // 2)
    return {"lines": [(pad + l).rstrip() for l in lines], "warn": warn}


# ---------------------------------------------------------------- mediainfo (same as plex-nfo-server.py)


def mediainfo_bin():
    for cand in (os.environ.get("MEDIAINFO"), shutil.which("mediainfo"),
                 "/opt/homebrew/bin/mediainfo", "/usr/local/bin/mediainfo", "/usr/bin/mediainfo"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def num(v, cast=float):
    try:
        return cast(str(v).split(" / ")[0])
    except (TypeError, ValueError):
        return None


def yes(v):
    return str(v).strip().lower() in ("yes", "1", "true")


def video_codec(t):
    fmt = t.get("Format", "")
    prof = (t.get("Format_Profile") or "").split("@")[0]
    name = {"MPEG Video": "MPEG-2"}.get(fmt, fmt)
    return f"{name} {prof}".strip()


def hdr_format(t):
    fmts = [x.strip() for x in (t.get("HDR_Format") or "").split(" / ")]
    profs = [x.strip() for x in (t.get("HDR_Format_Profile") or "").split(" / ")]
    compats = [x.strip() for x in (t.get("HDR_Format_Compatibility") or "").split(" / ")]
    out = []
    for i, f in enumerate(fmts):
        if not f:
            continue
        prof = profs[i] if i < len(profs) else ""
        compat = compats[i] if i < len(compats) else ""
        if f.startswith("Dolby Vision"):
            m = re.search(r"\.(\d+)", prof)
            p = str(int(m.group(1))) if m else "?"
            sub = {"HDR10": ".1", "SDR": ".2", "HLG": ".4", "Blu-ray": ".6"}.get(compat.split(" ")[0], "")
            if sub and p in ("7", "8"):
                p += sub
            out.append(f"Dolby Vision Profile {p}" + (f" ({compat} compatible)" if compat else ""))
        elif "2094" in f:
            out.append("HDR10+")
        elif "2086" in f:
            out.append("HDR10")
        else:
            out.append(f)
    trc = t.get("transfer_characteristics", "")
    if not any(x in ("HDR10", "HDR10+") or "HDR10 compatible" in x for x in out):
        if trc == "PQ":
            out.append("HDR10")
        elif trc == "HLG":
            out.append("HLG")
    return " / ".join(dict.fromkeys(out)) or "SDR"


def audio_codec(t):
    fmt, com = t.get("Format", ""), t.get("Format_Commercial_IfAny", "") or ""
    feat = t.get("Format_AdditionalFeatures", "") or ""
    atmos = "Atmos" in com or "JOC" in feat or "16-ch" in feat
    if fmt == "E-AC-3":
        return "E-AC-3 JOC" if atmos else "E-AC-3"
    if fmt in ("MLP FAT", "TrueHD") or "TrueHD" in com:
        return "TrueHD Atmos" if atmos else "TrueHD"
    if fmt == "DTS":
        if "XLL X" in feat or "DTS:X" in com:
            return "DTS:X"
        if "XLL" in feat or "Master Audio" in com:
            return "DTS-HD MA"
        if "XBR" in feat or "High Resolution" in com:
            return "DTS-HD HRA"
        return "DTS"
    if fmt == "PCM":
        return "LPCM"
    return fmt


def channels(t):
    n = num(t.get("Channels"), int)
    return {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}.get(n, f"{n}ch" if n else "")


def sub_format(t):
    f = t.get("Format", "")
    return {"UTF-8": "SRT", "ASCII": "SRT", "PGS": "PGS", "VobSub": "VobSub", "ASS": "ASS", "SSA": "SSA",
            "Timed Text": "TX3G", "WebVTT": "WebVTT"}.get(f, f)


def mediainfo_report(exe, path: Path) -> str:
    """mediainfo's usual text report, with the full path reduced to the file name (no personal folders in NFOs)."""
    out = subprocess.run([exe, str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    lines = out.stdout.replace("\r", "").rstrip().split("\n")
    return "\n".join(re.sub(r"^(Complete name\s*:\s*).*$", lambda m: m.group(1) + path.name, l) for l in lines)


def mediainfo(path: Path) -> dict:
    exe = mediainfo_bin()
    if not exe:
        raise RuntimeError("MediaInfo isn't installed — run: python3 nfo-studio.py setup")
    out = subprocess.run([exe, "--Output=JSON", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=180)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"mediainfo failed on {path.name}")
    tracks = json.loads(out.stdout).get("media", {}).get("track", [])
    by = lambda kind: [t for t in tracks if t.get("@type") == kind]
    g = (by("General") or [{}])[0]
    v = (by("Video") or [{}])[0]
    return {
        "file": path.name, "release": path.stem, "report": mediainfo_report(exe, path),
        "size": num(g.get("FileSize"), int) or path.stat().st_size,
        "duration": num(g.get("Duration")),
        "movieName": g.get("Title") or g.get("Movie"),
        "video": {
            "codec": video_codec(v) if v else None,
            "bitrate": num(v.get("BitRate")) or num(v.get("BitRate_Nominal")),
            "width": num(v.get("Width"), int), "height": num(v.get("Height"), int),
            "aspect": num(v.get("DisplayAspectRatio")), "fps": num(v.get("FrameRate")),
            "bitDepth": num(v.get("BitDepth"), int), "primaries": v.get("colour_primaries"),
            "hdr": hdr_format(v) if v else None,
        },
        "audio": [{
            "codec": audio_codec(t), "channels": channels(t), "bitrate": num(t.get("BitRate")),
            "lang": t.get("Language"), "title": t.get("Title"), "ad": t.get("ServiceKind") == "VI",
        } for t in by("Audio")],
        "subs": [{
            "format": sub_format(t), "lang": t.get("Language"), "title": t.get("Title"),
            "forced": yes(t.get("Forced")), "lines": num(t.get("ElementCount"), int),
        } for t in by("Text")],
    }


# ---------------------------------------------------------------- what the file is (no Plex: from its name)

EP_RE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,3})[ ._-]?e(\d{1,4})")
YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
ID_RES = [("IMDB", re.compile(r"[\[{(](?:imdb|imdbid)[-=](tt\d+)[\]})]", re.I)),
          ("TMDB", re.compile(r"[\[{(](?:tmdb|tmdbid)[-=](\d+)[\]})]", re.I)),
          ("TVDB", re.compile(r"[\[{(](?:tvdb|tvdbid)[-=](\d+)[\]})]", re.I))]


def pretty(name: str) -> str:
    name = re.sub(r"[\[{(](?:imdb|tmdb|tvdb)\w*[-=][^\]})]+[\]})]", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name.replace(".", " ").replace("_", " ")).strip(" -")


def describe(path: Path, mi: dict) -> dict:
    """Title, year, season/episode and database links, guessed from the file and folder names."""
    stem = path.stem
    where = " / ".join([path.parent.parent.name, path.parent.name, stem])
    links = []
    for label, rx in ID_RES:
        m = rx.search(where)
        if m:
            kind_is_show = bool(EP_RE.search(stem))
            if label == "IMDB":
                url = f"https://www.imdb.com/title/{m.group(1)}"
            elif label == "TMDB":
                url = f"https://www.themoviedb.org/{'tv' if kind_is_show else 'movie'}/{m.group(1)}"
            else:
                url = f"https://www.thetvdb.com/dereferrer/{'series' if kind_is_show else 'movie'}/{m.group(1)}"
            links.append([label, url])
    ep = EP_RE.search(stem)
    if ep:
        show = pretty(stem[:ep.start()])
        show = YEAR_RE.sub("", show).strip(" -") or pretty(path.parent.name)
        title = ""
        name = (mi.get("movieName") or "").strip()
        if name and name.lower() != stem.lower():
            title = re.sub(r"^.*?S\d+E\d+\s*[-:]\s*", "", name, flags=re.I).strip()
        return {"type": "episode", "show": show, "title": title, "season": int(ep.group(1)),
                "episode": int(ep.group(2)), "airDate": None, "links": links}
    y = YEAR_RE.search(stem)
    title = pretty(stem[:y.start()]) if y and y.start() > 0 else pretty(stem)
    return {"type": "movie", "title": title, "year": int(y.group(1)) if y else None, "airDate": None, "links": links}


# ---------------------------------------------------------------- model + layouts (same as the userscript)

SERVICES = {"AMZN": "Amazon Prime Video", "NF": "Netflix", "ATVP": "Apple TV+", "DSNP": "Disney+", "HMAX": "HBO Max",
            "MAX": "Max", "HULU": "Hulu", "PCOK": "Peacock", "PMTP": "Paramount+", "CRAV": "Crave", "CANALP": "Canal+",
            "MYCANAL": "myCANAL", "ADN": "ADN", "CR": "Crunchyroll", "IT": "iTunes", "STAN": "Stan", "MUBI": "MUBI",
            "ARTE": "ARTE", "TF1": "TF1+", "FTV": "france.tv", "SKST": "SkyShowtime", "RKTN": "Rakuten TV",
            "ROKU": "Roku", "TVNZ": "TVNZ", "BCORE": "Bravia Core", "PLAY": "Google Play"}


def source_of(name: str):
    t = re.sub(r"[._]", " ", name)
    A = re.ASCII
    uhd = re.search(r"\b(2160p|4k|uhd)\b", t, re.I | A)
    kind = None
    if re.search(r"\bremux\b", t, re.I | A):
        kind = ("UHD Blu-ray" if uhd else "Blu-ray") + " Remux"
    elif re.search(r"\b(blu-?ray|bdrip|brrip|bd)\b", t, re.I | A):
        kind = "UHD Blu-ray" if uhd else "Blu-ray"
    elif re.search(r"\bweb-?rip\b", t, re.I | A):
        kind = "WEBRip"
    elif re.search(r"\b(web-?dl|web)\b", t, re.I | A):
        kind = "WEB-DL"
    elif re.search(r"\bhdtv\b", t, re.I | A):
        kind = "HDTV"
    elif re.search(r"\b(dvd(rip)?|dvd[59])\b", t, re.I | A):
        kind = "DVD"
    svc = next((k for k in SERVICES if re.search(rf"\b{k}\b", t, A if k == "IT" else re.I | A)), None)
    return " · ".join(x for x in (kind, svc and SERVICES[svc]) if x) or None


LANGS = {"fr": "French", "en": "English", "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
         "nl": "Dutch", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "ru": "Russian", "pl": "Polish",
         "sv": "Swedish", "no": "Norwegian", "nb": "Norwegian Bokmål", "da": "Danish", "fi": "Finnish",
         "cs": "Czech", "hu": "Hungarian", "ro": "Romanian", "el": "Greek", "tr": "Turkish", "ar": "Arabic",
         "he": "Hebrew", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "uk": "Ukrainian",
         "ca": "Catalan", "eu": "Basque", "hr": "Croatian", "sr": "Serbian", "sk": "Slovak", "sl": "Slovenian",
         "bg": "Bulgarian", "is": "Icelandic", "fa": "Persian", "ta": "Tamil", "te": "Telugu", "ms": "Malay",
         "la": "Latin", "zxx": "No linguistic content", "und": "Undetermined"}


def language(code, title):
    base, _, region = str(code or "").partition("-")
    name = LANGS.get(base.lower()) or base or "Unknown"
    fr = re.search(r"\b(VFF|VFQ|VFI|VF2|VOF|VFB)\b", str(title or ""), re.I)
    tag = fr.group(1).upper() if fr else region.upper()
    return f"{name} ({tag})" if tag else name


def rate(bps):
    if not bps:
        return None
    return f"{bps / 1e6:.1f} Mb/s" if bps >= 1e6 else f"{round(bps / 1000)} kb/s"


def gib(b):
    if not b:
        return None
    return f"{b / 1024 ** 3:.2f} GiB" if b >= 1024 ** 3 else f"{b / 1024 ** 2:.1f} MiB"


def duration(sec):
    if not sec:
        return None
    s = round(sec)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h} h {m:02d} min" if h else f"{m} min {s % 60:02d} s"


def two(n):
    return f"{n:02d}" if isinstance(n, int) else str(n)


def build_model(info, v):
    vid = v.get("video") or {}
    if info["type"] == "episode":
        ep = f"S{two(info['season'])}E{two(info['episode'])}"
        title_lines = [info["show"], f"{ep} : {info['title']}" if info.get("title") else ep]
    else:
        year = f" ({info['year']})" if info.get("year") else ""
        title_lines = [f"{info['title']}{year}"]
    m = re.search(r"-([A-Za-z0-9]+)$", v["release"])
    fmt3 = lambda x: f"{x:.3f}"
    return {
        "file": v["file"], "release": v["release"], "group": m.group(1) if m else "", "report": v.get("report") or "",
        "titleLines": title_lines,
        "rows": [
            ["RELEASE SIZE", gib(v.get("size")), "NFO DATE", datetime.date.today().isoformat()],
            ["SOURCE", source_of(v["release"])],
            ["AIR DATE" if info["type"] == "episode" else "RELEASED", info.get("airDate"), "RUNTIME", duration(v.get("duration"))],
        ],
        "video": [
            ["CODEC", vid.get("codec"), "BITRATE", rate(vid.get("bitrate"))],
            ["RESOLUTION", f"{vid['width']} x {vid['height']}" if vid.get("width") else None,
             "ASPECT RATIO", fmt3(vid["aspect"]) if vid.get("aspect") else None],
            ["FRAMERATE", f"{fmt3(vid['fps'])} FPS" if vid.get("fps") else None,
             "BIT DEPTH", f"{vid['bitDepth']} bits" if vid.get("bitDepth") else None],
            ["COLOR PRIMARIES", vid.get("primaries")],
            ["HDR FORMAT", vid.get("hdr")],
        ],
        "audio": [[
            ["LANGUAGE", language(a.get("lang"), a.get("title")), "TYPE",
             "Audiodescription" if a.get("ad") or re.search(r"\b(AD|audio ?desc)", a.get("title") or "", re.I | re.ASCII)
             else "Commentary" if re.search(r"comment", a.get("title") or "", re.I) else "Normal"],
            ["CODEC", f"{a.get('codec') or ''} {a.get('channels') or ''}".strip(), "BITRATE", rate(a.get("bitrate"))],
        ] for a in v.get("audio", [])],
        "subs": [[
            ["LANGUAGE", language(t.get("lang"), t.get("title")), "TYPE",
             "Forced" if t.get("forced") or re.search(r"forc", t.get("title") or "", re.I)
             else "SDH" if re.search(r"\b(SDH|HI|CC)\b", t.get("title") or "", re.I | re.ASCII) else "Full"],
            ["FORMAT", t.get("format"), "LINES", str(t["lines"]) if t.get("lines") else None],
        ] for t in v.get("subs", [])],
        "links": info.get("links") or [],
    }


def has(v):
    return v is not None and v != ""


def center(s, w):
    l = (w - len(s)) // 2
    return (" " * max(0, l) + s).ljust(w)


def wrap(text, w):
    out, line = [], ""
    for word in str(text).split():
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= w:
            line += " " + word
        else:
            out.append(line)
            line = word
        while len(line) > w:
            out.append(line[:w])
            line = line[w:]
    if line:
        out.append(line)
    return out or [""]


def flat(rows):
    out = []
    for r in rows:
        r = list(r) + [None] * (4 - len(r))
        out += [[r[0], r[1]], [r[2], r[3]]]
    return [[l, v] for l, v in out if l and has(v)]


ACRONYMS = re.compile(r"^(HDR|NFO|TMDB|TVDB|IMDB|FPS|SDH)$", re.I)


def label(key, upper=False):
    if settings["sceneLabels"]:
        return key.upper().replace("I", "i")
    if upper:
        return key.upper()
    return " ".join(w.upper() if ACRONYMS.match(w) else (w.lower() if i else w[:1].upper() + w[1:].lower())
                    for i, w in enumerate(key.split(" ")))


def kv(pairs, width, label_w, indent, sep=" : "):
    out = []
    for l, v in pairs:
        head = " " * indent + label(l).ljust(label_w) + sep
        for i, x in enumerate(wrap(v, max(20, width - len(head)))):
            out.append((" " * len(head) if i else head) + x)
    return out


def sections(m):
    lst = [["Details", flat(m["rows"])], ["Video", flat(m["video"])]]
    for i, a in enumerate(m["audio"]):
        lst.append([f"Audio #{i + 1}" if len(m["audio"]) > 1 else "Audio", flat(a)])
    for i, t in enumerate(m["subs"]):
        lst.append([f"Subtitles #{i + 1}" if len(m["subs"]) > 1 else "Subtitles", flat(t)])
    return lst


def notes():
    return [x for x in (settings["greetz"].strip(), settings["footer"].strip()) if x]


def intro(header, w):
    out = list(header["lines"])
    if settings["subtitle"].strip():
        out += ["", center(settings["subtitle"].strip(), w)]
    if out:
        out.append("")
    return out


def layout_mediainfo(m, header):
    """mediainfo's own report under the file name, like most NFOs do it."""
    if not m["report"]:
        return layout_minimal(m, header)
    W = 80
    out = intro(header, W)
    bar = "=" * max(W, len(m["file"]))
    out += [bar, m["file"], bar] + m["report"].split("\n")
    if m["links"]:
        out += ["", "Links"] + [f"{label(l).ljust(41)}: {u}" for l, u in m["links"]]
    if notes():
        out += [""] + [x for n in notes() for x in wrap(n, W)]
    return out


def layout_minimal(m, header):
    W = 80
    out = intro(header, W)
    out += [m["release"], ""] + list(m["titleLines"])
    for title, pairs in sections(m):
        out += ["", label(title, True)] + kv(pairs, W, 20, 0)
    if m["links"]:
        out += ["", label("Links", True)] + [u for _, u in m["links"]]
    if notes():
        out += [""] + [x for n in notes() for x in wrap(n, W)]
    return out


def layout_rules(m, header):
    W = 80

    def rule(t, ch):
        x = f" {t} " if t else ""
        l = (W - len(x)) // 2
        return ch * l + x + ch * (W - l - len(x))

    out = intro(header, W)
    out += [rule(label("Release", True), "═"), ""]
    for t in m["titleLines"]:
        out += [center(x, W) for x in wrap(t, W - 4)]
    out += [center(x, W) for x in wrap(m["release"], W - 4)]
    out += ["", rule(label("Details", True), "═"), ""] + kv(flat(m["rows"]), W, 15, 3) + [""]
    out.append(rule(label("Tracks", True), "═"))
    for title, pairs in sections(m)[1:]:
        out += [rule(label(title), "─"), ""] + kv(pairs, W, 15, 3) + [""]
    if m["links"]:
        out += [rule(label("Links", True), "═"), ""] + ["   " + u for _, u in m["links"]] + [""]
    if notes():
        out += [rule(label("Notes", True), "═"), ""] + [center(x, W) for n in notes() for x in wrap(n, W - 4)] + [""]
    out.append("═" * W)
    return out


def layout_hash(m, header):
    W, IN = 80, 78
    line = lambda t: f"#{t.ljust(IN)}#"
    blank, full = line(""), "#" * W
    banner = lambda t: [full, line(center(f"[ {t} ]", IN)), full]
    out = intro(header, W)
    out += banner(label("Release", True)) + [blank]
    for t in m["titleLines"]:
        out += [line(center(x, IN)) for x in wrap(t, IN - 6)]
    out.append(blank)
    out += [line(center(x, IN)) for x in wrap(m["release"], IN - 6)]
    out.append(blank)
    for title, pairs in sections(m):
        out += banner(label(title, True)) + [blank] + [line(x) for x in kv(pairs, IN - 3, 15, 3)] + [blank]
    if m["links"]:
        out += banner(label("Links", True)) + [blank] + [line("   " + u) for _, u in m["links"]] + [blank]
    if notes():
        out += banner(label("Notes", True)) + [blank] + [line(center(x, IN)) for n in notes() for x in wrap(n, IN - 6)] + [blank]
    out.append(full)
    return out


def layout_shaded(m, header):
    W = 81
    INNER, COL1, LABEL = W - 4, 39, 16
    COL2 = INNER - COL1
    L = lambda t: label(t, True)
    row = lambda x: f"█ {x.ljust(INNER)} █"

    def head(l):
        lab = L(l)
        return f"■ {lab}{'.' * max(0, LABEL - len(lab))}: "

    def field(l, v, w):
        h = head(l)
        return [(" " * len(h) if i else h) + x for i, x in enumerate(wrap(v, w - len(h)))]

    def rows(lst):
        out = [row("")]
        for r in lst:
            l1, v1, l2, v2 = (list(r) + [None] * 4)[:4]
            a1, a2 = has(v1), bool(l2) and has(v2)
            if not a1 and not a2:
                continue
            A = field(l1, v1, COL1 if a2 else INNER) if a1 else []
            B = field(l2, v2, COL2 if a1 else INNER) if a2 else []
            if a1 and a2 and (len(A) > 1 or len(B) > 1):
                out += [row(x) for x in field(l1, v1, INNER)] + [row(x) for x in field(l2, v2, INNER)]
            elif a1 and a2:
                out.append(row(A[0].ljust(COL1) + B[0]))
            else:
                out += [row(x) for x in (A or B)]
        out.append(row(""))
        return out

    def bar(t, pos=None):
        l = {"top": "█ ▄███▓▓▓▒▒▒░░░", "end": "█ ▀███▓▓▓▒▒▒░░░"}.get(pos, "█ ████▓▓▓▒▒▒░░░")
        r = {"top": "░░░▒▒▒▓▓▓███▄ █", "end": "░░░▒▒▒▓▓▓███▀ █"}.get(pos, "░░░▒▒▒▓▓▓████ █")
        return l + center(t, W - 30) + r

    top, low = "▀" * (W - 2), "▄" * (W - 2)
    section = lambda t, body: [f"█{top}█", bar(L(t)), f"█{low}█"] + body
    out = intro(header, W)
    out += [f"▄█{'▀' * (W - 4)}█▄", bar(L("Release"), "top"), f"█{low}█", row("")]
    for t in m["titleLines"]:
        out += [row(center(x, INNER)) for x in wrap(t, INNER)]
    out += [row(center(x, INNER)) for x in wrap(m["release"], INNER)]
    out += rows(m["rows"]) + section("Video", rows(m["video"]))
    for i, a in enumerate(m["audio"]):
        out += section(f"Audio #{i + 1}" if len(m["audio"]) > 1 else "Audio", rows(a))
    for i, t in enumerate(m["subs"]):
        out += section(f"Subtitles #{i + 1}" if len(m["subs"]) > 1 else "Subtitles", rows(t))
    if m["links"]:
        out += section("Links", rows(m["links"]))
    if settings["greetz"].strip():
        out += section("Notes", [row("")] + [row(center(x, INNER)) for x in wrap(settings["greetz"].strip(), INNER)] + [row("")])
    out += [f"█{top}█", bar(settings["footer"].strip(), "end"), f"▀█{low[2:]}█▀"]
    return out


LAYOUTS = {
    "mediainfo": ("1 - MediaInfo", 80, layout_mediainfo),
    "rules": ("2 - Clean rules", 80, layout_rules),
    "hash": ("3 - Hash box", 80, layout_hash),
    "shaded": ("4 - Shaded box", 81, layout_shaded),
    "minimal": ("5 - Minimalistic", 80, layout_minimal),
}


def generate(info, v) -> dict:
    model = build_model(info, v)
    name, width, render = LAYOUTS.get(settings["layout"]) or LAYOUTS[DEFAULTS["layout"]]
    text = re.sub(r"\{group\}", lambda _: model["group"] or "NFO", settings["asciiText"], flags=re.I)
    text = re.sub(r"\{title\}", lambda _: info["show"] if info["type"] == "episode" else info["title"], text, flags=re.I)
    header = ascii_header(text, width) if text.strip() else {"lines": [], "warn": None}
    body = "\n".join(l.rstrip() for l in render(model, header))
    return {"text": body, "release": model["release"], "warn": header["warn"]}


# ---------------------------------------------------------------- make: files -> NFOs


def collect(paths, recursive):
    files, missing = [], []
    for raw in paths:
        p = Path(clean_path(raw)).expanduser()
        if p.is_file() and p.suffix.lower() in VIDEO_EXT:
            files.append(p)
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            files += sorted(x for x in it if x.is_file() and x.suffix.lower() in VIDEO_EXT
                            and not re.search(r"(?i)(^|[ ._-])sample([ ._-]|$)", x.stem))
        else:
            missing.append(raw)
    seen, out = set(), []
    for f in files:
        if f.resolve() not in seen:
            seen.add(f.resolve())
            out.append(f)
    return out, missing


def write_nfo(target: Path, text: str):
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_bytes((text.rstrip("\n") + "\n").replace("\n", "\r\n").encode("utf-8"))
    os.replace(tmp, target)


def make(args):
    if not mediainfo_bin():
        print(red("MediaInfo isn't installed."), "Run:", bold("python3 nfo-studio.py setup"))
        return 1
    if args.layout:
        if args.layout not in LAYOUTS:
            print(red(f"Unknown layout {args.layout!r}."), "Choose:", ", ".join(LAYOUTS))
            return 1
        settings["layout"] = args.layout
    files, missing = collect(args.paths, args.recursive)
    for m in missing:
        print(yellow(f"⚠ not a video file or folder: {m}"))
    if not files:
        print("No video found." + ("" if args.recursive else " Tip: -r also looks inside sub-folders."))
        return 1
    interactive = sys.stdin.isatty() and not args.stdout and not args.save
    print(dim(f"{len(files)} video(s) · layout {LAYOUTS[settings['layout']][0]} · font {settings['font']}"))
    sticky = None          # an answer ending with "!" applies to every remaining file
    regen_all = args.regenerate
    done = skipped = 0
    for n, video in enumerate(files, 1):
        print()
        print(bold(f"[{n}/{len(files)}] {video.name}"))
        existing = video.with_suffix(".nfo")
        if existing.exists() and not regen_all and not args.stdout:
            if not interactive:
                print(dim("  already has an NFO — skipped (use --regenerate)"))
                skipped += 1
                continue
            a = ask(f"  {yellow('An NFO already exists.')} Regenerate it? [y/N/a=all] ").lower()
            if a == "a":
                regen_all = True
            elif not a.startswith(("y", "o")):
                skipped += 1
                continue
        try:
            info_v = mediainfo(video)
        except Exception as e:
            print(red(f"  ✗ {e}"))
            skipped += 1
            continue
        nfo = generate(describe(video, info_v), info_v)
        if nfo["warn"]:
            print(yellow(f"  ⚠ {nfo['warn']}"))
        if args.stdout:
            print(nfo["text"])
            continue
        if not args.no_preview and not (sticky and not args.save):
            lines = nfo["text"].split("\n")
            show = lines if len(lines) <= 60 else lines[:45]
            print("\n".join(show))
            if len(lines) > 60:
                print(dim(f"  … {len(lines) - 45} more lines (answer 'f' to see all)"))
        target = None
        if args.save:
            target = video.with_suffix(".nfo") if args.save == "next" else Path(clean_path(args.save)).expanduser() / f"{nfo['release']}.nfo"
        else:
            while True:
                choice = sticky or ask(
                    f"\n  Save {bold(nfo['release'] + '.nfo')}?  "
                    f"[1] next to the video  [2] in another folder  [f] full view  [s] skip  [q] quit\n"
                    f"  {dim('(add ! to use the same answer for all remaining files, e.g. 1!)')}  > ").lower()
                if choice.endswith("!") and len(choice) > 1:
                    choice = sticky = choice[:-1]
                if choice == "f":
                    print(nfo["text"])
                    continue
                if choice == "q":
                    print(dim(f"\nStopped. {done} saved, {skipped} skipped."))
                    return 0
                if choice == "s":
                    break
                if choice == "1":
                    target = video.with_suffix(".nfo")
                    break
                if choice == "2":
                    last = settings.get("lastFolder") or ""
                    folder = sticky and last or clean_path(ask(f"  Folder (drag it here){f' [{last}]' if last else ''}: ", last))
                    if not folder:
                        continue
                    folder = Path(folder).expanduser()
                    if not folder.is_dir():
                        if yes_no(f"  {folder} doesn't exist. Create it?"):
                            folder.mkdir(parents=True, exist_ok=True)
                        else:
                            sticky = None
                            continue
                    settings["lastFolder"] = str(folder)
                    save_settings(settings)
                    target = folder / f"{nfo['release']}.nfo"
                    break
                sticky = None
                print(dim("  Please answer 1, 2, f, s or q."))
        if not target:
            skipped += 1
            continue
        if target.exists() and target != existing and not (sticky or args.save):
            if not yes_no(f"  {target} already exists. Replace it?"):
                skipped += 1
                continue
        try:
            write_nfo(target, nfo["text"])
            print(green(f"  ✓ saved {target}"))
            done += 1
        except OSError as e:
            print(red(f"  ✗ can't write {target}: {e.strerror or e}"))
            skipped += 1
    if not args.stdout:
        print(dim(f"\nDone: {done} saved, {skipped} skipped."))
    return 0


# ---------------------------------------------------------------- setup / update


def install_command():
    system = platform.system()
    if system == "Darwin":
        if shutil.which("brew"):
            return ["brew", "install", "media-info"], None
        return None, "Install Homebrew first (https://brew.sh), or the MediaInfo CLI from https://mediaarea.net/en/MediaInfo/Download/Mac_OS"
    if system == "Linux":
        for pm, cmd in (("apt-get", ["sudo", "apt-get", "install", "-y", "mediainfo"]),
                        ("dnf", ["sudo", "dnf", "install", "-y", "mediainfo"]),
                        ("pacman", ["sudo", "pacman", "-S", "--noconfirm", "mediainfo"]),
                        ("zypper", ["sudo", "zypper", "install", "-y", "mediainfo"])):
            if shutil.which(pm):
                return cmd, None
        return None, "Install the 'mediainfo' package with your distribution's package manager."
    if system == "Windows":
        if shutil.which("winget"):
            return ["winget", "install", "--id", "MediaArea.MediaInfo", "-e"], \
                "If 'mediainfo' still isn't found afterwards, get the CLI zip from https://mediaarea.net/en/MediaInfo/Download/Windows and add it to PATH (or set MEDIAINFO)."
        return None, "Download the MediaInfo CLI from https://mediaarea.net/en/MediaInfo/Download/Windows, then add it to PATH (or set MEDIAINFO)."
    return None, "Install MediaInfo from https://mediaarea.net/en/MediaInfo"


def run(cmd):
    print(dim("  $ " + " ".join(cmd)))
    return subprocess.run(cmd).returncode == 0


def setup(_args=None):
    print(bold(f"NFO Studio {VERSION}") + dim(f" · Python {platform.python_version()} · {platform.system()}"))
    ok = True
    if sys.version_info < (3, 8):
        print(red("✗ Python 3.8 or newer is needed."))
        ok = False
    exe = mediainfo_bin()
    if exe:
        ver = subprocess.run([exe, "--Version"], capture_output=True, text=True).stdout.strip().splitlines()
        print(green("✓ MediaInfo"), dim(f"{exe} · {ver[-1] if ver else ''}"))
    else:
        print(red("✗ MediaInfo is missing"), "(it reads the video files)")
        cmd, note = install_command()
        if cmd and yes_no(f"  Install it now with: {bold(' '.join(cmd))} ?", True):
            run(cmd)
            exe = mediainfo_bin()
            print(green("✓ MediaInfo installed") if exe else red("✗ still not found"))
        if note and not mediainfo_bin():
            print(yellow("  " + note))
        ok = ok and bool(mediainfo_bin())
    try:
        load_font()
        print(green("✓ Font"), dim(f"{settings['font']} (cached in {FONT_DIR})"))
    except Exception as e:
        print(yellow(f"⚠ Font {settings['font']!r}: {e} — headers fall back to plain text"))
    print(dim(f"  Settings: {CONFIG_FILE}"))
    print(green("\nReady.") if ok else red("\nNot ready yet — see above."))
    return 0 if ok else 1


def update(_args=None):
    print(bold("Checking for a newer NFO Studio…"))
    try:
        src = http_get(UPDATE_URL).decode("utf-8")
        m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
        remote = m.group(1) if m else None
    except urllib.error.HTTPError as e:
        remote, src = None, None
        print(yellow("⚠ no published version found online" if e.code == 404 else f"⚠ update server answered {e.code}"))
    except Exception as e:
        remote, src = None, None
        print(yellow(f"⚠ couldn't reach the update server: {e}"))
    as_tuple = lambda v: tuple(int(x) for x in re.findall(r"\d+", v))
    if remote and as_tuple(remote) > as_tuple(VERSION):
        me = Path(__file__).resolve()
        if yes_no(f"  Version {remote} is available (you have {VERSION}). Update {me.name}?", True):
            shutil.copy2(me, me.with_suffix(".py.bak"))
            fd, tmp = tempfile.mkstemp(dir=me.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
            os.replace(tmp, me)
            print(green(f"✓ updated to {remote}"), dim(f"(previous version kept as {me.with_suffix('.py.bak').name})"))
    elif remote:
        print(green(f"✓ NFO Studio is up to date ({VERSION})"))
    if shutil.which("brew") and mediainfo_bin():
        if yes_no("  Also update MediaInfo with Homebrew?", False):
            run(["brew", "upgrade", "media-info"])
    elif mediainfo_bin():
        print(dim("  MediaInfo updates come with your system's package updates."))
    return 0


# ---------------------------------------------------------------- config / fonts / layouts / preview

BOOL_KEYS = {"sceneLabels"}


def config(args):
    if args.pairs == ["reset"]:
        keep = settings.get("lastFolder", "")
        settings.clear()
        settings.update(DEFAULTS, lastFolder=keep)
        save_settings(settings)
        print(green("✓ settings reset"))
        return 0
    for pair in args.pairs:
        if "=" not in pair:
            print(red(f"Use key=value, got {pair!r}"))
            return 1
        k, v = pair.split("=", 1)
        if k not in DEFAULTS or k == "lastFolder":
            print(red(f"Unknown setting {k!r}."), "Settings:", ", ".join(x for x in DEFAULTS if x != "lastFolder"))
            return 1
        if k == "layout" and v not in LAYOUTS:
            print(red(f"Unknown layout {v!r}."), "Choose:", ", ".join(LAYOUTS))
            return 1
        if k == "spacing" and v not in ("full", "fitted"):
            print(red("spacing is 'full' or 'fitted'"))
            return 1
        settings[k] = v.lower() in ("1", "true", "yes", "on") if k in BOOL_KEYS else v
    if args.pairs:
        save_settings(settings)
        print(green("✓ saved"))
    width = max(len(k) for k in DEFAULTS)
    for k in DEFAULTS:
        if k == "lastFolder":
            continue
        v = settings[k]
        shown = LAYOUTS[v][0] if k == "layout" and v in LAYOUTS else (repr(v) if v == "" else v)
        print(f"  {k.ljust(width)}  {shown}")
    print(dim(f"\n  Change with: python3 nfo-studio.py config layout=hash font=\"ANSI Shadow\" footer=\"Enjoy\"\n  File: {CONFIG_FILE}"))
    return 0


def list_fonts(_args=None):
    for f in FONTS:
        mark = green(" ← current") if f == settings["font"] and not settings["fontUrl"] else ""
        print(f"  {f}{mark}")
    print(dim("\n  Any FIGlet font works: config fontUrl=https://…/MyFont.flf · browse them at https://patorjk.com/software/taag/"))
    return 0


def list_layouts(_args=None):
    for k, (name, width, _) in LAYOUTS.items():
        mark = green(" ← current") if k == settings["layout"] else ""
        print(f"  {k.ljust(8)} {name} ({width} columns){mark}")
    return 0


def preview(args):
    text = " ".join(args.text) if args.text else settings["asciiText"].replace("{title}", "Title").replace("{group}", "NFO")
    h = ascii_header(text, 80)
    print("\n".join(h["lines"]))
    if h["warn"]:
        print(yellow(f"⚠ {h['warn']}"))
    return 0



# ---------------------------------------------------------------- GUI (Tkinter, ships with Python)


def nfo_for(video: Path, mi: dict) -> dict:
    """NFO text for one video from its (cached) mediainfo."""
    return generate(describe(video, mi), mi)


def mono_font():
    return {"Darwin": ("Menlo", 11), "Windows": ("Consolas", 10)}.get(platform.system(), ("DejaVu Sans Mono", 10))


def run_gui():
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    # Drag & drop needs the small tkinterdnd2 module (Tk has none built in). It lives in NFO Studio's own
    # folder, installed on request from the window — nothing is added to your Python.
    dnd_lib = CONFIG_DIR / "lib"
    if dnd_lib.exists() and str(dnd_lib) not in sys.path:
        sys.path.insert(0, str(dnd_lib))
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
        root = TkinterDnD.Tk()
    except Exception:
        DND_FILES, root = None, tk.Tk()
    root.title(f"NFO Studio {VERSION}")
    root.geometry("1280x780")
    root.minsize(900, 560)
    MONO = mono_font()

    items = {}          # iid -> {"path": Path, "mi": dict|None, "nfo": dict|None, "error": str|None, "saved": str|None}
    jobs = queue.Queue()
    done = queue.Queue()
    include_sub = tk.BooleanVar(value=True)

    # ---------- background worker: mediainfo + rendering, never blocks the window ----------
    def worker():
        while True:
            iid = jobs.get()
            it = items.get(iid)
            if not it:
                continue
            try:
                if it["mi"] is None:
                    it["mi"] = mediainfo(it["path"])
                it["nfo"], it["error"] = nfo_for(it["path"], it["mi"]), None
            except Exception as e:
                it["nfo"], it["error"] = None, str(e)
            done.put(iid)

    threading.Thread(target=worker, daemon=True).start()

    def poll():
        changed = False
        while not done.empty():
            iid = done.get()
            if tree.exists(iid):
                refresh_row(iid)
                changed = True
        if changed:
            show_selected()
            update_buttons()
        root.after(120, poll)

    # ---------- layout ----------
    top = ttk.Frame(root, padding=(10, 8))
    top.pack(fill="x")
    ttk.Button(top, text="＋ Add videos…", command=lambda: add_files()).pack(side="left")
    ttk.Button(top, text="＋ Add a folder…", command=lambda: add_folder()).pack(side="left", padx=(6, 0))
    ttk.Checkbutton(top, text="include sub-folders", variable=include_sub).pack(side="left", padx=(6, 18))
    ttk.Button(top, text="Remove", command=lambda: remove_selected()).pack(side="left")
    ttk.Button(top, text="Clear", command=lambda: clear_all()).pack(side="left", padx=(6, 0))

    panes = ttk.PanedWindow(root, orient="horizontal")
    panes.pack(fill="both", expand=True, padx=10)

    left = ttk.Frame(panes)
    tree = ttk.Treeview(left, columns=("status",), selectmode="extended")
    tree.heading("#0", text="Video", anchor="w")
    tree.heading("status", text="Status", anchor="w")
    tree.column("#0", width=380, stretch=True)
    tree.column("status", width=110, stretch=False)
    tsb = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=tsb.set)
    tree.pack(side="left", fill="both", expand=True)
    tsb.pack(side="right", fill="y")
    tree.tag_configure("new", foreground="#b8860b")
    tree.tag_configure("has", foreground="#2e8b57")
    tree.tag_configure("saved", foreground="#2e8b57")
    tree.tag_configure("error", foreground="#c0392b")
    panes.add(left, weight=2)

    right = ttk.Frame(panes)
    opts = ttk.Frame(right)
    opts.pack(fill="x", pady=(0, 4))
    layout_names = {v[0]: k for k, v in LAYOUTS.items()}
    layout_var = tk.StringVar(value=LAYOUTS[settings["layout"]][0])
    ttk.Label(opts, text="Layout").pack(side="left")
    layout_box = ttk.Combobox(opts, textvariable=layout_var, values=list(layout_names), width=17, state="readonly")
    layout_box.pack(side="left", padx=(4, 14))
    font_var = tk.StringVar(value=settings["font"])
    ttk.Label(opts, text="Font").pack(side="left")
    font_box = ttk.Combobox(opts, textvariable=font_var, values=FONTS, width=22, state="readonly")
    font_box.pack(side="left", padx=(4, 14))
    ttk.Button(opts, text="⚙ More settings…", command=lambda: open_settings()).pack(side="left")
    note = ttk.Label(right, text="", foreground="#8a6d00")
    note.pack(fill="x", pady=(0, 4))
    txt_frame = ttk.Frame(right)
    txt_frame.pack(fill="both", expand=True)
    preview = tk.Text(txt_frame, wrap="none", font=MONO, bg="#0b0b0b", fg="#d8d8d8", insertbackground="#d8d8d8",
                      relief="flat", padx=14, pady=12, undo=False)
    vsb = ttk.Scrollbar(txt_frame, orient="vertical", command=preview.yview)
    hsb = ttk.Scrollbar(txt_frame, orient="horizontal", command=preview.xview)
    preview.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    preview.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    txt_frame.rowconfigure(0, weight=1)
    txt_frame.columnconfigure(0, weight=1)
    panes.add(right, weight=3)

    bottom = ttk.Frame(root, padding=(10, 8))
    bottom.pack(fill="x")
    btn_next = ttk.Button(bottom, text="💾 Save next to the video", command=lambda: save_selected("next"))
    btn_next.pack(side="left")
    btn_dir = ttk.Button(bottom, text="📁 Save to a folder…", command=lambda: save_selected("dir"))
    btn_dir.pack(side="left", padx=(6, 0))
    btn_copy = ttk.Button(bottom, text="Copy", command=lambda: copy_selected())
    btn_copy.pack(side="left", padx=(6, 0))
    ttk.Label(bottom, text="Tip: select several videos (⇧ / ⌘ / Ctrl) to save them all at once.",
              foreground="#888").pack(side="left", padx=(14, 0))

    status = ttk.Frame(root, padding=(10, 0, 10, 8))
    status.pack(fill="x")
    status_lbl = ttk.Label(status, text="Add videos or a folder to start.", foreground="#666")
    status_lbl.pack(side="left")
    ttk.Button(status, text="Check for updates", command=lambda: check_updates()).pack(side="right")
    if not DND_FILES:
        ttk.Button(status, text="Enable drag & drop…", command=lambda: enable_dnd()).pack(side="right", padx=(0, 8))
    mi_btn = ttk.Button(status, text="Install MediaInfo…", command=lambda: install_mediainfo())
    mi_lbl = ttk.Label(status, text="")
    mi_lbl.pack(side="right", padx=(0, 10))

    def refresh_mediainfo_status():
        exe = mediainfo_bin()
        mi_lbl.configure(text="MediaInfo ✓" if exe else "MediaInfo missing — needed to read videos",
                         foreground="#2e8b57" if exe else "#c0392b")
        if exe:
            mi_btn.pack_forget()
        else:
            mi_btn.pack(side="right", padx=(0, 8), before=mi_lbl)

    # ---------- list ----------
    def row_state(it):
        if it["error"]:
            return "error", "⚠ error"
        if it["saved"]:
            return "saved", "✓ saved"
        if it["nfo"] is None:
            return "", "reading…"
        if it["path"].with_suffix(".nfo").exists():
            return "has", "has an NFO"
        return "new", "new"

    def refresh_row(iid):
        tag, label_ = row_state(items[iid])
        tree.item(iid, values=(label_,), tags=(tag,))

    def add_paths(paths):
        if not mediainfo_bin():
            refresh_mediainfo_status()
            if not messagebox.askyesno("MediaInfo is missing",
                                       "NFO Studio needs MediaInfo to read videos.\n\nInstall it now?"):
                return
            install_mediainfo()
            return
        files, _ = collect([str(p) for p in paths], include_sub.get())
        known = {it["path"].resolve() for it in items.values()}
        added = 0
        for f in files:
            if f.resolve() in known:
                continue
            iid = tree.insert("", "end", text=f.name, values=("reading…",))
            items[iid] = {"path": f, "mi": None, "nfo": None, "error": None, "saved": None}
            jobs.put(iid)
            added += 1
        status_lbl.configure(text=f"{len(items)} video(s) in the list" + (f" · {added} added" if added else " · no new video found"))
        if added and not tree.selection():
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)

    def add_files():
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT))
        paths = filedialog.askopenfilenames(title="Choose videos", filetypes=[("Videos", exts), ("All files", "*")])
        if paths:
            add_paths(paths)

    def add_folder():
        d = filedialog.askdirectory(title="Choose a folder with videos")
        if d:
            add_paths([d])

    def remove_selected():
        for iid in tree.selection():
            tree.delete(iid)
            items.pop(iid, None)
        show_selected()
        update_buttons()

    def clear_all():
        for iid in list(items):
            tree.delete(iid)
        items.clear()
        show_selected()
        update_buttons()

    # ---------- preview ----------
    def show_selected(_=None):
        sel = tree.selection()
        preview.configure(state="normal")
        preview.delete("1.0", "end")
        note.configure(text="")
        if not sel:
            preview.insert("1.0", "\n  Drag videos or folders here, or use ＋ Add videos… / ＋ Add a folder…" if DND_FILES and not items
                           else "\n  Select a video to see its NFO." if items
                           else "\n  Use ＋ Add videos… or ＋ Add a folder… to start.")
        else:
            it = items[sel[0]]
            if it["error"]:
                preview.insert("1.0", f"\n  ⚠ {it['error']}")
            elif it["nfo"] is None:
                preview.insert("1.0", f"\n  Reading {it['path'].name} with MediaInfo…")
            else:
                preview.insert("1.0", it["nfo"]["text"])
                bits = []
                if it["nfo"].get("warn"):
                    bits.append(f"⚠ {it['nfo']['warn']}")
                if it["path"].with_suffix(".nfo").exists():
                    bits.append("This video already has an NFO: saving next to it will ask before replacing it.")
                if len(sel) > 1:
                    bits.append(f"{len(sel)} videos selected — the buttons below apply to all of them.")
                note.configure(text="   ".join(bits))
        preview.configure(state="disabled")

    def update_buttons():
        ready = [iid for iid in tree.selection() if items[iid]["nfo"]]
        state = "normal" if ready else "disabled"
        for b in (btn_next, btn_dir):
            b.configure(state=state)
        btn_copy.configure(state="normal" if len(ready) == 1 else "disabled")

    tree.bind("<<TreeviewSelect>>", lambda e: (show_selected(), update_buttons()))

    # ---------- drag & drop from the Finder / Explorer ----------
    def on_drop(event):
        paths = [p for p in root.tk.splitlist(event.data) if p]
        if paths:
            add_paths(paths)
        return getattr(event, "action", "copy")

    if DND_FILES:
        for w in (root, tree, preview):
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>", on_drop)

    def enable_dnd():
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade",
               "--target", str(dnd_lib), "tkinterdnd2"]
        if not messagebox.askyesno("Enable drag & drop",
                                   "Dragging files onto the window needs a small free module (tkinterdnd2, about 1 MB).\n\n"
                                   f"It will be installed in NFO Studio's own folder:\n{dnd_lib}\n\nContinue?"):
            return
        status_lbl.configure(text="Installing drag & drop support…")

        def go():
            try:
                r = subprocess.run(cmd, capture_output=True, text=True)
                ok, err = r.returncode == 0, (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
            except OSError as e:
                ok, err = False, [str(e)]
            root.after(0, lambda: done_install(ok, err[0]))

        def done_install(ok, err):
            if not ok:
                hint = " Run 'python3 -m ensurepip' once, then try again." if "No module named pip" in err else ""
                messagebox.showerror("Drag & drop", f"The install didn't work:\n{err}{hint}")
                status_lbl.configure(text="Drag & drop not enabled")
                return
            if messagebox.askyesno("Drag & drop", "Installed. Restart NFO Studio now to use it?"):
                root.destroy()
                os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve())])
                return
            status_lbl.configure(text="Drag & drop will work next time NFO Studio starts")
        threading.Thread(target=go, daemon=True).start()
    root.bind_all("<Control-a>", lambda e: tree.selection_set(tree.get_children()) if e.widget is tree else None)
    root.bind_all("<Command-a>", lambda e: tree.selection_set(tree.get_children()) if e.widget is tree else None)

    def rerender_all():
        for iid, it in items.items():
            if it["mi"] is not None:
                it["nfo"] = None
                refresh_row(iid)
                jobs.put(iid)
        show_selected()

    def on_layout(_=None):
        settings["layout"] = layout_names[layout_var.get()]
        save_settings(settings)
        rerender_all()

    def on_font(_=None):
        settings["font"], settings["fontUrl"] = font_var.get(), ""
        save_settings(settings)
        rerender_all()

    layout_box.bind("<<ComboboxSelected>>", on_layout)
    font_box.bind("<<ComboboxSelected>>", on_font)

    # ---------- saving: always an explicit click, never a silent overwrite ----------
    def save_selected(where):
        ready = [iid for iid in tree.selection() if items[iid]["nfo"]]
        if not ready:
            return
        folder = None
        if where == "dir":
            d = filedialog.askdirectory(title="Save the NFO(s) in…", initialdir=settings.get("lastFolder") or None)
            if not d:
                return
            folder = Path(d)
            settings["lastFolder"] = d
            save_settings(settings)
        plan = []
        for iid in ready:
            it = items[iid]
            target = it["path"].with_suffix(".nfo") if folder is None else folder / f"{it['nfo']['release']}.nfo"
            plan.append((iid, target))
        clashes = [t for _, t in plan if t.exists()]
        replace = False
        if clashes:
            names = "\n".join(f"• {t.name}" for t in clashes[:8]) + (f"\n… and {len(clashes) - 8} more" if len(clashes) > 8 else "")
            if len(plan) == 1:
                if not messagebox.askyesno("Replace the NFO?", f"{clashes[0].name} already exists.\n\nReplace it?", icon="warning"):
                    return
                replace = True
            else:
                ans = messagebox.askyesnocancel(
                    "Some NFOs already exist",
                    f"{len(clashes)} of the {len(plan)} NFOs already exist:\n\n{names}\n\n"
                    "Yes = replace them · No = keep them and save only the others · Cancel = do nothing", icon="warning")
                if ans is None:
                    return
                replace = ans
        saved = failed = 0
        for iid, target in plan:
            if target.exists() and not replace:
                continue
            try:
                write_nfo(target, items[iid]["nfo"]["text"])
                items[iid]["saved"] = str(target)
                saved += 1
            except OSError as e:
                items[iid]["error"] = f"can't write {target}: {e.strerror or e}"
                failed += 1
            refresh_row(iid)
        show_selected()
        where_txt = "next to the videos" if folder is None else f"in {folder}"
        status_lbl.configure(text=f"✓ {saved} NFO(s) saved {where_txt}" + (f" · {failed} failed" if failed else ""))

    def copy_selected():
        sel = [iid for iid in tree.selection() if items[iid]["nfo"]]
        if len(sel) == 1:
            root.clipboard_clear()
            root.clipboard_append(items[sel[0]]["nfo"]["text"])
            status_lbl.configure(text="✓ NFO copied to the clipboard")

    # ---------- settings window ----------
    def open_settings():
        win = tk.Toplevel(root)
        win.title("Settings")
        win.transient(root)
        win.resizable(True, True)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        fields = {}
        rows = [("ASCII text", "asciiText", "{title} = film / show title · {group} = release group · empty = no header"),
                ("Custom font URL", "fontUrl", "optional — any FIGlet .flf file, replaces the font list"),
                ("Line under it", "subtitle", "optional"),
                ("Notes / greetz", "greetz", "optional — adds a Notes section"),
                ("Footer", "footer", "optional — closing line")]
        for r, (lab, key, hint) in enumerate(rows):
            ttk.Label(frm, text=lab).grid(row=r * 2, column=0, sticky="w", pady=(6, 0))
            v = tk.StringVar(value=settings[key])
            ttk.Entry(frm, textvariable=v, width=56).grid(row=r * 2, column=1, sticky="ew", pady=(6, 0))
            ttk.Label(frm, text=hint, foreground="#888").grid(row=r * 2 + 1, column=1, sticky="w")
            fields[key] = v
        base = len(rows) * 2
        ttk.Label(frm, text="Letter spacing").grid(row=base, column=0, sticky="w", pady=(8, 0))
        spacing = tk.StringVar(value=settings["spacing"])
        sp = ttk.Frame(frm)
        sp.grid(row=base, column=1, sticky="w", pady=(8, 0))
        ttk.Radiobutton(sp, text="Spaced (as drawn)", value="full", variable=spacing).pack(side="left")
        ttk.Radiobutton(sp, text="Packed", value="fitted", variable=spacing).pack(side="left", padx=(10, 0))
        scene = tk.BooleanVar(value=bool(settings["sceneLabels"]))
        ttk.Checkbutton(frm, text="Scene-style labels (RESOLUTiON, AUDiO…)", variable=scene).grid(
            row=base + 1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frm, text="Header preview").grid(row=base + 2, column=0, sticky="nw", pady=(10, 0))
        pv = tk.Text(frm, height=9, width=84, wrap="none", font=(MONO[0], max(7, MONO[1] - 2)), bg="#0b0b0b",
                     fg="#d8d8d8", relief="flat", padx=8, pady=6)
        pv.grid(row=base + 2, column=1, sticky="nsew", pady=(10, 0))
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(base + 2, weight=1)
        pending = {"id": None}

        def current():
            s = dict(settings)
            s.update({k: v.get() for k, v in fields.items()}, spacing=spacing.get(), sceneLabels=scene.get())
            return s

        def refresh_preview(*_):
            if pending["id"]:
                win.after_cancel(pending["id"])

            def go():
                saved_settings = dict(settings)
                settings.update(current())
                try:
                    text = settings["asciiText"].replace("{title}", "Title").replace("{group}", "NFO")
                    h = ascii_header(text, 80) if text.strip() else {"lines": ["(no header)"], "warn": None}
                finally:
                    settings.clear()
                    settings.update(saved_settings)
                lines = h["lines"] + ([""] + [f"⚠ {h['warn']}"] if h["warn"] else [])
                if settings["subtitle"] or fields["subtitle"].get():
                    lines += ["", fields["subtitle"].get().strip().center(80).rstrip()]
                pv.configure(state="normal")
                pv.delete("1.0", "end")
                pv.insert("1.0", "\n".join(lines))
                pv.configure(state="disabled")
            pending["id"] = win.after(300, go)

        for v in list(fields.values()) + [spacing, scene]:
            v.trace_add("write", refresh_preview)
        refresh_preview()

        btns = ttk.Frame(frm)
        btns.grid(row=base + 3, column=0, columnspan=2, sticky="e", pady=(12, 0))

        def reset():
            for k, v in fields.items():
                v.set(DEFAULTS[k])
            spacing.set(DEFAULTS["spacing"])
            scene.set(DEFAULTS["sceneLabels"])

        def ok():
            settings.update(current())
            save_settings(settings)
            win.destroy()
            rerender_all()

        ttk.Button(btns, text="Reset", command=reset).pack(side="left")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=(6, 0))
        ttk.Button(btns, text="Save", command=ok).pack(side="left", padx=(6, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        win.grab_set()

    # ---------- tools ----------
    def install_mediainfo():
        cmd, hint = install_command()
        if not cmd or (platform.system() == "Linux"):
            text = (f"Run this in a terminal:\n\n{' '.join(cmd)}" if cmd else "") + (f"\n\n{hint}" if hint else "")
            messagebox.showinfo("Install MediaInfo", text.strip() or "Install MediaInfo from https://mediaarea.net")
            return
        if not messagebox.askyesno("Install MediaInfo", f"NFO Studio will run:\n\n{' '.join(cmd)}\n\nContinue?"):
            return
        status_lbl.configure(text="Installing MediaInfo… (this can take a minute)")

        def go():
            try:
                ok = subprocess.run(cmd, capture_output=True, text=True).returncode == 0
            except OSError:
                ok = False
            root.after(0, lambda: (refresh_mediainfo_status(), status_lbl.configure(
                text="✓ MediaInfo installed" if mediainfo_bin() else "✗ MediaInfo install failed" + (f" — {hint}" if hint else ""))))
            _ = ok
        threading.Thread(target=go, daemon=True).start()

    def check_updates():
        status_lbl.configure(text="Checking for updates…")

        def go():
            try:
                src = http_get(UPDATE_URL).decode("utf-8")
                m = re.search(r'^VERSION = "([^"]+)"', src, re.M)
                res = (m.group(1) if m else None, src, None)
            except urllib.error.HTTPError as e:
                res = (None, None, "No published version found online yet." if e.code == 404 else f"Update server answered {e.code}.")
            except Exception as e:
                res = (None, None, f"Couldn't reach the update server: {e}")
            root.after(0, lambda: finish(*res))

        def finish(remote, src, err):
            as_tuple = lambda v: tuple(int(x) for x in re.findall(r"\d+", v))
            if err:
                status_lbl.configure(text=err)
            elif remote and as_tuple(remote) > as_tuple(VERSION):
                if messagebox.askyesno("Update available", f"NFO Studio {remote} is available (you have {VERSION}).\n\nUpdate now?"):
                    me = Path(__file__).resolve()
                    shutil.copy2(me, me.with_suffix(".py.bak"))
                    fd, tmp = tempfile.mkstemp(dir=me.parent, suffix=".tmp")
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(src)
                    os.replace(tmp, me)
                    messagebox.showinfo("Updated", f"Updated to {remote}. Restart NFO Studio to use it.")
                status_lbl.configure(text=f"Version {remote} available")
            else:
                status_lbl.configure(text=f"✓ NFO Studio is up to date ({VERSION})")
        threading.Thread(target=go, daemon=True).start()

    refresh_mediainfo_status()
    show_selected()
    update_buttons()
    root.after(120, poll)
    root._nfo = {"add_paths": add_paths, "items": items, "tree": tree, "save": save_selected,
                 "on_drop": on_drop, "enable_dnd": enable_dnd, "dnd": bool(DND_FILES),
                 "preview": preview, "open_settings": open_settings, "layout": layout_var, "on_layout": on_layout}
    root.mainloop()
    return 0


def gui_available() -> bool:
    try:
        import tkinter
        del tkinter
    except ImportError:
        return False
    return platform.system() in ("Darwin", "Windows") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


# ---------------------------------------------------------------- menu + CLI


def menu():
    while True:
        print()
        print(bold(f"NFO Studio {VERSION}") + dim(f" · layout {LAYOUTS[settings['layout']][0]} · font {settings['font']}"))
        print("  [1] Make NFOs (files or folders)\n  [2] Settings\n  [3] Try the ASCII header\n"
              "  [4] Check / install tools\n  [5] Update\n  [q] Quit")
        choice = ask("> ").lower()
        if choice == "1":
            raw = ask("Drag files or folders here, then Enter: ")
            if not raw:
                continue
            try:
                paths = shlex.split(raw, posix=os.name != "nt")
            except ValueError:
                paths = [raw]
            rec = yes_no("Also look inside sub-folders?", False)
            make(argparse.Namespace(paths=paths, recursive=rec, regenerate=False, save=None, stdout=False,
                                    layout=None, no_preview=False))
        elif choice == "2":
            config(argparse.Namespace(pairs=[]))
            raw = ask("key=value to change (Enter to go back): ")
            if raw:
                try:
                    config(argparse.Namespace(pairs=shlex.split(raw)))
                except ValueError:
                    print(red("Couldn't read that — use quotes around values with spaces."))
        elif choice == "3":
            preview(argparse.Namespace(text=ask("Text (Enter = current setting): ").split()))
        elif choice == "4":
            setup()
        elif choice == "5":
            update()
        elif choice in ("q", "quit", "exit"):
            return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv == ["gui"]:
        if gui_available():
            return run_gui()
        if argv:
            print("The window needs Tkinter (macOS Homebrew Python: brew install python-tk). Falling back to the terminal menu.")
        return menu() if sys.stdin.isatty() else (print(__doc__) or 0)
    if argv == ["menu"]:
        return menu()
    commands = {"make", "setup", "update", "config", "fonts", "layouts", "preview"}
    if argv[0] not in commands and argv[0] not in ("-h", "--help", "--version"):
        argv = ["make"] + argv                  # nfo-studio.py Film.mkv  ==  nfo-studio.py make Film.mkv
    p = argparse.ArgumentParser(prog="nfo-studio.py", description="Build release-style .nfo files for your videos.")
    p.add_argument("--version", action="version", version=f"NFO Studio {VERSION}")
    sub = p.add_subparsers(dest="cmd")
    mk = sub.add_parser("make", help="build NFOs for files and/or folders")
    mk.add_argument("paths", nargs="+")
    mk.add_argument("-r", "--recursive", action="store_true", help="look inside sub-folders too")
    mk.add_argument("--regenerate", action="store_true", help="also rebuild videos that already have an NFO")
    mk.add_argument("--save", metavar="next|DIR", help="don't ask: save next to each video, or into DIR")
    mk.add_argument("--stdout", action="store_true", help="print the NFOs, save nothing")
    mk.add_argument("--layout", help="mediainfo | rules | hash | shaded | minimal (this run only)")
    mk.add_argument("--no-preview", action="store_true", help="don't print each NFO before asking")
    sub.add_parser("setup", help="check / install what NFO Studio needs")
    sub.add_parser("update", help="update NFO Studio (and MediaInfo with Homebrew)")
    cf = sub.add_parser("config", help="show or change settings (key=value …, or 'reset')")
    cf.add_argument("pairs", nargs="*")
    sub.add_parser("fonts", help="list the FIGlet fonts")
    sub.add_parser("layouts", help="list the layouts")
    pv = sub.add_parser("preview", help="try the ASCII header")
    pv.add_argument("text", nargs="*")
    args = p.parse_args(argv)
    handlers = {"make": make, "setup": setup, "update": update, "config": config,
                "fonts": list_fonts, "layouts": list_layouts, "preview": preview}
    return handlers[args.cmd](args) if args.cmd else (p.print_help() or 0)


# FIGlet fonts from the figlet.js collection (npm 'figlet' 1.11.4), trimmed to printable ASCII,
# zlib-compressed and base85-encoded so NFO Studio works offline. Decoded by embedded_font().
FONT_DATA = {
    'ANSI Regular': (
        "c-rk)J8r`;4BdT-H)Ji)v_n^4rAwwBB149pAXn+$qvRyD>(~~567?z20*Vb=QY2EO=*Q3L<Mi@=z}s{kU-5Lr^KuOb;4pfg<8Z%S"
        "ryE)W5WV>^4&<5!A9J?%x=P+?FdOSHY|cb3KliqL?tXo45<HfejOHKHfi~-YJ1;lcz^+Jy6vIt#+X#dT6b!nPfcQvA0iqjGAJ?8b"
        "8xgG<N4-<lTU46I)0m*N=wWZm_cOe#bEoFEFj#)x|3B~R*jq{mh{s}6)c!d=!<rD`th2hRtV%747*S|Vnh9`Q;b%c#RAz`X<g^Xz"
        "QphE3Loq0oo4F;Xq7)|R-+4aE6u=<PP%if26*#iwO2=3>0a1l<!YHGR$-F{S^lOliq6n(sX;et4hgm+WAyZX)H%mC;h#@lfvI9Av"
        "EklDq95TvneSHbsPcPS5QKBwjKp3MSh$vQC%+D8mC_dyC!`ORX+)kKO5=$Z@8PMxtoHNg@bsZJ;9%>zo-wRZ^nM~wIb7xw^R4^!Y"
        "FbJZccFv{Dg;#zp@HKdJ>xv*MoRTe-nHFl<Qnof6pK1$phH62c2-`qwh)6r!rq~s=Kx=PeW0sRJw_}-tvLVtgaAS#~+GwxHlQhE~"
        "hp<aQ!zHj%p!hxa`FB%_SXn7rP)T;97@upo?Yv7N8?CC4t_%goryZ$=7#6;WI8^)_LlV;q>0*&Ezru~=Yq8X(BIX+xFJ2)`JF!%i"
        "kCWQUGHQP1DejmQp<$7?+(yc7HckhvEQRSc(w-dk<ftb{Jvr*hQBRI~a@3Qfo*eb$s3%ANJvm~reoprr@CV9i"
    ),
    'ANSI Shadow': (
        "c-rk*J967F5bbq}Epz}o@g%LTlEzgIp~8hrb4C(pJi~LO&rx)eNF+t<=Pdw&q$dR|jto#FxOn@pyLf#4{rc_Ko_^jw&)?~(|NMBN"
        "r`?`tf5`bh?5>yh`*ChVvysj7=k@$?J$LfjaF&Q}<A~4g<a8}-O(WRjG3MAi-3JftZpocA*~(hI*~4EC!drh#!`{1p|AZO$qlUw^"
        "hePu?466E`0P^KH9^pB{N&_i#gqk$*J~4;%eG*ga(0C+WJla6S{7EEEG(<@kV=uXo-p90v4)!S@x&)L{W!xs!4kT(hYlg=N{O!fT"
        "l?0a2_+Z7p!(#$H0fWJ}P8f#I;wX52IreTgl%D=uOvWGi*T{Rgrqy`69JU9#0DG_j?@<Fd%#zh}Sx%fw9XE{*8gLu4IA=l3WPZWx"
        "T`aVQGss-U*e5A384oCK#@39KZI9v>102&@0GnE6CpCfmxEYIqD;OvtMo5||C43@61d2Zm(VT1mG6?Y%G4)NLc923rc>AOlcWB<S"
        "%i|Ef3zZ3_ORfYU0%6O<Es;fseh5@?z~H>2lXjM9&OH{TZgul?c+z+lo93q*KMCzJ?tK#}8r+LY1>=KGl@&BjiRVAP)NPJu5hyua"
        "(iF+RvhYiyvSx`}6uKl}8o}eax;SV=a&?P#Jhn06IGqE$>m&qTuFhT-epJQxG;W#+AZ90u`NpL~%96B7rJ1bXlMsPE4m+)v2i8Fh"
        "R{-g{V0HnrDC>{0Lto#~Cgs}}<_R}X)jq9Ncrb@q|I(^8{z?VqMlmCz8l|)(w!@S=JW${2v63L?Rs<<@;}X=ADS@cbLbgbOC55dQ"
        "y7KzVmuPPR<hd525YrS=DANknu7j*(aulQ|-u}d9f~G-{88ErcF}`&z_jW|g_aD~w<pmCTX=an)v`OUvLhhOoJs-3|3@!mKRJdOW"
        "Trx`()Pw({aFBc<Af9)UgGk9b?*!b8=zv|m8o!`|pcP^T3x~`Ilom~li{a*(hHZ<*WyS*!N1mMklzb~E(2SG}AR8cffKH8<e^*>G"
        "I9AQ`no`k=)<ed!1WG;t3F!H26gS!Lg}lcE*&J&y)(cJYRgsN=;VI;13-mfr6D9syMrTvlS_r=LhOSB?RT8O^NR>pYBvK`jDv4A{"
        "q)H-H5~-3%l|-r}QYDcpiEJ*3s9pJ-?msAlFaH"
    ),
    'ANSI Compact': (
        "c-pO5%Xu6j48FUH|DXaKe~Hg-B^RGkgbp02AO)t9>>?hL5X{3~@65m~5<-t(0)PJbeEUs!$1nVN!)y6_|B2TZqL1kMc){cIX#V?~"
        "9$zZ0{P_A-{k4p4M2}aH*Z=<Vqs482zRRO^%d^r!2+J(>9=<7LWEd4{kwAnj<2AW_MYg1ek<jy#zOfwAp)q9<GCya2fvhh#sZl#D"
        "LrGEhrfM{XY{ce1NosN@{lk+hJwL*<P{}}&e!aR0q1M}#SuCjZQcNtiQf4H1sv+I>mK~EZX|trA+3CL9GtuM>fp-*!u!ig2*DV6$"
        "iSzyGvUS~1ey;D0e<J6w1ba2Dc`h{KM26*J-K8HuDJWHnvY@VPDEDPjw1MP<WNyW(RmQ3Ifr||Nv$5BCX32U^(*CoJ5vK|p$ZBDx"
        "vuD?VL1DuR9vng(MDxsgXw5-epn*e~^@^Qn)H|3RWGSg$Z?<CPd$J~_f#CqGbv!c1O1E-JD2N&tkyb;RiA-qAn>j28_7Iv}0;r)c"
        "(=)+fILPcW<4qkq1tjcDYIbruR30}_U{V~0DbTwqw)vdCvK3rx+`@~yhh_n9uKCT`kY)0(x|YYdD<N>e_J+rr=xV|NJH$CB?NF(O"
        "F+GNH2Ltq4ff;QyQr~1t8iNUkP6nq=o6VF^R;fCtoz4S!ZMWD=_2x$F#;Wu%3|BThmB{8QtZP?|QP_DxC!n}<<g(gyiYTev;)G%Z"
        "A>t?4AW&YIvDeI=`NaDw>g*~9{ub0>x)dQZ-WYM~_F`4*D4|>CInZ?gLpJxOZ_szPDRIR4TKucUP99~n>0E}4uX{uQ0m+r#yhnB$"
        "dOCWe&`3QYM-xOdot7U4gYKo}{XXTAvAo^&))#aR*T%6#+k+7njj&EESxZt`?n`i+Co_la_rivsqfnO5w4vouBeoIs873sR=SpGH"
        "|0q1#Ap+H&y~XD+t_sb%1fAb(HiIc-7S?NV4vplp*uslLo?DXf`Ru;azbVb;%IrIsVLtFU))rKsuPqOeJRYh7wfCyaP%o-3^{}Tq"
        "_UJ%Md|6;V5IAC`>tF&-7&OTG2|(n%X?DA7sW=6E^Qwkh;QE#L=R1Kh$G@$dPWkc$iF|z&ONGfj(iQhuVNzCa;)9NjH9eM-&Y`qP"
        "s7Dci`!?J~KgOv%ZpH!P`i3{V!<~fu#c<!EWO&wml#Z=5>F_jF*D%nvOK%rBTD768j4jr>a@`arZOZ0moqN`G4NPIPJP3&Y%Yi<5"
        "k5Cwh`wy-eN=g"
    ),
    'DOS Rebel': (
        "c-rk;OOoR@4BhuBu;^{7(wRzCmVK3MW|f0@%gr2cPLfaCk_fy9JW{mVGnFovTcTu&1c8Sy_4Ti>zy0}YZ*TU8efiyfd9z<%USD6|"
        "$G_fR?EAON$LIa!`?ofK_V33)f4BC3{<`n&Rs)^3z6<NRvHAc19c&})4v#}mK8INyn*Hhe-fSo4@o<}(^)sD1{Ww3+<a$dSw_S(i"
        "hI}1Rn8CHTZW_lsLU-M7(EZ!U_6NK9Bq!a&ZmowkaozS4J@(bLAD-gcw=1DN61W}54BGc`ALVPixHY)7HL+!Ez4xKuHLsR7VS9hs"
        ";xox38(hf_NZc}t%rXFO`FBs`o*R!32{d5m=~DFIkwN2Z$LTqkSTo1GIbM5A>S@#yq(IjAX&#lrbqa!1d1P?jO&l_K+tH#EZ!fLo"
        "dEek6Xm10^I;Obbd>ijQ2kwzGRp^`mZ|sKzQ^16ZK!7?u4CxP&G0L(pXHNMW9P4xvjFsKzDnT<t>Q7g5MDZE3Sv+quyf!TD2lgMn"
        "+WPJR97E9R%_D9xR^ru>gKLFT=eg|U+JRy+f@F@wQF9sX7WZqO6+XJ~M$SFa;_>uoz@~4(1asoS_#2LxkYt1=zTfy9&(+91@peXc"
        "`^DYBmzqtfGhYuF+A!=QwzMem6CR+%tB{Y{ZSWq|>|^LTfh-e)C*r2_5*D?EPMz`YKUvxa_=n+8<nhk8bVh9%#4z)r2%C={NS*+6"
        "T=6nlxlWII0%L_y^L(|qi2SU8?Nh*sWTQ*=d+GYLU15*8L*5~XIw?8<!2IWdJQfl}SAweRh)R!>n`UjZv%jqeolM4=nv1D>c7+Eg"
        "V?z)D6$OnvL9va2>lpTpVPbsG`~*uRY{y_3v0sUG0OO&PckQ859ep!|1eR$X=BWa32@RR|8C9cX#ZxYtdY+Fsl>TtE@@GPW$}CxE"
        "59!e?kWo`rC8_c#eO@wmN}01_dZtaz`AyJ2@If%r9K5h-7*!))Nugp{Dt_Zab&4IO%UocSS%?<&DxW+P9uU8{*k-}BH1q2781a(b"
        "Q0elx$oN>2B9B)@e#(iH(ua`fN+S&U2H0jY8S)e$RIP*AKnD4NkR%P={<kV*tKtb01_l|lcPV|u$<DMsNgyU?i8R>+urS2j5}>4-"
        "g#Dhx-+dI{)2`y_74SffD??(rqE)nN6{GeP!$25GT|pBuj@Iu~jFSQdE3+U(uJBVr&>BPaY_vlDl=iJ8r9wxTpDIXRt-6s4K}2|X"
        "C!~EzdZ7VU0hF}>X+?lg6HrwJ)O7)JWk6LQuv@7=dqANv6s9IRwb9R&*@_&-*sJEQBBw&)f2mxTOxC)>friU4S3AN=6k)O5Za;<9"
        "J|CUVE18a7<9w``IHsQ)k5&z`OHv_uyDL;lO4XBcD*_oSL4Vkvn0NZAfAoOMt;#9fEsFP$V1w23jujO`HMAicWL@2ILaG6>%`)B?"
        "CkX`RvQiR!U<t$}DVArVwXU(NOY8vI2$2Y!AqT~!a5Gb?8h6-0vcjkS1TwWJ)E$!Y#8pn9qCDH^5KF+Klm8UamXL&3w*rahGoe!{"
        "4hHoLVob`C7~C9?ovsBEHezb~o6_@>p=!2;uG>r%cgqI%YVzPziPE|ftAX{jbZhd&E<N&OHCRtFZ-sZICJ3_hz@!}1f;|vxA<>+n"
        "Yr{1hHe3$q4#yUW>{5+*tpsi1f`$Qt!&tSqqvKLuBq8JS+Fg104hyN&-kaTTDM|J_8GW{!ln1n7bfVMPnaSkxdu@I@zPf?|7D{21"
        "FEyDzQ9w%Tkg^4dIg?ytnr|(VW*OoN7gb-$mTY2k6}OA_Bc3fEpjK%^n_P1wCS?S(ZNn&Rj3rO<q!BsNxH5Qj<!>Lsm(RWzR!B4}"
        "FQv)TC^N8f=1yK6pvP(!rsc<92~gAWhrr}}8Ovw%^J#;5#8oYq@syu4=2?;Cb>+`>*4dbUTiRat@eoR{n(D<w|8DI6FfM4Ygl=@j"
        "#@wO)sFqUd&crpy9CYZK`_Y6W>{-xYqEdSwGA1-A8Nidr(Q*8HcmrakJWR+6EK(kYb8agt^|LtmvXlsZH^ZWY$#kk?!YH#*Vk|Ss"
        "nq(MalC_F_Ru!4e7-4thr|iu->KlO`lU4koU<H>|z~H3ZEvwb2-{~d1J|NwOhO>`O`D8ni%P8ms)CRK$Y~;51zM`N#LE;W4?fg64"
        "YGuFbt+UD-Y3ZA1m(6>azMM8M+(p5L3fs@!J&x5aEjxby8@0Cpl>"
    ),
    'Delta Corps Priest 1': (
        "c-rk)J92|S4DE9Y6&%3HWHNEbRnoZ1AyjNZ;eyHs^hshHShSM%$AC#Piap~X0s5{!@AQ3o`)c3_pYVEw!{hVu4u_Wp0Gf8`Px~T1"
        "Ml#Zhq+6O@FYbEj)NgaY!CwK8i^Z(#1!Lm2k&EzPkiRysg@fffgbsDnx0|(jNT&*5NJ&X|IrqAxVX8MWIgk*hIy)c_^(;r4&CUzU"
        "+SI0Q7D8ww>Wp25L0w;K<=Urr5TWL;51O92BU^iE#Ziiz@we=*Czjf`q%KM8=phX3g7vB()YhQMW|BCNl3&1#RANp{R%DclFV;s5"
        "j?4gBlf!Bi7Kn5CrT!Y6Ww+z+Aa$hjGq~pU7-CWWkbNIxFino>3El|9xFXNNeh#9N8yyXXh<g{wNAgxDA{@R#w(W^OX^P;F<sAJ;"
        "|J;Id%a}0%v^F#!o54YWvSU#6(>2vm=5)*sLBj*oVaor%^`<er+AXLYcs{f+$ECd#Q(|j6vys`Am@5khy*(HZ*$ir@FSp0;gq355"
        "P#<FxNot;s2XiOR-Hw144QkNsHXJZBtD&P^D>;hb+@PMhLl3O225*QBrd|1Fz=&*=)cs#LmWJvT=;kIJWE8bwXt=e}WmV9$(=1ba"
        "@!GvBqo^K>{o!A988Qh*%!<o=3daxRDHAbu&?F!?Nb8w*`1E}SzGk973#1$L^tIaAbaDrfZ>DR&RJQuC>u@<@=Z?-mbM2FLMD}7w"
        "(IbG!h{oo4KAo%EmI@`VGbSc8Jq<zzzCyC6(k7tne}2VcJiqpfr+h7zbg?cuwlF8#0?L#(wBOKvL;DTwH?-f-ena~W?KiaF(0)Vv"
        "4ed9y-_U+T`wi_kwBOKvL;DTwH?-f-ena~W?f0Y~sIkjL"
    ),
    'Bloody': (
        "c-rk)J96AG4DEdi6nlUqGwJs#X<X$HDpaU&VSqDxz&%M;6ahSd)Y>UBDNI}0h@e+`03JTX>+jbezb^Fs8~xlr>hH^iF4w>MczoV("
        "@AunQueZym)<xHHtmS@N9~T`fzTcL5wOuB9eAM^ZU6oRkRTzWcFC)CTE&Bh|*2QdH+_aB7GJDo9?RRkVwGI1!g>;0)9vSuL`cmDu"
        "!L=s&9w3YE>~glhgsc8#eSWFmAq>eXmz@|*emt5Va#CsCHW`!d>}#SXYy0{W6grUa083P;A`glR8Rj5AG+Ru~64WnED)9|VRj`U#"
        "mEpq_Ox-FfWC|n)1XN2yVB%(B0=-JTR(#k2iW1nvg!{W2+zJ`(u4XsDx&RGu?^71v-`nyvP^jtvvSI{uSz>1)Bh$X9Xq$DLCa`9+"
        "zH%yhVg%h7RKK&N2%+QzRkQ~xP0R;cZZ<|>fXi=@;@86|+u+FsEGT^#H@(5CLMUqiXcm#7eE_cWHryQm*JMD#KwE%~5NH;)oPurp"
        "IM@h6t-9MX1_!R1^V7X%js}L3=YupuUsR(lL3dV1yF<b(ZfjMJEj^NnfGT7hLP<)5s7wh;MCcN-HxE|@36bttU7zwU8fIxs7Kj%j"
        "!jKOLR}XA2A5I^#a4#D495@?A41_f%Lxg2}8Fv>j;o9Dvv%{+ND)!}ZY1yTke8qCG<g^<zF#~%u<ltb+CX}v_^SOk(bg}_7Q4S!s"
        "u0$ZZ=WN_kP3j4%(X`M>dP7QZXiJc0$p=8Vq+Ngvwk8x&&zg=z14WN=M#!$ks$5`>?u>M9)_xpt-o|0f1EU1>#A<?ZOC2Y%>^SXA"
        "tF%-#C6~$0<fmXq8{}fcAIf};55aV)2$h;9!uYR{oT8At`iM$Ol{4&v+EB8-tBI*gzkzv{(l`<lYB5<0k?U?&g`Rt-oOwsG8=Rk^"
        "S&aS{NPOPmh??Eou~7%+{ObCO;FV?U6|xkYQ-yXF8=aQye1Iv`RmWCr2$~Pgd+HgFq7YozWq#zJl(GogIL6ps+oLq6jeNL8&j?f="
        "rD50fll2s#rAVi(s6<!rQ%a}M^_9^9gj3lo=m-t!6fdg2*}5Z7cd{Ciip+qJpegrCkf0?*M)3xfb2;i=Q7E*p5|eUh^x%95eZWVA"
        "ilweAtE^(do=RnY^2SDny);-=W;mJDk`x7uG)YTJd(jgVEUPKij0=SxHz0aPVvY&Q{cxDTkGsGrhY8KWll(NQU>c9oFl|V4EhE}s"
        "kYnHavzv4oNdRaI6etUxmg2yH`sL`rPT{~sVdmqro6M9g^n&kD3qfq2D9$TSQdTEI_7;l;2{9iJqTTW|9pV&3|E-nV-dUIk`idqX"
        "EY{~wXYBIvRA-?MWRu*caNk*X1C}{6oHN5YGn_NSIWwFy!#OjYGs8JEoHN5YGn_NSIWwFy!#OjYGs8JEoHN5YGn_NSe>^k%1FFn("
        "fd"
    ),
    'Calvin S': (
        "c-pm9F>b>!4BYby4%rj5MZ4w)`9PK~oeJa&M&=9-pdf+K009~pFyPT+U*ngQI*Ae`tKJ&NGDS)}9wkv9pC3=}OM2$#jn;TxUueBp"
        "E;sUS7NhBHbJ#5Q+{0u0)o6Y~TTqbwVdN32+KMI@uRLPw5stRY-(ky2VkO1dm7j8@7(@EmafmC#K^VT`{w|sce+Os-a{*TpY8bKq"
        "%l|)qK(az!NYX%iN-a#sWx!_wp|gB8<GCKs7c1T&ep|64e#L$vZ|(fR1_N*T{1XzBTU%J!---WLSHsbCHbc{l_j|CrjgcZVPCmC4"
        "Wsw&wh^NDTu?q5<Cb)1Y8GYr%G4{l}5^={{ppvw5o7ry)JEM1+PYu*SNRmSyfaN}2JQjqaa?^?%H!cTia<Q<=gilB58WbsT`r^7>"
        ";OB<fHVL_dn59N-AdvJ!4D^8lDGsdyY6Yd6>?0Essp7;b!Vw}lJy|JpNAKW!wn(gYwlLsz#Z1~zI5IElq<%qspqR1SLF_eo>kx~e"
        "hf7a7QNh}WFk_@cVK)1J(2VzJr)iBLUySwKUX(>%ayz)b8!EXp!9`pHMPE5_j6Lz8MBMQfs3fi2X7;PX&gdQHQ&Y(;lqQEfK$YBJ"
        "l|vGW%1tkB!gfo5OfD8y8RwAWNY|i9fzub)tqylcyfU?em?fh&5J<+Gcu*8bap)CLFDS!gA01;NW1KU0;4dWuCiV"
    ),
    'Electronic': (
        "c-rk-J96VN4DEdi6h1)W+1c7xNt0?1p~8g<71_oI^hsjtLjfQ_>O)JUjacJIq)dXq14vL$-~YZ(Uq|>nJ&w~Se6*jxf8gVA1c2i?"
        ">A&*<Zr2N(54Y>j?fU<AZ5E&28>&ERZ>Rvccc*;X>c=mKzwZ1|>{@a)?z-#kcCFijx;^)gu@=cS{pd%4+Nq>ttbJLjp|#{_p{lnK"
        "`#TS1&Yr`Fg~7RF@+cANYJV8+0CHQBZb8!>x@=-&t@$8Uc3U~`!uDZiIiu^l6rHIr$i!_gqwTTx_Kl9!eOnwGq=6nS2u&Pbo$j?>"
        "DG*&`ap=S23q_TL6`jW17n0FGK?8GDPIp}y@NmGP5=1731p$QGgDg#K3>A!DNaxEGNDw*mQi+mD99cMErMSNHP5W9rd4@ED0ibGy"
        "#!GR^@IqHZ+*EYmxPMEhGyn*#u?Qid*NgssL}soU(hY<#qs3cl1s4RJ2&f0(R%-D1_}jKSsS0T!b+M#9qSz<<-nQ;P;sQ;XKjFfl"
        "Z1ZP)Z+#<cNk{nQElrcy3gyTWFbzql__`{v<eFPm=b`omg|G##v-Ic;B3%X{XQpWyyv|E)ub3=#h8~&ayx?W4Qo<_btWw&_%Y=Ma"
        "OnK3rRUkLm6wOHyG&id-OqcHL1+03OZA|prQQp=U3L_+b(7+M8;&F$CCO+fd2Q`c-z8kf&x4_lZkq@zLip;^myt-edlxkj;w`)H;"
        "u)#F-=NRpCl_?V1ponGfGYWIw%-G2fwyZLI?~vAQDT`&?JA^?p1(y=rCrHu+MPd(vickH@+e<BZ(V3K-fvqa-<s>gm%6G|f1CDDr"
        "xPO+qQ1cBM!=$B&i^tNUM2*d&*GC?B%g@P4skdd|k3qUiX;1E3bV~{u9^YV(g@0Ko!Z;Inoi^Tcbjg~Gks}&6L~Cex3oGaq)svHp"
        ">63vx!;Y=W`tp=s8b^gnuc?$m3M;k19qZDPT6=6@dDQlJe=>4?>ZtyxRAGF-H@>+7<$&G@fL7LOyXR5m>{Zq8##G5H#hq6ed^cgk"
        "&6PJ>cd&PJWaUcE<KICaCHr^yFAJ$J6NwC(81F`dm9&fpf#V}Fa|9lbFy)E3-3`56cgqqLo;0n<KjWFKk_!XcZHc5V0i|ka)ljOT"
        "Gl)rwV{)g~vL;JN`bwMMUxmc`ys0WSt-K~Rz4!-;Dc{ROZ<>Z*J5+d5>MEUapsEpX<n_Zf=f;*Ywv@4@j4fqsDPv0+Tguo{#+EX+"
        "l(D6ZEoE#eV@nxZ%GgrImNK@Kv89YHWo#*9OBq|r*iy!pGPablrHn0QY$;<)8C%NOQpT1twv@4@j4idg;uq2)?}7"
    ),
    'Sub-Zero': (
        "c-rlj&uWA)5XSF5#fJzz>2wQiFJ16K0-={Z^$|*+y~$|C88?eYd*~q%#X<Aqn?D)m_H}#t?C^$HT;9=N@Z5A=H$Hrfjk>Dw4-I%;"
        "2y1M}A=q$iIEWQ~3znbmV8D^5c(WlZhr3=a?gN60OK@@R!10!ZbJMp{oTl}Iz$iW!R<BT&w5bcpD0Wl96iUJm3I6<A5Gj?%eTq2w"
        "3CGDlRTc|sQKb;#m~0*SEo}s3EBve`;>4MpD>+991m#FORY0L5#u=O{NWka|dV)R`qhQ-et>T4yoeFKq&Vr%2t~J;Ub+cPIqqMy4"
        "xmTpb^mMfDts<`#Ij!hy|5_!2O8XaonAK5vytuIE%AX3Ge_vGHmqzwHn4<9RK%ao5(1DNF40|Hg1;1?;GaVJHqY%RdqmIxHMj*Ar"
        "k39|9<B)OO^EIJk{Cs$(KI5md*=_`n8YCy_SZ1ZGNfThV$S>-4;OdM}XM{Q<)ES}92z5rNGeVsa{%A({1#8l-U;"
    ),
    'Standard': (
        "c-o~{$!^;)5WV*+=3pQ;5SWdd1Sk*~{fe9jFuml|PvFOwvvNp|Y^C)g*%D`avr?abJ}<uy@Cq;R^a@We@V&h+&(E(v;Bxu-4GzDe"
        "Z#zJNcC1kQvEcDoPIwnU7SXa}TSAxL+na75U_2`UlHiA~md3b!SW>%qKI1j{Lu)J1G|BDLIXQNPuQo1tPEbg_Tnc2;3n0<`MlK4s"
        "C2U6GTa73Yk9H<aQb}fHms3>fW8-lGp=e@KCWMaV3h;(koA8oya>{l*j|5X?fd_Z&azqt|9hOsLpK)nn%X51R0L!TvqM)<xs22&y"
        "AiyNVBh#9k9Kf9n%xmT{^Oy6Fca9Lx#|eO0f-S=rOTZUPz&P3~VF*xzR6!m54dQQDOfP~j`&jX}bSMHe`Fd82qsV8D%O6{$63|R>"
        "B1SLs#XwFA2m`TUB<7$;vLbjAiLrX>O-?`eQ+amR&c2<M$DegF<Boi=RHBSyHV=mKQ89?I9V~}xt_AZgvmTu~dzF)T^6zcQVMutA"
        "N7E)CUg9&1)p*gEGQL8H-C6{)r1Fx#K~CMtuBp6aDyJ&7+owL!xXJ1TE4m1FIW0}Vz@$(YDQHgl(3EL(nq>s9D4QjyG`o8VNL1I9"
        "DgMz^5&;|(9i@$c=E$hvGXGvcGXgA2)wdkeyLl^>VUf2~nvGO8zk>OdYV;`iUJ(JOwv`5BQ!2!%6xpm(gD;S)WatZ5OX<E8%w9Ct"
        "Xp<79eNo$_muG2ERDV&c!0uYk30E-zxhX$z(p5N%yGr#TWfrOvqF;9$Ltn-M+p;K{lj#h-r@)BOf^arVAsc<}2<Juq|24lbo;81<"
        "_qDC4+9`R+;(e0g4b0|E;$$5y@9t=>yt<(Xv~jv$!Jrf;!#=c2=@U+->L;Za|5#JZZ78?lE>DedU^McY*i9TKrYm2^TA3O<8^NqF"
        "&nd)1h$>@*?>6n+1{GgA%WRGLL+WYfaWSo1<99_G1I(aOkQAP5z~oRu3C49xX_kP1Q{T^3c2dC!w&&<wv-LD&0LzLtCY@W6O`mVp"
        "M;E1!VZHB_Z<+XdcRfel)d}t$N5%HeTG!1>QMzhV^*)6|nUJl5ZOQD5t($5u)ta}oroE_2jP&8>9;&WI1YNXGncI65Lr~{bQOCMy"
        "(unmciS)T`f|cK(Th3~P$_$ZAfO)e&rj9Y1KQ+l<M20V8IEBd<w-a|HTi-fQ9rNGfhU?@Q+{NnMgP&SW)|yam{?a7WM=cbXLvZ&^"
        "rj4jRE0D5oWW&t?@kYwRj>bWaDkOIF1B-#nz-Zz%mye+JT_p{Ij`t<|mulckyi6iV_f*pD>aqzNc5B?=-rC#G)Aqn=GfOR36+G>V"
        "1D7jY{ewCJj=Nl{+~!jJ<ZyU`_!qVELIV"
    ),
    'Slant': (
        "c-o~{OKux64Bh(_TzFvvMp(B<&;mx_Q*<=|(@j=Af(~DaKT#uT#!i!<l3kIa9zT!L=ikroe~$15uW)^X>nnWAzt^8XuD=dYevZfc"
        "K}O(79(hv6GT|{#WQ4n{CgKqR%b16<6bZ}96P-oW1t|aK?;*kYen^;jwdhyvQFm5e{>lf+8ZrM7YtNtgtNWqObzk1PZn{#(6LMo&"
        "1)Q>ibCy8<uQE{`vf2gnaR^zWER>}@AuuUEaoAf`5UEM0tWH*x&0)b*wq;$n{Oyug5-!-OU=U6PG;quWGFjQ3klY=z8H$OZ;3P_o"
        "n5s#TWTFU}Kkc*)Fa~qBIF)T#qqu~!dNDP+q7?&mEqj*KlWex^vJz|lks#s_>a^+#{wZ1+QjTMIL@S-;qc2oQE2ypS0Rk{%-wV3p"
        "aCHXbD~?C^dz5zJGTxxgE(p$u*(H%yCQ0`abT!-%6c1d(pkRrdxt=&Af!7Dofsu-lWT+TuK6R(Ay;|#s?aem&;&K4@q@P$#XwrLi"
        "1lEF{VX%*L4V7nbCQE4IwB+3&J?PawB-Bq?=CX;lexti{v`TS_rDt!c)F4hzDK8{ONQKCjGj7?Kr7bU(@B%feyFyHLIaWZa`s}a*"
        "YS68SpoMKl2Df;`;>KYNxs|6MdJ^{p<En5j9z-62pz#M%|FV!&Le|VqEdse{4#O8bdN~qmC<F^UbcY2ZM6QM|D%vQi03n=u6>BRN"
        "3t~^lT`OjJ2)<>oWGfG+ikKzAOHo@kX9+#PMIy@1w1-P<P(qVJp5mPJW(#f|%3_2nH3|tBxprm@NJMRrUGbTN)N?qwUvBQ2ROnX@"
        "!Q4Jn5h*?P25YA5A@swSnqeR&iiI*(%N|Ep7hA?uJyH~-pQWlw8|D7Xv^vX|z?H_9|4r+;hJIoH3;6zHclLBw>Y9|<J;tV5z$-gL"
        "P4hN42XtTgzE+sW!<j50IA&Qvl28DisdyTu6NQ9I(sBkBjh3JY6U`#hPa`|a3@jGG(`w}grM4oP$wj#vcKU{mM#D1@TbirMXPj6w"
        "cjz?l&`IlU;a*Vo6)|OFsQf<MP5VGE7{iKJ?*mxwG8w~^v=P|6*|pvJe*eC@1i|l(OHgQ2fUL-WtTXd6sSMzUoi%AlFKrV&>yI=w"
        "*}aw#IJ^)E>e}iCURo#AWx@}>v65Ob7&W<<#@L?F8iua?Jg)%3TaIxBInt|kv!N6{hI8!^afGwhPQ9z`ckA(r>tZpoYiM_DJ~Y^`"
        "JKB{f5f976{E=ka;5EpRZ$4V6TDV%s28pprUNg*NQu*b|!_%w%Ahzpuf1C7PI?{bEpS>!^IBU+lz8~%|-Inl)?9pg`E4Bk#PKm*q"
        "$vE`6oU>lY1|7w?ZrKY)WkzE~WyEResghAR%2yT2qk{%H+%^NBGF0@=FBlu?5rG~y7yI?ZI78l%Oia_bdsU2&WcHeKD@HbtWTkcM"
        "#nx|Q{UclZNH%?2qj!3ryshDad<1(6v=>;Yd>W$XA9hFzQ~"
    ),
    'Small': (
        "c-oCuOHL#)4Bc~zw`hrO2?#K-Lx@wbiezTPYL1Y@j}zOm^QneuO{GXVj{W>>C*S|RpZ}iliWfZn!P6@q`S<+%^7MLx)5rXuA0Y6v"
        "pwuVT=b0{b9?;KC;LIXz`jda{^9eGVpoq#Om8wxR^V~z7(Rb)O>TWD&i`%+Vs2Ono#zO)nnKVm4Vhddoe{JjdrOok>y5Vj-nQB&6"
        "NS43XS1}?}{a4`2;Fz47!$meaAz;FK+m=WI9eiMwxysa?>jwO5J~VuFU{j;llrlB5@YWP0GpEDqpms<Dh`{8vO>8{r5o_S6Hd0+{"
        "L}xt%GYkgB0UDF)-K0ms;O_!Q!TBP9Z~&5<@7O2dV_>C+v}14?b~P`8Ac=co9<RviZW;w4=9L~w8sMGG)BQn8=_9pD)@~p~Ft7W2"
        "ARwsNni0!k|1+z*{G@i=_fEqU5dWA%y9x99d1O<}g*X0L5)2&PGOo-qo08@o@0Kk$YXB7Z=1?8k#D#WFyechl@cUPTIako`$aqMe"
        "m6ma=I(G%So~7Kol@x*vu8@MdMMCkj&g6)Os};RHamob-UHXBUpn@IB$AV=|+|}BixQn&kOEhsS>YBl2=bPjjS!vu`3suWcQ>*&-"
        ")k6qbDnX?@-wG+&f*3fH?x}At&e7FmOX||fv{{o=FH}HubO9!t2M=GiBG0=ZvbNVt@`4$CUg(#_x<<Q$fL_ymmD;Kss@p}UdSk8i"
        "sNPbj;l0qpY~6J<f^*ARK;;;d*7+L~YBkXgmHF(Vf_bQ{z9g>l*2aIOoWj~^dC*|1b!F}9S~q6){bb@62y1GXdhVLz8nv<<QA(Ay"
        "{moVN(j3e?WfTuFy*Z0ABw<s^c9w|4)CcBq=nGSyfhZi$U%t?e3s%#6Mc*d5p>k{7d<f(mPNJR6s<ljhXh~8EUfqB4GU2{8)4WzX"
        "ueL?gxa`yTs{gS;%>L`kt>*r&E_=J*9ly-;en?O@P_k{qt_FG=wH(tzu5ka@Zs<o)a{IdjG!)zk9{j@LB|Jw5s?m^vM^GNMQ2jba"
        "lH;Qh=$a2huf~Rpl3O}MkHDiy<He(?b=TwK0Z`Q+DO#UevgQLNMp+jREAmjW4>rh6JS~*DZnu^v8gE(Xl~Z)i89m%($Zm5s+O=-O"
        "*3Q{oNv$ymdgMGW<uVnimub9C50|k29uqW+"
    ),
    'Big': (
        "c-o~|OK#jS4Bht>To}j%2%<Lqpap_KPmz@Xrkk#M1RcKmkQ6CXvS%_gu@&2*p7?pB<iG#^zWmwXC;Wi#zu?Pv_?*8l-@g6$`U$q*"
        "tG|2zgnUep#v_*F1s}^Z0Ca2}YL`oXu-<&j-+Frk{|xHf8d!hzD(2zxuc0W{%U@dGDd6(2i#21Yfv{c8Qp!1G5e0F~l46|!K&baI"
        "`A7>$#HNSbJ+zy-(H<*}+A58TGhpaCv!H$}d<`734lcR#BTLHILab)E;1%GtT+N6v#4t)QNb7{k$f=>*c1L5yHZPhKDabwL!6S`U"
        "I%3ml%wJON>{N82I+ERKnlV2UnurMR@X%D6usxO<v?q9`VPeQOiow{}v`39=_Yx0M?!(V*)<&bX{NrBPj(K|%>!*RJpF|I+!MA<4"
        "^GKW^1+g%|k>>(??ot;>mTxY~4kDD%TS#6|opvq|DPZOfL>#30QpW})A>qi{du$zKf}sdzg(n%gH`p#LNknCe;K31I*?y12i3d{>"
        "ziZT`nvAA9A@Bp%A^qqU2Lhf@YSJwF(@25i*t!A<ga@(kh1GFrP2=N0_JB=Xt`F3mZtrz_&-eUJP+ftHbROwiVD9|H*n6~KIAjSO"
        "iQ7?Zt~$81LZM87Ou%0Aml%|sw^Q|)aSzC7N>OX;a0V*?ERri+6;h)>n_>6C3}Y}$bQCMWB$x;KN9Erlx~A!OVXTWVRW&=!IgX(-"
        "48V7C_L6@>k<p!7)Daqxs0db>Xi~LwS^~wi9N5r3KAjDAF)jgO1#CiAswvOLr6Y7&VyG%BsI;K+0;UevYS<NwBG@pVTume51J}J>"
        "FpH=TcuZ5Sj)8}(NnlXL86CH3X0FXf<)gX_;&j4zV`U^cNm{Cc8nAk`Ey~pdX=oxi_?>ujR8aY^l#Eoaa=i#dfr%@{QYNYh8xp=;"
        ")W)4PZ*r?{=Ul6@Ibv}Jns2UP?=ez0eiYq7<km9?n?(?rab+ab>F;dFo-xvd3s4akiMS)O?n7+3_8!Clf;ftUjU;}O3obH`Xeu_%"
        ";%8`4my)KIgo8Gwan&^DxEa}e)H@K>ilk;osbyk|66zgG^hm8x?})G%_W5#5o;=Vv*lxhxpu2(hgF68H3k*aM$k1;*=9O!=z|2+Z"
        "1LIdVa%pC2Qhh$CrUHEyw;I2GctJr+#?4(a^4e{k5LBiW=kd?Nvq=|+H05qZF`wA28!U>Y<~jtG@g(1QXH}^fM9{V@QizFmxaKDq"
        "H$N6Uw`y+l!hKjYKtpx3@dM5}A8Auh7swYC-cKtt59!k`PSskn*Q?J5i&GfOe9foAroyFZu_lm$giS20b;nfK$RKhq35UpfucSj@"
        "3>CdE;H>CwwW6pX$R0;A#BEko)k|8sMPJ|U_n5h(UFEiaWzCGR4AQ_ss;3|`pvG7^0<E6bb)vr|zJTs01KKXfzRWF(d>=N@tU`y1"
        "a4&OaeuNKLYvNDqosz>KK7~-qt!G;w_VV!K9<P@piI{UA%qT_tgId6KAPbt&XQBk=Dt_HigKhen!AppMh8>EebeauNmvY|b?9*-T"
        "a8%L6gH1BGWOGWxV1yg_!w~*EpV6v_T{sE85($JNGA=iiZq@06VI4O?Dn^3YE1O%{ZUf3|tzb9d_{ELc4m{?yf#baGy)GG|hs132"
        "wR$O}RxpM2J3{we;eW&!lB@"
    ),
    'Doom': (
        "c-oy=OL8135Z&t(Z&4B5ijbS}gva59LZMT5MNIZ=yz&t^{6KynsRFv%tyY1miX?gYd!S#xfBpU6h9CHjuix<XAN=*QeGb2$FCcur"
        "B&6;_`EsS#{1lK2zgS+qBB4C^nSaWo4TA=);g<MY?opl~e|ge!xBRBX{d_CsZ<Xg|w+V>v<?*=*d2m)RW)ZQd3xx8F(m=DK6cMi#"
        "f!{-E`IXX^PHBr;fdUMZPKnQ^4(md?)N3C0sv@#3Ar^G6^Z~r*ds#mUG4x~MG%LF2*%E8B^%rLq)?bvD^&hW!t|-#;0Aj2nq9QiB"
        "mZjDK5c|nOVtyWYXv7^ChAQz>F;~DFZX!E|jCl;+Siali=)A*RpxIyF**egB^pBSvfCn?x9`T1K<xi5QZMJPc;>^tZ;DLG?#KWeZ"
        "2KBV*TC27UnnY~4NC$z<7c*!w2RqqR5YGj6k-K_|G;9VAAqs<_Q4Io%$ewjCeh@BhDYx7bsfZ?IwiIqt2hNtAD2u-F0)h$(Q~Qe`"
        "Fo5ER0!7h7FyXM&InqOoSYi^@#GUhpH45>RMk=U;J_`~+STspyO4&NWR9j<2s)$0f^P8LbO?=94@^XHPAWlz`EgD#K1|xGoH>`Bd"
        "TCEF~Z4o<@7mwjBAtpW~Fumg)lCQ*!H5c|3YLyKp83OaXpe3Np>F55yN7ajid>CAinau58#cS2*mJUnBs&m$MpKD3Qf&EY<ay`4N"
        ";j)~h&X8^OCQI8}t+_P3yA+p>;>pbupq`MXLDtZ0k)ZkNcxUNAEfK+n<!;nU$ZwY*qf&WwQI9l&WVE^v-w9gn$XV`T{y<WLmOEOK"
        "<(<{iXebCKC;@Jb1?*E{Z_qZ3^idK!t}qg+gdP#n&NJqBS-;hIXpOgy@qlFkYgs>8YTuN_07_&|C2M1WWp*P{YHUgSd?&JJl%>TL"
        "MrNAr(6LAKurhL_N!l;AQgCm`Ux7J>>YWYo4)Oz?H0wpP9%3@NmJK3IR(vM=??NSVVS7M7mRSHY?0FBLYm_OHXcSrjct#_rKP1WJ"
        "f>An=mx$-TfBAANFL8UCMVtwh)`^R}Gzi{f$4bGYP+1E)Wqt`2lZ2f&^Cm-u<voY+p2)d<S#de8vVC;=#&2K+>t9ZEk?+i6Z!U=M"
        "oCp0JbvwnAE%7T#gU0wtYGi^eA2AQ9Ohgq|Zi32AJU1F^U}-iD8m$Gr2H?G7F{Wha@fH1MbIPlxy=}(bJW&KIvDirCkYh-WJRD)V"
        "witPvhQF%<E$2(;(wO$E;B{LSIP@IP?I~27S|rnq3ldyC6cUo2UeZDnBY+f^n$-7G^VZ&>-679`nLl9_TV{~s3`s$!tpYp$LYkY`"
        "HX^%|GacY}Bm9T$X9&~S2^u?2Rnydnnz0h8M(=`{3s{f#=tys(;^;_C_E!aF2hbfINF7ieSVw6?GZOv?{h>ef`;x?)qx6tHp3z97"
        "6t^8CNy<q6+(Y50s${Btkde{z3)MKOQ_sS^HSLfl)zi>4InlwOmVjKW7RA$<=$vm89w!(tXkCWqMR$BgY^AVC^g_MdUi9d!xwn!q"
        "b)lFn@I!(#bVT#GL4N|XSw=gUo1{bscg8TWnm;D7Q}f_YEc7_gpO&ohN|#{{#~)P~Z!-I7{V53Zg^-vqg!w-g2LEIDAAW@pxc"
    ),
    'Epic': (
        "c-rk+JCfWu49({h2qqvR*xstGb=C)A70QVtAAyHo;!8=S`ZMiNZL0;bA>#iCO7r{A?;pQD;V1kD-~WN{|H8MA&(GVV<Msgzw~qpN"
        "`)JU%UxhJ#RSLkk;|9G4YHI}gt1vu!R)YPIi$0kia=~4^9x6H*(SGT=tbT#!|7^C-Bu5I|NY#5(40kEaEXMSU{volv=%@;i4~Ugi"
        "g%`yumF;655j}TOZb>^qwa?TwAs`*;XZ=aeLg={@dM<s|N;_ADufkEpT$L)&IZ?+d_+)abB2lK5QMFBGlrd4ADh+M-(f2T(e|gYM"
        "4!Z7gcM%SEzF&2ns_EvQ%Ir~|y5m8J5RNOKn<-s!+XhoOs`)o&3fD^PCH#>inIbkmyzb)7;gxpDa>x}+P$$(=&mhQL(IDZTVMl60"
        "&be~$YZshjX6*?}*2r4C&-{}7P4Q2e4e(@U3$e;<uO{#!1TY{f5-mp}y<N%Dr$Qq|j6+p-ETaJyAB=acqj)st7IO5`(Eu$8Rt)j7"
        "VLH*8fJ19F&$KqTfTgvhu(?JEOX#Bcz~aIH6gp__B8H3_F05A03R-B+7t_1bVhR)n(t1}Aa2d2fDo$hp*WY7^4yNR28o8C_q}(Or"
        "ncy3#ek`X$s5@0X)KYy)hQ$uObz=~^L9FDdK`8?$Wt63iX0}%dC3NDV3<g1UE#o{e=n+>}QaW{DGi4rnNpu;9qS)F2X@|<#>1HK3"
        "F}z^MnT+EUEvCrS7KZj*Jl0S$T`s|Bb;}+MIed%(Jb^o5NvsXFOnO3%H7QYToCb{IbhYQ)E^4u=tp(yzL8fC%^dKU-R0Yh+l9biA"
        "DZXv(Woi3ppvLGXTui9NuCq){okH&mOF<f|y#tGxs&S9Ce$Ik*?GKm-2lUJ}uS*{t45$`24ZoIiJFUyIENzuxMyQNvTS^3~%ZPmL"
        "GU|El-@~vLYvdVO%wc1>qWgBhu%o)$+VDyPh;^{-oJq=C_#TEmz~azYm%{Ybi*~#v1Cyj<^YW#5IIoa3bC4gk4=K7i>K^j({oLjF"
        "D3WT8k^@Isbdn2_tzGdMR`Hl*K*~rQX^=RwAX}9fVJ^_Lh{y01)Myf$?@-9QwX8wN8Wd3RJonDLlS~oD6B)&q!DHI|ts3;avK3G7"
        "(=CKmoBcO*WNcG8KE|t!9cAQ|*i@p)tub5aF3FOw(Ga6@rwp8O_c87s0AwZ~*2i3fDFZO4V9ff^Dc-B|=k1`Hc1VVW9hKg+Ber`Q"
        "nv!<tg6MYCFJdAeA3rR5uIWj{U&N9<iCoSeaooei_w(un8M_`yduoaYBhh_(r~L&PoXYp^vrW~XnDCMv#dR}{r8^^k6vFw4qG&z|"
        "ukHHq97|r3vGvM2DVDx^Y10e2>2)ghzvUtBMsZ~IMW|QjGHPY&>2-@%ov5}}M0=k7xf`5&&FXvW-NLg~ZPX;X)opp+X7^4tZI%mF"
        "AAIWjsmD1lJs;X`s3M>4KKi>__^XA#TKKDlzgqaKg}++(tA)Q>_^X9q;V#(gmtt>;u-J=|W<9vAM_7zyeyBeEIl|_T5DsHuk*#I^"
        "IuGp~ShFU+>B}+JL48R$OUpOB#NA`kZjrj~i+%R(e@+#&9{"
    ),
    'Graffiti': (
        "c-pNP%Wfk#?4GaSVGi~o!|fK^lVRjH0uW_;*;7A3$hTK~iIisSWQzulht`Pir}XompWpx9;1Bo#zki3{e!#bv?QQwIz3jZdy)<b4"
        "1tee00%3oRg!J}u#Vhue{&(`N57tj#V(xVMM@W#okYzz`AnX0niT1URkN%DXw{hzqC&c{^N%^B70<s|Yl@0=duA{yqAtW2v7<7R_"
        "zl4x-mF<Eg`@1xZvRlRO015$pMaDv~ixj3Hie9APUBH`|qc7f=#6X>>{E^bKKR25gP1dIWZgMpVZwhrfsE`04&;_7>bgeTbgG750"
        "Uu`CqEb80C#%uq~1W^nJn{&25+d-|n)_(9+mM<n2v2J1<B#R)5wUG07u&Z$POQJwxfL#Wq7$oIIOT0nlVT5%OB%7}on;_phxT(_y"
        "Lr!ikRPOzh58=?z7Az9OY3LRNG#xA)4KXw^VJwKq@<XFY9K-4{T_^TP2J-z_0?8s-mMv#ALEHN5?^_@xXI9P*QG~LX#iTG6V^80e"
        "A#5@HkPz@i;F}XQ43Y4W3dWehQxCihD6+<hOZ2Ce3UbS}@UX^<5ZXf~*I)H<8x%FaAW<RYb`e781<SqDSY7qRJ3Ndu3<+UK6~qt>"
        "H1*8~HF&E;qv(V>ciVDvuf4vSYQ)dtC1NJ(FQrkxEYaq{Ju+g3M^QQ7be%VO#q%VJ6rw0B#F1uGZP^~C^=XE0rIKu-?92mbl7SH_"
        "&!Z<HJZWZByEThgu)bwwOyFCG@7WA%v}<Zb-`cHJ+`uCWM{LnaNI=#QuukNys8$j6WP{@6Hpj&ps#C10ip@zdmLK-!7-omaS8^t7"
        "3PiR~Yo9ASRDDJcph#!RhCnoa{57Tbb7eWD<J6D&%7YyIALLS05gS}t3mzwD-C-&}Q0tG|j?9#9JDN+-nRK?l6c2M>xZ)#XL!R$Z"
        "0!RZTYQT_BCqm`!{bhNSW)rPgq}mNOSIw|Ya{))M$%Yk#Of|M)R5tg-akg6hAl6N>7ID5dtv$+IK{$t9%SYYsn0-*PkL=4W{Qi0N"
        "L5*)9n90o!`-NnNxjBDMj{j3_fEw&>MXL>M8N}do=@3fg>I2YTq$3~3A<D-6E7CozT<u}H)b0;UDP9JNU#ksV%#{)ye{ks@$4tl+"
        "421@a31bHEv46XjTrTjKwcPiZwcNL03<6}t&g61a`(#0m<&~u$4Jvua`9jWalnh2C95SYaSP5V{L8FC@&PY^=6gU5FB}lCwb8+NB"
        "3%`!_EJ1|FpmjHvVF_1pkfDUcS~)hWK{8zEjM^M(07uC8sX&_^9Kp{F(M3FQ6fh43N8@Jo*Qkh!Hq^8u!~rEYIR^R>27H7g%&BO7"
        "4n>~>VMQO({5VXPljCrn-=>0*)k5ssNnPgT8=1GewHTVWzXmaHHSL{I3s&TMh>S6YH8#f4%-XZH@%sUJxB8(+UCnkCR<AeY(n2AN"
        "qXu8&9LEly1s-sYLTJJ=#WhQeM{QK+xx5@s&dZP*Gpc0&y^Jrzk1-r%cXgY$Y|B<y$Onbh=6keU>+JbG27;U^T=IO?>SU2~MNSnh"
        "tCY(m4r)3}MA5uC|IFH$8WL-HDv@Xqr<R-!ZZItIo~CHk7Ei(eu;8mvA6W7cvc=Fv4kUNDM&-lvFo>Erx^!@E9r<oD7;=k7f8^{<"
        ")D3j&jN7QWWf*k#ES%ZT4qipo?1Z00@4^{M?;effG;hE&O{(t5nvpbvYnHOPs6q7P77f|l(iRg}H1BU#P;fN2XpDHu%@FNQRZ%-;"
        "K_2KxOWR*9oUJuF>wVlM=rwC;rZ`*Jn(D4cRY;F=?V)YCHC=q`ebl??xNyLwYi1WayBg>L1=BhcZ`Ne3=S54i$7@>`{nqsC3ZPr#"
        "Ue_7fFa9{MGEs7h<ATG+qk3`q7=HT~JJjaP"
    ),
    'Larry 3D': (
        "c-pmEOO6~j4BgKuxM-j@1=CJ|00A0<IY1vlfGQiWa)2!Q@DW8)Bvq<Uf5slzV%N0z5&8I0{r>m&AOBq7Z}<!T{0YC{=f~x80r~Uw"
        "0Y(+VC?XnF38k12N(iQus8l0}`YKe;vKJ;uyrM<oU1Uk$DvO->`w7dEcS@Mi_H--WI_&Nl<V3qMkFrJi5lKZ+kyI($IB;1JL>XIp"
        "n3-|?gv6i){Zy?A>l;76<+@XO4)VO}T;yU=RY?&xvV|3ChE?U&5DBN8mt>zO5rt$d-$`r|wl)g^OZm2#xh&22Vt!*r|5YVve=L7l"
        "M-)QEWJ0J)WL6*ecZESEx)WY2D>gTi<DD#+gt{ZG?)t@q2&6w&J?U4|?Gxad3w37`6a`U?&jcK?ANw#~lx6iJbuQ;wm!andAFj^8"
        "pW>vO7dCUDe5`1&EG=5fNy#}asq+}W?8Fz(Nc2B%wa}UauWU52PtY03v2>ev;&+h%EesZzKx3dBKsf;ofEniC{oeJ1@x!`frYBMD"
        "lG(W>{wTVL0iAF4b79~B<_t6asQXvgkeSE_`26MR#u0>N=07THZlYFaJl^40G}#ctQ{5tGi&2YDK5~v{7t0)=v?(w4yx6&Mz#kNV"
        "PoRGl_6>iM>0T{ybTY$`DSjosjTu{3F`nuH&H?sJoTEmjQW3l9L||R4)zFRBqLrSmgPI=J*QsU8j5*yL%L3^wnxH;;#*872x%8=L"
        "6>%qVifN2W42vlxip`pw5RNfbF}-ppIvMkvvGFG3FMNc_D;zZ*sXR>H%9M}QB}!pg^;V8mnCC6r$2x!pJK}_s1mHwJ^*HNF@XbPw"
        "a@v}~$FA>y3qj{QpCxwQ;5!kDm0MufN{(4Vk+z-Df-#2Upbi*>hx=6XnwBFH8k%HUf?0j<v5lI2)HeM_xdlH6kk$olcP&{@46)Ro"
        "nr$g@-!*UVWY$sA0G~EQR7RGjxTcf_I=fc=*r{~1R0zVCRQ}nin>5_nPY)^`b{H;WYW6SXhajm&1U}jbbP74&WL{bM5(TMMa_p)c"
        "@>EW7UBYX%;-c6+uvO09UZkJzaBiW?fm(YjfJ$C$Fy=y`;^~Q+IF!T{vewKSZ@5mz`V|1(=c*ATDymz4J6ETx<@-=%yr1jM>spH6"
        "8@yLn$J22%erT@b{m7hXw<9>MS?OH8W;nm18N^nq8wuntU?2nxydz*7`z&3$D;ce8^=%iC++k=(>)^p^yHo6qdA4fRm#f+I#20Gh"
        "BU2L;nyKfSr_9u8an<v4riL(_XhgHCpZ?@Gp+tO!Q%Ov&E|HSMm6!oGGCJJI$&{|4bXMsdn=dK2_Y6yL@8vD-9fEp<k`X2&PJ%c5"
        "Gw!_wTcEHtYd`cnY(1}Hno>~FTeuG0&S%^Ro8yE(LmP=%Kec_p@FM$JU|UM<)Bpyo)K~Ug-#mj~xdTrRogO$_zis7ysP=n@eiXmp"
        "yPa9t2kwFSM!kUzzp9mmRjnfNT=5+^6BW7RSb~)luJWmp{Abck;_>S_GYmdZ$>J2$9|(;QEu)lmX%nt)H+pC}rPlYI{|^9md*<Cn"
        "aN3};1jI!<EbXXadBq1;6$7{Mt!B_Z-c7!RR84Ekku$`~q4&p?N3M?{0Dy#lfWDYgeK0@(J)cjHA~3dsGB%dBaM87nkQbdH$HDa8"
        "l&!DFVl@UsNU_*vYo1mpj4Z%SEHWx^mIju3SRUqwi=xpz(ZTfi;FmVnjc*mgMo6K#x88p9dLmtHdi28e#Fo691K(Tvk+M#I@JDL$"
        ";u+B2t{St(5mC%?Ts6*5RgH0_nNEj<HX>{{X?6^FbUWhiEA+a;CLuPDuxUhYvj`fRW!Rlmj}Fj_zob?zJt#h?$;VdE8L*_B)g^Am"
        "SN&=@#f%r3W!X+2eUp?qzXiXFj^#WHLf^T(weO5$M!P>Hyoq_dS+E(<e|>c39zPp;4sO?5TWL+DYp^s<PS~n+Rq(Biqf0w>$xFc8"
        "rL~qFDjRU;pR7qf$Ot>dhG2j-H4`-((r{Q&h!(P5V^#NDRkLy97kESD>v=kU55k0@92iUpm=4_IAeIOBoa#l2LeKSs2lMNA6_HO~"
        "MdYKah(YKTt=cOP%fvUgO>&bvBHu>j#$!;JofT$DSRRa@J%)IhC$?uJ`<ZO046QKqt?;22t&ZB>4Ds&SdJsR$S?A<ec_7>y57Yzb"
        "t{^vfj3ZxpMSdMm?ev&$ZLX%qd&Pf0@>h^<jHk*I=@k~#dqItPeBe%Gpx(H-wW$~`mS<t9$My*tB<Px3vG+a}7^u%na_<fKiz|0+"
        "b>M^w_>+!D=W6|{ymN8|?MJK|iLM=@vU`<kJ**ryR`A5gBUAx`>GCd)zTiXGYgk`wU>vBOEE{VRRC2gogCy@oXm?W_^3wX7>Y5kV"
        ";jG?&g9oUu{{hYh!|D"
    ),
    '3D-ASCII': (
        "c-rk+OK#gR5Z(I}v&f!F3bg3X2O&UolU0wv!&fASGjE3UPqvd3pc6!*ERk;>=dXPH{doWLLT|6-_x%_B{6VkV&)e@edOf@ly&U)b"
        "Jsv2fMD24t<VY0zAd-x>+jO)I$|$AC1ByAN+y~`T=In!9?(Bnlr`%R&J02!K?G}cn2>bHHKmjj*wXIcf#ePWqaFH$9`O2_~P*80G"
        "v<EUjrvgpqWEe1K>T?Pw<D4KmQ)rnapb2=b-ysb}Ql1p|PGr*kE2}2mLnfpzSWzaXQAlS&K}MzlBrB*<9>%H~6{7MUDYS9pI)^eA"
        "nd?Y6ts_yI9LreMCgy;8;MC&uu)z{_FqEkdb<&uS5S6wjWazYPh(dd|K+au|O^!P02D%BOYi`drKKWAP#o%llI_T$&<ob)2m2zal"
        "JJZH8FCNdXV(Is%jg7XqqIT?EDy~9uWwdHuW3uU7Fp~{D%`PPPs2Av;$hI$^eAT2kU*IdJ4s9!VCvYR&p-&9%HCXKn-G<6(J?GPM"
        "9#T6kYgINq*puhpdc5g4yU}<N{T4@cEZrMPc#y3acH!cf6<XkJVgtC%i6n(qTu6^*JQ1j$x@K8q3|e${Bw!E@B>=R!g-{(x$hsYY"
        "oLAg7EHtf^Q1dXhhV6vF&?92uEF_p9nm|D|PAI}h0fd;>Q<EH>#xVjD-jeEEP8#2a*gHhrou+LA%abE9QVhN;?||x&B2bg{CH2|$"
        "0t0`B6;FIMFIDFScSk|PET2=4xz&z(w9Ub<b!M>F(-CSN-0@tF1YoiNd&<E8N7Z_+?V9k5S)e3<kXu74ty-PUEaTa*gJ6Et*An3~"
        "KCZfKG7uL8Gz%fjLr60bhhJ_NwG^GKs{|~mx(AS4(xiPx&Uu~%yh>Ozb4#9Ta0N&MGzmDx@aWetNMMxE!VoGOm~JQ#eUUg6insXW"
        "GbBC2kjWV`29FAHigTwSPXYEI{2e^a72yIV#Q5=v04;+>Lrqq?r45$BR4+UcAUR)cG6O2vpbfZL5<e8-sSFCZR)z4!1=PEUfY2bo"
        "815`6F6&mZ%sf^3hXi!YKfl&}_`V%<?SL{@VbcKNa^-Fsa;68ThArz>1@B8|NYf=b>6@e(?2QE$Z;tI;W@uP805$qK@98&aWM<&!"
        "-tqwNusuuDX8lShO0ycK+8Yfj-};uW($Br!Y4nYz7BJJnGa-LU?dgkWN-{6XD8?YdC#c8wZma-63(tZ>3p0@-*;qkjH~nV2pKYwS"
        "d-P3%qaG<V^*C^rX@5?-H~lQNht<FBE~|bv;yxz*{6G2@Nehv-Q0!$0XC<zqhc$`m0r?KljKr&6DcqhSe6sCgu_f5JD(oyt&8kt8"
        "yD72XAJ9^;PZ_0`d>25ndKigpiZR3!mlTeUyS<{CQ9I@LggWkuJp<#0Z_BoF9poJocMl@5NUfz6sp)$oeD<<v-iU@`+rlA-#{;Tg"
        "$==uQo3yB#QMZw{0>Ykmabx=#ntl5G7xc%yXLPm6tn76Oo7FrA?PiB_uFqk*G-NyZ5M?ILao!5N7c0SQaosY&!5SX*JyjMbT@*fu"
        "lkOg7hm*dJ!qq=o{iD@CTK%KdKU)2x)jwMOqt!oJ{iD@CTK%KdKU)2x)jwMOqnq{e&i~e)UHk{GrzKY"
    ),
    'Colossal': (
        "c-qBUyRO_g4DIt3gg_v>jgj1KT&BN`ixhE}7)GiE?*Dgd@hv|k&rEiT8F4IG6h$6Bq~HJke*1;+^(*x7_67cVgD;;DmJfb=_V2e("
        "ec$8p_t+21Hzcrs^0?wVKL8#w0Ci8m17Mj1;r4HS|1zh1;fCzP2cOy*!sm(<I*;V1-At2AU8a`yB9uNYG)Z&$n|jhR(<nU=>@A_^"
        "LM@$H{@im&p(pqr=x0UI^wZLs`88u^h6q~*6EaW;-T@+*QbMR%Uo+_f0Y+i;n3*+eBBw=6{91ldUOv+#tH=_|U`WzG4<Gvey_3Z)"
        "&l2&GyQl<Z4!}4*KPlN+v$sQM1}B<kU`;1IS8c8O&OezU6d5KJUw#X#UTlmkilsA&g6t`;!Nt7O$m6p<Eq~>{6L0Ovdqrx~mkp5~"
        "OOd9}N(Se1?277xHU><wCfO*_ET)+)`3xyPUFkMSX4Fla^L|6_G~vO#2c*!xmQ8B~4H*Bs8oO!bCNJRZd!<7_Sm{8@zz1~==el83"
        "3!9^v^a}4~(X-n;MpVIwD!79xtR{glR6*IwKtL0!prQ&Iq=@qOjF{%!Y6wz8T_-AlU^(pxkrg<<P?(VxYgtPxYUp~N&<dcsS-wcq"
        "H3k$oq5GH(HD^XxP{T+N2>z^G;Q8{;`7sudIO{EH$e=)wcf1{r=PmpQ7U?49>_UW?$x}0t+e*yDf)J}90{qDiam|mv4Q#E!4Ftl)"
        "h>V9UMg+K$Pm8)<nvd}_Tem!k@$+Q!*@!!$P6VBRIT&SHB2EOH0&gDIXAf6GyhggY3>ngk^tlr5jJe)6Vi4lF`g7$<s+`NtD<~Wq"
        "8r-NVovVoHhx?<^f=%#X3oQ1)5I3EIZl#Y7rrhkc_gDpIHHLU*AZJUzSQ1v_b~l@jAh5%50z98H>?ohK>3P9Y3NT1-q>wXsas3MP"
        "<jDf(3L#nOXtHG@2P-jv8_9yKcaENdDMPZ^j8G{H$|c28MUfn<x6~nkqC5+ceM5xoB2|x*BxfL^vz&lF@0~qK$3c}#_yz8#h|%^P"
        "<qO=;RJHIrqI*O>#m>vS*|n_+_J4a%&c|%D=zrwLiC5_l1Zb#?{#)f!sTdnEHW7{cg?Dn0JJ|>nJyY<eVSGh%*!=6si}@b`^C<Y)"
        "@QVq@h*=#WhqCXbrcU69XzrHZqApivSVagG8LB!zcv5p?vj<J;JZaHS)o#*+NY!xa=AemEnsJq*&@rmu=#Gl{N;_t@<C|)ns4`r#"
        "8<`E1R7?Y-Q^96oUSnx&<Cej?IzthTtbD^t3fB||AzcYLTMtp8U@NBuZQh#gTwZ=mw8n0ai)yqTCQ*%fhoh+am^+OY8oyXn-wS~F"
        "9s?+*p5ebNK&2do)|(F)q#Tg6Ld=_!o|Vq^o2WoF;B{0JjJ6xupE1CO2}8z4q}MbS@*^D2F!WmXHd<NZ25TidA0@rYqquf&v>0<h"
        ";h{iRJq}WOfCGfBasf9aq*OR_5Fe*`^DI11P#j=2I`T(0@Abx^Fm<aye{$^Sr*-@JLM3Zx2VHQpRTqM4-9!!<0_mk585BsCOQ^X7"
        "Qso(HFR(2YHAzCs-?v%~!T4h6rb)Si24)$Pm_<H#o1{<&UV?k5Y2}|#33{N}6!G#p#T>OxCL>3)4B21f`ttXBYA!<a6#(?qN2^#N"
        "?L<EjtqtN;V-K4#qDdj1DKOn)nU#M#5$4sJC3^8=B#eo?1u-7Nly?TsXFf|4(UX=rM}Zp=hvY!rIzDgB8n02fHesQpBbV2}*IXxU"
        "UXqG3gyo@x9RE3LR2}12t!`msqf7g8#TemCMFdJqd6pJ$o7ZxUrw<5#R~Ml6Pbd^taJ2JFzBYgc>B*4#vVp-K8w3>B-^JwQ{a9>O"
        "0D~>xNB0Bjqr6afD0Y0q|5bJWcA??Y5MHK^jV73OO|a`i#_}{czv)P>G`HT7+<<e0ljcQ;q{)rjVc>heRS|NXE>s;Rz4Tq95MGhz"
        "^FMf@EaT5DQNlsv$a{=-7crIj+CA@aX&x{0%7@cwF?qr5Ns7Z1x)0E3H1){Vgj_0-)v3d772;{grj!3<p?oI2`@uzV($NKV{PMz!"
        "H|b_@fg!l4LpJllni$6`B%Ttt<r@~Yp@nT|7dpFm$MY|_4Qc;*KXg09l_9Q9>GitNX~QmjUw7WUPHKh56mCZ70BZ5wRQNlFHL2)F"
        "av1>ql;#RDck*YHiq5Q_A=EguR?0SAorO2#>;D1&yz#;"
    ),
    'Georgia11': (
        "c-p-hU2mL7@;$$z4<T`DwcER^)r7HSDZE6=Diw1t%pzGaL1;&c4$b)Gp7P_bKKcuW8NhaS5<^$h(AD)_)&2K>|Ni}3s($-TZPo98"
        "s9*Wt=8t!&Qt5}+TtB>1`199?2YjINoUT89glm)0Q?04pwdvq3x!I2WQQ;pAc)U^gXJ57-P$~a^ZA(QHjsXps>Pm}YS1JQMbKm#C"
        "|K$mk^o*#`_nkc8C-!am8jc`XU7U2{5qLmr4aNwNmGq?PS|VcJn<pIBB!pa31Auu_j1OKLzz!Y;@-+j*kh&#2yKUxYg>(*~)91EF"
        "Lz2{c<0ytbP}3J%71o@CL11fP{!Wu1#@M0nZcR*bjRaM7n{p4o(BR96_-#sa$p>fwT{ex+Nzb@z0@iz6M7#HJ4xe!HJdEGLQ77^E"
        "622f--+{@l3BwwWhS8(t9(gfv6#gSy5aFz80D7Zl@$ow0#6RebN|#;TH|1^&-H#oAVpFp0Z!E6xB^}&ru<U@=>PY?UaBiAF%n$EG"
        "9GRExv(u-#5FC(ePPua!HBb_y(E$%O$C_WyvQvgp`x);?H^HO2k^aN|_4U>D_5DG<b%Rgr_zv&wNWoXPU-q)-(Xic%#c&2=a?C{}"
        "7GAAQ-Lv)iPGXs<u_P?H_Ytla#&Kir!Z<lIFPCV6u37BdbXsgIlcsV5ik%y)t<c)X(&WcN0c##edMh`DeLQVomjL=$qJ={xAg$T)"
        "49$*~lw0#GHnk8FSg>q2Y@xQ<B7;A1e|pkvINpP(h?>5eKLI|QY1W*~c(!@$u9B;kl>FZhzhKw*+_FR8edpEa>w9a+i@qO<$LM3L"
        "XGXKjgyor=y!C>RTN{C-Z-dR`c!I}TZ;WIXPw+zK3eUh>i=qMtXymm8G_&0TLIZ$^3ILJ05nv@H;EI3vMFYO~M+;EwjUO9qD!Cp<"
        "32RO_h}1Tnvs`9#m81h@VgE&B6uZ9>m&-IKFX-fZ$9}=iwF4IK^exS&r9eS!t;H+^^(nmGOH<l~S9jN>wrgHQLL(ins)AP8ec{fE"
        "R@tyy*Lx9r45q;9eM#i6nf`ezyx8)1mW8*agfgs<d&BG~nFTa)4Xu?gvM|g7=Sh`U2x@g(D~^4V%{meZGLj@6JCe!ZeBCF`_@V`*"
        "(04aC8EYXmL`tP05!K_PiN+&%8`OkA^Tv*S;2g&mFg?@%TA^6H${A|SBQa=FL~qS<zGZ8$5B^yM4$WU}wG%=NgIxD9Sest3cc)Rh"
        "I^U(u_5J;|4>r*scXnlswT+r|M0aSk3XDKWZMcW>GEOJhq2;#}aAtI&XQV8<mcsVWro%sD^9kx}Fgf{38I@I|#-?7%=zn=b-?$`X"
        "X_CP|OBV;<0o=wZXZ01ps1WYZ+>m;EM`rpNigaM}D)`K?Kmi<r;2j$alZ<JvNMQ$xKz(E_Mh;8P6#+Ic=3!!}+>@q`hG~)(3R3!0"
        "cX2V<JcZ0Y=eauiVpnF?AyRnh9GPm!jljUEfQi>dZjJF0Q`sawe7H3Gh;}ESBUkGZSx1RBCXDyq4nwiy4n#qX2}dgoq${(jc`v28"
        "W60qc;zvg5eFSx>N1wu*G+qokWUU6+2FBu8NS71vjI0f)fp`tja<b;*EI}$ASRmyC;<>PIj-j!%siC;3p}dU*E=U|L>thy)oFlxI"
        "xw1~zF?7nL$RuI1fo!R1xVO>kx#uzLs5wkCW=%@C$B13ZV_u%mgc+h$%rrS_w!O8Bnin~$N!3RVP~9yH1T;QPMbLDKcZC#CLJ&_H"
        "T(LP1?>0Ojl4L*$dbR3*jA8+=E(;)Ozm@8rtsxSMmM%I-jD)*g>170+wRpwQ!*xc;;+`4|>1))3?8#!GM!HfZd0MQO)DWlSfD#81"
        "M~6MbDnh-DLB*Ly0GaO*NXv-OWq7T{IA_=LXK_@{t~FHii|ks%ObvF;g4UCYqjGl5Ix1_|x@4`Rn)U|pY=#=)0T+Oh_;oJtiX6bt"
        "^9KJE)qo4H0ga|T9J*|od$-wYY)S@{Qv=fZXTIj^-s8Z<QjczCv5t>3S@1!dmTE#$GaZ~;WFnxbe_@WBn3Amo%+xLu4z`npY4#Ks"
        "0ZNJ=T%=KoK?t#i3X-!P41)G%K0P)iPNIfBdMR9c%j!z=6oH7;txFRYBHTbB6IOP@ijy1yUD=yZ3bkC~cwE;}>K5Q`kU&-+(~Ei4"
        "rPT6VetGI21@F^^oLkV@g`HdA<U-FZ`0~PM*EQ?1IA$S~tSzcoCw1u-!yYLch<udPYsjoM<D9&VLl;bHDQd(ueS2l-;&FsbP%MUQ"
        "C}QGQsN?C0hE70UU0-O{fQ@Yd6`3i*kD9cB<(f8K=cc6UydS~ov~7|R!ln(zXU$sJmT{Tc%kTW1LBLx<REaLa9&Hq>+99&{QiS*$"
        "0v&mXBX<#1twa630zY#)*lBV!c+uU`FwPrp9{GNA^Z1DO8#+Po$j=>f*4$kqbp4E$FCe<jGL15SN4nWk{f*13jMcfz$}I=Ik&?xS"
        "clj?NP147HD^1?sl4b*k!}e=^FiX-TF8>DLYLHWm0NqI>z(7<au|AA<9>kcs$glj0p#X<q%R;(ElsImZ_K>>WkB0}pd=>49i~7=5"
        "{-|Pfi4r#Y(?J{FLmbFEfNHL-0J<o{lU$-E$A&g~r`aJU$w><HDoU6=hFaek3pQ-E>~kjkONW$@9f)k661KK!<QM`KT9brp#L&o7"
        "-b{<5_vw~1+v6|4c$-DSO0qz=MCMPqDE$umO4%jLV{xrHednVo8;KXV3|hM);*>XwHGx>KCemvM9l<MueKsu1OhHT&r6w}jGv3VA"
        ">0Ob2Sgd(PaYmv7ZBKTz@c{z<^1K=U&jX#NLPXlWyvR|(be!-x(qO9P;^w<hBD{k>WIV$Rr!LF_|Le4Pj!f%oS{Gxv<$Z*hh|rWE"
        "O$pHDzDTqt1?&PYbH)72iR?e32P^D^KJJoTeEwr_{3AO35gq<GIs0)wSzQb;Z8|6padBi9Zz_ntwE2F)8wx2vbXe|8apa|kCv>+<"
        ">i=~TIP5}=P31A8ggCwcP7ZX^2l~i-rPo5bwEEDwwWE8<#ygX~ITiX}@>=IxbS$WDsj`uii*Cq0^7+ee#2%jG6QKlKawplG*BZ9^"
        "e(e10iIZ6;DFi;uJIVEW83!**^^(y!bV<ENFITCA<cE^5SHtvhm>pJClpOBTbydN)vCo+tH&Ul47pIs{EMQ_C>fCY`+vk@OyJ7Ua"
        "nfnX=a(GcMovo2wY2K6`Z%NM{eg28e=YAKkR4#aUcxaP!wH4s1D;EMXUj~SZ0G#a8_DR8Mcbw>7umYHa$-s?2R~Al~*~;ycab@Es"
        "X^-Bb`BS^m8+ACHhM|?@mr_VrcZtsa_RqfB>1Xs4`uPwu^TY!2jL1@B1LMJVIrb!log?sSQA;1_Ac)2s8Ry&_lI8Dy_&*{YKkx"
    ),
    'Univers': (
        "c-qYy%ZlVU>^@&%OhINL6f?7X@ZOre)uAERuq+dYFsI%BzpiCTw&XY|CsoyS8Y-6Jhb-%5+5PL^UqAfCeE)9#G=F?EzyC1bzFdsC"
        "ygl;!_Qizf?Td4O+WNH?e!b5aY!!d;#l56vsU<cxFkEbNrjW+qkqq=7e=Zf!d*?j<AQ8@4{K0wbeckVQMTP_s4pWD^@v~nXJaZT*"
        "fQz&7t93JsG`e0^o<`^4uji2_>)>zhnSW4u;<i45-VO1q9pgZQjRTcrRH!T_()(o&Bgdy-a1C$9+#DgWU;ArbhAts;-wDAefk0G5"
        "gcptzx+g>+6vu%PEj6HVE>%+3s1g@p9*JQdhT*{2IEJ7vu$IL38mDQ-xev{NvH^PzERJNsqClGgRs+`#xgVOW$zH)iWsF&{OMDwt"
        "Hp9Yyz7WUoq^#u__$IwKssK`n0*EDO9!tbEF}*N`^VBTLWo$MPPYlbdVXlfoPjVaIAb;^~q6|pE+E`o--vSkJvNsyohJ_1@W7!&j"
        "vPYdKg$HeqN>3Ue>UPsO^Tcl`h&J_iXZ$^BtU=yO&2&jmJy|75W+qbBjO5vxe(ypI9(WTmff+7K5HC}zC&xKn_Vn<q<(v=nL%@-j"
        "V|SIsnqPbCyhtqo8J4zcQM|#AslB$7=_A6@W&&~f<8iqYb}-6*vV>tw+ktao_3qiYB|VQG&#=X-V?ejlgmL--1ui5QUa~SEq+@bR"
        "HQQamWK<S<GSIwMV!4i&^str4f_yglUG`Ee|Fj_NcQ5Gc*zaE8L)J^L*0)^V{H$EY9iM%MO7Gc=N6@DA=gJ}~!M09D7Ovut??x6v"
        "M-~-8!D0$Tj0G}+LIXzRNWrnw+Tz_ROsmFiHN<Laas-n!;F2R<^D{ijdj?jAhn8a)kYILz7iBj_bXPmE+W|QsL7<?@xEQxUmnaro"
        "llayzP<x(9&9O33nRJ}Ozo$`Itk~x_oz=iysmO{o;JKMKAg>nV_NKPy7k`UNCu3H!eWcfHMvWcf!xn?tg=ArTi$p?jM`pZh-UKx|"
        "+{>lW3-7$S83zr=BbJ2qF2x-_*Y_@2bf3j6genyLQU?j7@C-rjI)c?fTcgIhX%g1uQu)^0XG7HZ@U)Myw~<wMwbfef3=IZFLgN-^"
        "Yk_qUtry|?Q6rcP7Z3}s0&O-MFLU+(`KxD!fzZZK&B3=Egk3?aqh@CuCMdeByny>Fx?GCIaM7%b{W;zjnJL<>_$;L9IR6xb*EihT"
        "lS3;$tDQe`*v-Yt2EwL<<aOxC8ztQ0Mj&q_B<^LIzO?0x4NZ2rw8J$Dxl40XC_wVXj?!ws*)#W%qvY9~V_H>cUHDwjZQW@nn}thD"
        "7P@$%RdR**|921^4<<y|CHN~q*Q8P8A4c0%Hq7^fEzRRz6N02G%F2MHvP)Sb28p<`XX9a!^algQlrdFb3y^lY3}!_$_uD?OVfa8Q"
        "w<E6)mf<v8A;ZKwA;V6|a8>tOWLVNsO5YaD)|cUA*c#gxlWGZa@1^?JzqM3<F@hfdG03@Ig{kAlqU;4JYB{p1qm>q^Pk@A2!=Rnm"
        ";)Rb6v!MtNF-0=xj44Iiy|z!IJCp-%)1#+?r|n4YVm2}z#_{_safHm7qM*4+uow&4oW&WjQNx9d0~WPj3jP$6@ufCQWF2ZMvFY+R"
        "wyd+3OGcD)D9JA<FNmH?dY#th$sN$<UE8@fo;ogCyV1>TpD>`Dxz|yES%m>dvwcsDwe;D>EBw?)FdGa@$?VasnLVy{mLJG$WldA?"
        "E~Hz>ZnZ%Ce40b$E5#`)JmM5pgx_3mdUi5qE5}vt7GDjw8;mpY$}xDgPuSfiG*~#GS^J&hBtjlVNK877%orRm`<uMgWp5)7*U;B!"
        "PEW3@CM(*{sa5rxh*7hxz3CY)dR{Th)l*ebO2@Xm^~ojHiZpS>0%D2}8qtZ8WR54Bh9%9U&Xla#o$~glim=Yb;Tz{Ovq6L^avp3g"
        "MSel%7UH>AKJ6x3oh+6(6g%OLTL_MbKCcPne!M#Wo?OA=*P4TkxUg3+h0G~OFA2`!G@dl7J0KF;9dC=^a`XCxf*!iQiWdn_BX0<D"
        "{<BDTO+%_e{aKaLf+|_;jBvSf($jd5DYaV`X9`<PQCZNiv@V}0!};gP=N-4?o^L8UoiB8~1e9?3{d2&Dyo)<PwYxJWUx7%1a=38*"
        "03gJ+!!V9?tVk(zlF9fjX0Y>qos9&9zVkBJ@!s*nqubve=;Is_pJY<<e07qEx3%c_0Yc_LqpL+B`%&H@2sua$^sE#}!7Sy%I*;Ue"
        "4w9I<V+=GK*`PW|<k7MbeQ=FU5lELfi9*iFY%lT)892ak`%gaF9K<yt%j<iZfp}Mrrzel0ldpJxckccDJ>3w!n4-c~aXt9k9iH6-"
        "dFhr@OE=1;^S)Oe9?bZR7w0}kLyMh56>JbNeNj1Y|Bq!gwL4rJ;3M9&Pu>)`S)QQ^X59h!>P!UsbZiPv<nNrBgk|+24LOZX$IVYn"
        "2Jc7p$FNa$U_v12r3m4P=t`2r`yloJWsfOtFjEzkm|mn3kD3>vGI=hda#pT2`7S!yXY<mXy2|u0FB<~@H%TgY?byefYEv=QU4uJ6"
        "?`0()vx<$YGs1nS{qjg7pnU%=Sh{SFA&HcPGji-7%93Q+Yr8yB6#HfEzMH&$+RDZBmKyzeUjLAJ$ZDxYX?(TuD9nROxuvw6O3C#q"
        "V`H7I|I<p#vD9;X&zvr?tS7QKevA3gMHW3RzRIHWQIt~}hw3C>?0bwc>3@4L13`BvW98q1=zbMDdTY`RMzXEdl*H!}pV2veW3*FO"
        "i8}SaF^7%PI$MRe$mq&4-;PxO3$>NuS{o(5q*^Y|scLg5XBfNxnr3Ll9jRthbuSLw52bSsYW6v^>{P(KF6RF$ieg6{Ejg`NZPtt1"
        "-*khm;g&`VB5;1D|6K4n+W{Bk2di$#)9KMu5O4njxhgV@"
    ),
    'Star Wars': (
        "c-rk*$&Mm95WVviaj*nPke(@NdM>EMSJcUBGnYP%G(X^v@7NvAG*mB|TLMMePCM2YOH$wd{r2`xg1_MlyuHDnf59Jz1nK_z>wbV*"
        "Y0?1N_EIZdPPLx#0{D^>_{LReaK_mOzUgLy$pv3`zE=LwT5ZtqQ<**xdt3_Zuv*8Zh96J~AP}Wpm+SRX7nx?g%g0bWfYTLU54l_P"
        "4+$-_e3m^b2hqxM7vOlk9)%Pu%%anVnn;7pWN>p%Y={5g%roG-1<CMCHtSA4#8aN}A)034N{8-ULFQT2U?*gMX1S0ilw8O^r}_?X"
        "V|GZPWIt;@CATq)menEaZ?z(xXVQ?Lp+GLgso)8g>wYL83N<nJ?83UKR#nx?1$~_PWPt}9(S~M!ad(CbEKrzXM!%N~!SUTjhAM-U"
        ";i-d05t9Hq1wG%tgF)6t8{Off$}dJKznVsd?@Qx}HAmQRtmohkFpnryI%XIy3OXHt7<s~xz2umY2<P{DRGWJjy_GgNO2k68wCROY"
        "L^x43q?9er&a#t(Kt#$__f$XK&pQF9fEF@l6>hv%ZH<X+1s80Hip|1Y6O4*SP)V98ng%}GBc>V`O0%Si+*p>Cpq~gONzF~sZ6TzO"
        "?x>UL`@|_Oj#x{zNM_$aUPi)+6U|1qDN<a3cTj_WCe&*(c;sSYx|A%?#zgEqiV6yfG)^r<p6sE>^}y92(h%ZsxgTzrGbKrbq!>wR"
        "hjns!7Uxf5AJ*|_xtrM-Ki+~_`(5Sn;Env1gO+3|TR47b!Ln$Pa-)b1kge5SOnD^$PqH@Gr7E(JBkF?^HR1n6iGU>~XNhJ-U_8mc"
        "BBQIFMNXUJ^ebx)p0ui%LQ|J2$+po7D6SZayn<p`*`$Ku3w1wxjiUA2W?jzap?h$^Qb8Aiv}?el7qawGsDPyqVEZo${?YLvCTnmT"
        ";|s^wYAi-uKRlKeqq&GRZ$zveZt6lM6OAEtPF5o{qloYe)Ie&KpliT41oPGCP*<|Il$36p%NI(`koiNof#EcS7K8<HU(?RE*(Q5d"
        "q(5(Shi*6IqArahqz9l+)W1&YA$|>dj^OK>3i{}zW+y_0#x>jgW`0==@hIUcN`l>j=UxsC%M+kjxUq1H%29H+b>V0>qhW*gb6!}a"
        ">>BA3>;|Qe7wil>69{C>3^p-o3K&C%7%=qHj{4!;qo;*~B)$f<jon}xg9kLmy387LF6q;`2!AB(x%~P)X3zvt3qKDlcC1Ukt0~>?"
        "Qtmd;kW!CjlVM`eD6W3{q0GDjkhYvuC*#z@?I!h-BoCZIQkzXB&m|Z*CHOQg{T{x|En14y8l*CpgJR*QT|!ac*wb~fnTlQXVIXLc"
        "dP`EhL)h}0a-H#>N-&dSD$PbFusp~eQtlFbc!%@usgUklF66dh-7@@&NoBJuBp&Qia~_+U)PO#e{cP<Tw`ksvdmGMU>n3uY6zlLH"
        "q-3?Q5|SBWX}o$eZ*Eh6LKn4q^9A0LLT`e{Hn{5+_&=`tXb<Cc(RJsqy`i>M)M%o%U$58FAM;)eo;-68qs^1{wk!8%Yv4x&EG;j8"
        "0@)kdy`kM3+P$IO8`{00-5c7yq1_wW|DmB>|0Juxvj1p!U+OhU|KIc@g)Y{+$*->rV{3j_BJ^KjC657E3xG!DZYG!TBWVA>=4`}f"
        "{0;#~1@Z"
    ),
    'Speed': (
        "c-o~{Np4&@4Bh(_T-2fr6v9pe$*N$~DYE(i-J7iX2s(UAoJ4A<`tBG;zTik49?3^azyJOH_dgf-2|wWbPx$@={(6J<bGba;<O{s0"
        "UtY9tTky9o<O`3-8}Sc+0kD1Rr)@>Tw)0Bwax@$Os(+v>6nL-Sf(1p1>VWA0@gY7{e-N<#Hl0-v|58<qzE<sQK$kR#b5UY4i=b;="
        "z1I`<pjdA*WH+>ZZCRZn<P%MXY~=-konpU(Zr2R5`C5(T9jqJ<EKM!Ty4SxREcw7$gg__2DO@!k?&#zpc~C?diix1$6e`&~SrjPd"
        "Gg@%X$r%^|3r%TiS)zobNvejG9EwL@CAAV(w0H;{)3N;T1W89MuBfAeyg*6tVc#<i^gcptAk#V<00LC&Y!JsK1vEUIladPPnJ^gH"
        "mETj<S)4O)Ik-i2dHP$?p!_Mq9o;NF8ES;?y%_t^g^^k@z%?rdT9iG^W1JJh#w{8%b>!gfW`J2Rh?!hqDd;r^r*X!C-Cp2KR^r52"
        "$=aZt)HbJt#;KmU?a@w&E?>@Ar<}w{rw%AVaq+5oVvz7h^@1^kgAT8ZvEZ1cNg3Mn>oU@Ta|lZ$OHlzGdsaSzy@pWzU$SkRjxzuo"
        "s;w?R)gO-yG^7>a!_z9qr(q-<S)~YDvSbc(J5Y&rnb6?*%(>B%!y+A%c}W=|mo=GDF>gL-^o~V2tgSGh!3*@H6;ovjkyxKGOI}vy"
        "N)UHaaB|8-x)n6zB57r@eT9ql_W(!0Lqp|-kFV&41tRm5^<ObVQQ4DTHwbNFl8~Hrr2F9m!0qm#srhP7aQ|P5NNqpN4@QXILnjDL"
        "H@AHQv2+ws;I)v>IEJ~Rds2EtnqYKax~TFbB7f3om-Q05r}fW2ZuFH&|A+n?xc+T8hX$xk^jr^*722oCK28ST7Xv__06N0}o=<1+"
        "Bmj;z>JlwA3tDkK6Op7q5|m{{aW!k_<m}f0^Q`pDw7di_u^}W6i$mLX;1{Nf`I05&XrWuQyvRZnSZEc%IMG`RZ*!LQW5dMLhOF(c"
        "bXw(i3R!ZX>#Rgo<T6-vK+vn8Ty8@DUrqNXC;s8KK)==(s8I%xk^kpdvA0ddfV;dK=a9bTaeFsCX~}YLw@N`+j_E_+uzocb+HFxK"
        "(=^y_(}!GzoBAUe`-~YV_U#Qbz!WBMeqxYg+ts-@P}Dh`m0-0~FPJILyyxb6a)WJD4*A^^ykE14@<lls>=z@QR<uEHC}Yn6OM??%"
        "g*TJ{(`SY%!<8Z16vme@U)=cZBwA4%^A&XV;%PJdHZ|j~)iY>mq2<|<P~uJUSqFS^_)7NRw7)-_gH}^wuvRiIW1ia6deXRU?vI|H"
        "y;*vrL{IeX;cz~QNK%3z?XTnPw{(R2tgy^SdnDVH%9t?A0pvNi<e&{zXC%Amx9Q1EucP;7YI<^OL*C-p_|i5#+2yy$>7@>>;eOD^"
        "=A{k~<cqRBEan&23*?vA{{Rxhy^#"
    ),
    'Poison': (
        "c-rk-yOQHL49w>%fGVX|H8UY1{>mjA-T%iYk$5ynO0sOvZq=4NUW=4$vDpNHCaC@O->>g~7yA09uTT2^q<_BX^JAf<Jo!;RXnU29"
        "Qp$GD54;_?P_1!2$y`tB4}_)Gq#mRl+BH4Kjp63dfHp$wfwV{q)qgJ(tzjrD5~__i1he|u&Go&0c<<LgJ=f4}1>)^$`~Aj9{it;o"
        "Uf=5nH)^)B=(+a$m89)M9BZwUbl7JdXtf5l){4Cv)S_<%PVC?0+J}a6{avjV7|Ru`0@MyrtI38`>iBBw4R{ZA!gPhzuGzpBuh#1u"
        "k<9?&c1o<(OXJn*Y}Hn;90xTk&lR8=Wn;QhtzD_#6aq8d?SNXw$hD%|V51preJNZVyBdt$Y9n0XSNf&BD-j!Bs+`wbHNHiRbLC$q"
        "sE3(LO0^ENf_ePVF;Po)Yah56vKTD(uG<i8C9(T@=gfe#jnt*oa4?<cD%+3Qyaln%ObUZ$w*ht<^?rgDv_H!b3OCDa&2V#72Bi50"
        "cOKPS7>qiN%)D)w+n9%KF2}9!8|g`<;7p2<Y8b<tSHy`n`;B1bfkxdJFCY3a5(aXq$2*n79=~+!mwPP7fwOPYry^A2`kOuJLRj}I"
        "HBY+inCNUaMUc|45HZW$Xt9_@K2+PG1~H3g9%!p?;h6AsYLP`SQSYgwO8p~2syd+V-4v{bE1$;ZYFq>One1m8RW_DsZYNfplf(#^"
        "fR|ek2{&ia#Ahap536vvHj5!TpC;jB5NI77N6>+3^GRW~$>hvB8_tK~fPq-{1sZnWwN6wDwAI|KhNE?y%pW%!(JLm!r0JR}!+-`!"
        "kw^vunK9%_CN!5ztiqwTiUDfj0CZMg1XGnWhFp|{srEP!h#@HnimALn;DO1}xH}&3^nl993mS6tHq4}O(#~ecEHgt6*MolN0MOpd"
        "s-C9vGZxPsoDG($m*ij2yP$75W9TX%&JrRAt5m)UXy;(<7PpM;PRsIGoI1EE`Ay9`rkkFI`>fF*^noG_okQ-JDU2V(R)jptZqyqR"
        "?iM%FE1=mb?MaQI!`ui;8pS{@s-n5BARAZU(JpC}QCa86mlv3vY2d6ZP;w7eX{=5d1r-r8e5N6um_bLuvJ5h|iF43LFwi2S?0rJh"
        ";MBH=*`|fLWW82wva@;__EjOU<*x{(a;RyZ@;zpTu9O*}&72`DVmyy=9xhG*Q4h#s`zas-!J)e6A@4lIuib!}f@_o|l~er60rfo$"
        "r6D8G$f%I_7|Md<*$7{DSlPx%EJ2`oFiZ!`BD@b7$@!4|4nR@Pu5riX&meVXlt5AV1<$H_(BDkSwRBtBup1ES>A4RW*}@bY(I0aG"
        "yZL3kKjxRA_n;IY2o1Wp(+E2<6w*dHie-@{-eV9zD6(V_0#|6~M7s$97wWD;)krZUprgyQ%c);cRneE(%M9hha6XmA%cKkM!Snjk"
        ";UI{1aWhrZ_CQK#dp@AFDMO%MS907FDzG$*(r6N-BIQAGHw-IoWz<ar%T$!mb;Q@avu6%8Mfl#;#no_edAajM{Mt4Aw|Zwy(Pn%c"
        ">4pGBNE0DXuE!yDU)fs7sbaG!ndNt1XN!@oa^fZ~3pAQ#g6Jf{V5<XIKGQ7QL6Ghs$am0LVyaKOq+}<oPR7&%tn*?!05!jE!7W4w"
        "X_qI0=#S5`w*YD&Jj${S6R9sx{2VQbGO7VuhmQ%pI{Ami9ro=sfSY_f18}iGO`#WH)6CYMvb)XtYVan;++_GLdOUa^4ENFYIpZSJ"
        "unbV%HVJ+_)%U1^Py`MI^uPNj&1}I+&7+DpY*WwXUD{3=4V+T)q|cso8*i}Lg2F%V#zc;VyQE9rj8nbR<zZ;lp#y0TFbBgo+3Vn)"
        "y^agafkKBJbxrR_aIOm?<;U&G$#pJ%=b6KY1jg?yf9!<dYkFd*Gq4`B{S<!;=i1@UKi&DKJO6a&pYHtAoqxLXPj~+5&OhDxr#t_2"
        "=b!HU)180%%koc{z%US_cSw&Uc{*xXyX90*dh9wa-sRxQt|Muyk6lfrZAg%1N80B9^#IT1=O>QyGeTu5ZSm2dJ^^O|^}(q=JE8K?"
        "+V*FvV*TDf7ab2;{p5*ms{aotnp7_"
    ),
    'Big Money-ne': (
        "c-qBUJ96G25bpm|Y@u<xb}CmMAA}iAnp8Q04j;j?4-n!ZA;**G2MNLQ?R(Lmzkh!HeuIw>_=Jy7c!&4b8|;5?-?M+eUx3T<{o3Gg"
        "U02+n?te1I-q9;P)@gJSok(BR7caI#GcHIEa9_s%XG;0c69M7=vV`T+XKV5dm|&WdiMC6pgcN$z4RjG<v{oc*4GR)704^Z1`CB|@"
        "zr|x{5*NqEF&M@W#k8~w6eeUaJ3x6Bnhd{hf)L94qb3uL%5_ZUN!ge!oDmUruM<H;g?5wNM@5C0;oB~7M|K~^5JKqsiQuXIqzu}~"
        "mBCr!hFFY{wjz77n+q=o^21x6W~H~#h=Lj3U*oQUCJ2MJLnE@&zlGtgR8Y%nScotH>YD^FTFnU)`nyDS<eeY8FS81YIo>DDBX?9k"
        "K!8)#q}9D3MAUQ)$zP4*HF50VcuiWG_6ZIB>w4GT4N-h*mw{ED+qVn>i8!%f1o_kqF2)RcH5cU%|5*9ie(yIOECn-27!R-?t%ESi"
        "25kIKTcez<nEn8s1F<K(pD>2@6XjhDOSbr^1#S@$2Vr!2ZT~X}y!Az4XB(8-=`O|gS*w?~*C98+NNmu68ZsiLA<-2M<b)<;=CC7!"
        "`u|&m<}SaUKw^Z5r8_zsHcK^IUbF76ktu2ckTBea79tB8>m-p)7NWRzh?Cr5L5xC(g?>K1{3`~gMd9hbvWVh@cr=otR#a@L`c=^("
        "lRThJDmp#CNGIqdQAQV0#$d-KWDHAVd`m<*5}YPwF1sTJm{Srb&PIb-0Ett1*<~Rjh-Q)g5e>7+T0W-WDu&Y#?6If=V^BTubklEr"
        "QQn?s`Ww4E^Uw!xeF+K7`Mnl*znIL5HFREJG?55+$O$SzsDy4$gabp-*^?^D5xJIQ9*WHwdQK@D(I%eZG8K4?#+NvbDuSle5c8I#"
        "Iua5j8`qBkUo$x)?OV!d($tNsL%68$fmH%37=f)B8DHkHu*`Cp%4H#OIMneD-6Ox2L^-x%5(_4Y<#NzHeswz3wgvwIH=^OhdA<eD"
        "W)SB{r~u_iaMcc=!J4Z`Gzh&`W}RMSzLiy;R_{jJ1J%BMU-r0mKuSfjgk6KqEhJyz#KFaiY^?C_Jq5w7c(6~EPSLvhrlkoJ1BR&$"
        "i`6>mB3Mg?0I4x6D9sh?N=G^orUWGh=_|U!nD)4WR$U20EK<NUDT^@WQNkp{B{prv-dbQ`^Hix+wSj9@o10N{G-DDi*MB=Q`aBF_"
        "w;-U$!Q4^6MJd3b;cPJHF6>UC!L9y_y}Jv{uvxrtMFrR@QAYID=A_fs-U$2q71U<eJ<m8A({rh@$9T5QR)zJTD)o4Iv2L9c3Xdku"
        "H_8MJBo;j>sFw`xAaOV|+8VGo+~^G=>N1fLQO@XZ5z$+)R~E2wpBU+mBT*L2%cbnO+HtE)AjR#oZ&sk^JoIcGJULv#j>D)zso(;b"
        "XC(m+d;-X`QvV8oV_+77I^#Id3J5@Q{u^Y(3W{6`eZNP!bgU1BZ*z*k;7>~NY<<3Z>@<gn%X$6}Shn`*@ns_F?svM4KluIgeEv@G"
        "ff{X_q(u&*v_5+n1Dr`x8{~@u`ugcU62rOu6zw?V)@gQcjC4!^pUL4n3@yl|!;l{OpoUrjLw5jef}u^(!~WEMyjVI}l!}2<%h$JD"
        "4yRSg0Wu?j@!08Z;(q*sF*!cT56@#`8~uW@7&l;lJW==va<dJ|zR^A!{0K5Eg2KSOT#xZ!zu#UAe~S1I9rLE7"
    ),
}


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
