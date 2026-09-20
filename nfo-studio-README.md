# NFO Studio 🎞️

Builds release-style `.nfo` files for your videos in a simple window: no Plex, no browser, no terminal needed. Same engine as [Plex NFO Viewer](https://github.com/PyroDzeus/userscripts/blob/main/plex-nfo-viewer.md): MediaInfo for the technical details, the same five layouts, the same FIGlet ASCII headers and the same settings.

> ⚠️ Experimental and vibe-coded: use at your own risk!

Add videos or a whole folder, look at each NFO, then **choose where to save it**. Nothing is written until you click a Save button.

## Install

1. Download [`nfo-studio.py`](https://raw.githubusercontent.com/PyroDzeus/side-projects/main/nfo-studio.py) anywhere you like. It only needs Python 3.8+.
2. Open it: double-click `nfo-studio.py` (macOS opens it with *Python Launcher*; on Windows, rename it `nfo-studio.pyw` to hide the console), or run `python3 nfo-studio.py`.
3. If [MediaInfo](https://mediaarea.net/en/MediaInfo) is missing, the window says so and its **Install MediaInfo…** button installs it for you (Homebrew on macOS, winget on Windows; on Linux it shows the command to run).

The window uses Tkinter, which comes with Python from python.org. With Homebrew's Python, add it once with `brew install python-tk`.

## Use

1. **Drag videos or folders onto the window**, or use **＋ Add videos…** / **＋ Add a folder…** (tick *include sub-folders* for a whole library). Each video is read with MediaInfo in the background.
   Drag & drop needs a small free module the first time: click **Enable drag & drop…** at the bottom of the window, it installs it in NFO Studio's own folder (nothing is added to your Python) and restarts.
2. Click a video to see its NFO. Change the **Layout** and **Font** right above the preview; **⚙ More settings…** holds the rest (ASCII text, optional notes and footer…), with a live preview of the header.
3. Save:
   - **💾 Save next to the video** writes `<video name>.nfo` beside it.
   - **📁 Save to a folder…** lets you pick where.
   - **Copy** puts the NFO in the clipboard.

Select several videos (⇧ / ⌘ / Ctrl, or Ctrl/⌘+A) to save them all in one click. The list shows what's **new**, what **has an NFO** already and what's been **saved**. When some NFOs already exist you choose: replace them, keep them and save only the others, or cancel. Nothing is ever replaced without asking.

## From the terminal

Everything also works without the window, handy for scripts:

```bash
python3 nfo-studio.py make "Some.Film.2021.1080p.BluRay.x265-GRP.mkv"
python3 nfo-studio.py make ~/Movies/Series -r        # a folder and its sub-folders
python3 nfo-studio.py menu                           # the same thing as a terminal menu
```

For each video it shows the NFO and asks: `1` next to the video, `2` another folder, `f` full view, `s` skip, `q` quit. Add `!` to use the same answer for every remaining file (`1!`).

### Options

| Option | Effect |
|---|---|
| `-r`, `--recursive` | Also look inside sub-folders |
| `--regenerate` | Also rebuild videos that already have an NFO |
| `--save next` / `--save DIR` | Don't ask: save next to each video, or into `DIR` (for scripts) |
| `--stdout` | Just print the NFOs, save nothing |
| `--layout NAME` | `mediainfo`, `rules`, `hash`, `shaded` or `minimal`, for this run only |
| `--no-preview` | Don't print each NFO before asking |

Sample files are ignored.

## What goes in the NFO

The default layout, **1 - MediaInfo**, is MediaInfo's full report under the file name, like most NFOs (the full path is reduced to the file name, so none of your folders show up). The other layouts (**2 - Clean rules**, **3 - Hash box**, **4 - Shaded box**, **5 - Minimalistic**) lay out the essentials: size, runtime, video (codec, bitrate, resolution, HDR10 / HDR10+ / Dolby Vision profile), each audio track (language, VFF/VFQ, audio description, Atmos, DTS-HD MA…) and each subtitle (forced, SDH, line count).

The rest comes from the names:
- **Title and year:** `Some.Film.2021…` gives *Some Film (2021)*.
- **Episodes:** `Some.Show.S01E02…` gives *Some Show · S01E02*. The episode title is added when the file carries one.
- **Source:** `BluRay`, `REMUX`, `WEB-DL`… and the streaming service (`NF`, `AMZN`, `ATVP`…).
- **Links:** Plex-style IDs in the file or folder names, like `{tmdb-603}`, `{imdb-tt0133093}` or `{tvdb-81189}`, become TMDB / IMDb / TVDB links.

## Settings

In the window: **Layout** and **Font** above the preview, everything else in **⚙ More settings…**. From the terminal:

```bash
python3 nfo-studio.py config                                   # show
python3 nfo-studio.py config layout=hash font="ANSI Shadow"    # change
python3 nfo-studio.py config reset
python3 nfo-studio.py layouts        # 1 - MediaInfo · 2 - Clean rules · 3 - Hash box · 4 - Shaded box · 5 - Minimalistic
python3 nfo-studio.py fonts          # the 25 built-in FIGlet fonts
python3 nfo-studio.py preview PYRO   # try the ASCII header
```

| Setting | What it does |
|---|---|
| `layout` | `mediainfo` (default), `rules`, `hash`, `shaded` or `minimal` |
| `asciiText` | The big header: `{title}`, `{group}` (release group) or any text. Empty = no header |
| `font` / `fontUrl` | One of the 25 fonts built into the file (they work offline), or any `.flf` URL, downloaded once then cached |
| `spacing` | `full` (as drawn) or `fitted` (packed, like `figlet -k`) |
| `subtitle`, `greetz`, `footer` | Optional, empty by default |
| `sceneLabels` | `on` for `RESOLUTiON`-style labels |

They're the same settings as the userscript's, stored in `~/.config/nfo-studio/config.json` (`%APPDATA%\nfo-studio` on Windows).

## Update

```bash
python3 nfo-studio.py update
```

Or click **Check for updates** in the window. It checks for a newer NFO Studio on GitHub and replaces itself after asking, keeping the old one as `nfo-studio.py.bak`. With Homebrew, it also offers to update MediaInfo.

[← Back to all projects](README.md)
