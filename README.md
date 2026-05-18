# Sub Label Pos

A desktop application for visually editing label positions in ASS subtitle files. Load a video alongside its `.ass` file, then drag labels directly on the video frame to reposition them.

## Features

- Drag-and-drop label repositioning on video frames
- Inline text editing (click a selected label to edit)
- Per-label font size, alignment, bold/italic, fill / outline color, outline width
- Duplicate, delete, and merge labels
- Copy/paste label styles between labels; promote inline overrides into the named style
- Right-side **labels list** with click-to-jump timestamps, live text search, and grouping for labels that share identical timing
- Optional thumbnail **gallery** of label groups for visual navigation (collapsible handle, toggle with `G`)
- Folder batch mode with background preloading for fast file switching, plus a left-side file sidebar with per-file ready/pending status
- Snap guides when aligning labels to each other
- Multi-select with Ctrl+click and group drag
- Persistent **status bar** with file index, frame timecode, label/group counts, resolution, and current HW-decode mode
- Settings dialog with three performance profiles (**Performance / Balanced / Quality**) that auto-detect on first launch and can be overridden at any time

## Requirements

- Python 3.10 or later
- FFmpeg and FFprobe (must be available on your system PATH)
- PyQt6 and qtawesome (installed automatically as Python dependencies)

## Installation

### 1. Install FFmpeg

**Linux (Debian/Ubuntu):**

```
sudo apt install ffmpeg
```

**Linux (Fedora):**

```
sudo dnf install ffmpeg
```

**Linux (Arch):**

```
sudo pacman -S ffmpeg
```

**Windows:**

Download from https://ffmpeg.org/download.html and add the `bin` folder to your system PATH.

**macOS:**

```
brew install ffmpeg
```

### 2. Clone the repository

```
git clone https://codeberg.org/BuGiPoP/sub-label-pos.git
cd sub-label-pos
```

### 3. Set up a virtual environment and install the package

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

On Windows, replace the activate line with:

```
.venv\Scripts\activate
```

### 4. Run the application

```
sub-label-pos
```

## Usage

### Opening files

- Click the **Open** button in the toolbar (split-button menu: video file / folder / ASS only) to load a video. If a matching `.ass` file exists next to the video (same name, `.ass` extension), it loads automatically.
- Open a folder to load all videos in a directory — the left-side file sidebar appears with per-file ready/pending status while files preload in the background.
- Drag and drop a video file onto the window.
- The welcome screen lists your recent folders with file counts and last-opened time; click any row to reopen.

### Navigating labels

- **Right sidebar (labels list)**: every label with its `start → end` timestamp. Click any row to jump the playback to that label's start time and select it on the canvas. Labels that share identical timestamps are grouped into a single row with a `×N` badge stacking all of their texts. The search box filters labels by text. Right-click a row for **Edit text**, **Jump to time**, or **Delete**.
- **Gallery** (bottom): thumbnail preview of label groups for visual navigation. Hidden by default on the Performance profile; toggle with the `G` shortcut or the toolbar button.
- **Timeline**: scrub or click the chunky playhead grip; group markers along the track jump to their group when clicked.

### Editing labels

- **Click** a label on the canvas to select it. **Click again** to enter inline text editing.
- **Drag** a label to move it. Snap guides appear when aligning with other labels.
- **Ctrl+Click** to select multiple labels, then drag to move them together; a dashed bounding box and "N selected" badge show the group.
- **Right-click** a label for: edit text, duplicate, copy/paste style, sync times, merge (2-3 labels selected), delete.
- Use the **floating toolbar** that appears above a selected label for: style preset, font size, alignment, bold/italic, fill / outline color, outline width, copy/paste/promote style.

### Saving

Press **Save** in the toolbar or use `Ctrl+S`. The Save button shows a yellow dot when there are unsaved changes; the status bar also surfaces an `unsaved` chip. The app warns about unsaved changes when switching files or closing.

### Settings (cog icon)

- **Display**: toggle the gallery, file sidebar, or labels list.
- **Playback**: HW Decode mode (auto / VAAPI / NVDEC / software) and High Quality mpv scaling.
- **Hardware profile**: auto-detected on first launch (Performance / Balanced / Quality). Override manually at any time — affects thumbnail size, frame cache size, and worker counts. Most profile changes take effect on the next launch.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Left / Right arrow | Step one frame backward / forward |
| Space | Play / pause |
| Ctrl + Left / Right | Previous / next label group |
| Ctrl + Shift + Left / Right | Previous / next file (folder mode) |
| Ctrl + S | Save the ASS file |
| Ctrl + Z | Undo |
| Ctrl + Shift + Z / Ctrl + Y | Redo |
| Ctrl + D | Duplicate selected label |
| Ctrl + B / Ctrl + I | Toggle bold / italic on selected label |
| Ctrl + Shift + V | Paste style |
| Delete | Delete selected labels |
| G | Toggle gallery |
| L | Toggle labels list (right sidebar) |

## How it works

The application extracts individual video frames using FFmpeg rather than playing the video. Labels from the ASS subtitle file are rendered as overlays on the video frame using Qt's painting system. When you reposition a label, the `\pos()` tag in the ASS file is updated in place. The font rendering applies a correction factor to match how libass (used by players like mpv) interprets font sizes, so label positions in this editor correspond accurately to what you see during playback.

The first time you launch the app it detects your machine's RAM and CPU and picks one of three performance profiles. Each profile sets sensible defaults for thumbnail extraction size, the editor frame cache, preload worker counts, and whether the gallery is shown by default. You can override the profile at any time from the Settings dialog.

### Development

To run the test suite (385 tests):

```
pytest
```

## License

MIT License

Copyright (c) 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
