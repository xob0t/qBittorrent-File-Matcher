# qBittorrent File Matcher

A Python script to match torrents added to qBittorent to files on a disk.

## Demo

![demo](media/demo.webp "Made with ScreenToGif")

## Features

* No modification of any files on your disk
* Search for matches is done by size
* Matching is done by renaming files in qBittorent
* In case of finding multiple matches user will be asked to choose a match from a list
* Dry run to preview any changes done by the script

## How to run?

You can run the script from the command line using the following command:
#### ``python qbittorrent_file_matcher.py <torrent_hash> [new_directory] [-d]``

- `<torrent_hash>`: The hash of the torrent to match. (Right click a torrent in qBittorent, Copy - info hash v1)
- `[new_directory]` (optional): Provide a directory if the files you want to match are not in the torrent's download directory.
- `-d` (optional): Perform a dry run without modifying anything.

## Notes

* Tested only on Windows

## todo
* Directory scanning optimization. As it currently works, it will try to scan whole download dir and its sub dirs for files, which will not go well if the dir is you C drive
* Option to force matches to share an extension

## Setup

### Install dependencies

- [colorama](https://pypi.org/project/colorama/)
- [InquirerPy](https://pypi.org/project/inquirerpy/)
- [qbittorrent-api](https://pypi.org/project/qbittorrent-api/)

#### ``pip install colorama inquirerpy qbittorrent-api``

### Enable Web UI

In qBittorrent - Tools -> Options -> Web UI -> Top checkmark

### Modify client.ini with your Web UI credentials

If no client.ini found, script will try to use defaults
