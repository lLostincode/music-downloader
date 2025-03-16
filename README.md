# Spotify Downloader

A Spotify downloader web application built with Flask, powered by [spotDL](https://github.com/spotDL/spotify-downloader).

## Requirements

### System Prerequisites

#### For Ubuntu/Linux
```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3-pip python3-dev
```

#### For macOS
```bash
brew install ffmpeg
```

#### For Windows
Install [FFmpeg](https://ffmpeg.org/download.html) and ensure it's in your PATH.

### Python Packages
```bash
pip install -r requirements.txt
```

## Installation
1. Clone the repository
2. Install system prerequisites as listed above
3. Install Python packages:
```bash
pip install -r requirements.txt
```

## Usage
Run the application:
```bash
python3 app.py
```

## Features
- Web-based interface for easy access
- Download Spotify songs, albums, and playlists
- Progress tracking for downloads
- Simple and intuitive user experience

## Credits
This application uses [spotDL](https://github.com/spotDL/spotify-downloader) for downloading Spotify content.

## License
MIT
