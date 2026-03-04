# Sub Label Pos

A desktop application for visually editing label positions in ASS subtitle files. Load a video alongside its `.ass` file, then drag labels directly on the video frame to reposition them.

## Features

- Drag-and-drop label repositioning on video frames
- Inline text editing (click a selected label to edit)
- Per-label font size adjustment
- Duplicate, delete, and merge labels
- Copy/paste label styles between labels
- Thumbnail gallery of label groups for quick navigation
- Folder batch mode with background preloading for fast file switching
- Snap guides when aligning labels to each other
- Multi-select with Ctrl+click and group drag

## Requirements

- Python 3.10 or later
- FFmpeg and FFprobe (must be available on your system PATH)
- PyQt6

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

### 3. Set up a virtual environment and install PyQt6

```
python3 -m venv .venv
source .venv/bin/activate
pip install PyQt6
```

On Windows, replace the activate line with:

```
.venv\Scripts\activate
```

### 4. Run the application

```
python main.py
```

## Usage

### Opening files

- Use **Open Video** in the toolbar to load a video file (`.mkv`, `.mp4`, `.avi`, `.webm`). If a matching `.ass` file exists next to the video (same name, `.ass` extension), it is loaded automatically.
- Use **Open Folder** to load all videos in a directory. A sidebar appears listing the files, and all files are preloaded in the background for instant switching.
- You can also drag and drop a video file onto the window.

### Editing labels

- **Click** a label to select it. Click again to enter text editing mode.
- **Drag** a label to move it. Snap guides appear when aligning with other labels.
- **Ctrl+Click** to select multiple labels, then drag to move them together.
- **Right-click** a label for options: duplicate, delete, edit text, copy/paste style, or merge (when 2-3 labels are selected).
- Use the floating toolbar above a selected label to adjust font size, duplicate, delete, or copy/paste styles.

### Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Left / Right arrow | Step one frame backward / forward |
| Ctrl + Left / Right | Previous / next label group |
| Ctrl + Shift + Left / Right | Previous / next file (folder mode) |
| Ctrl + S | Save the ASS file |
| Delete | Delete selected labels |

### Saving

Press **Save ASS** in the toolbar or use Ctrl+S. The application warns about unsaved changes when switching files or closing.

## How it works

The application extracts individual video frames using FFmpeg rather than playing the video. Labels from the ASS subtitle file are rendered as overlays on the video frame using Qt's painting system. When you reposition a label, the `\pos()` tag in the ASS file is updated in place. The font rendering applies a correction factor to match how libass (used by players like mpv) interprets font sizes, so label positions in this editor correspond accurately to what you see during playback.

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
