import argparse
import configparser
import sys
from pathlib import Path

try:
    from colorama import Fore, Style, init
except ModuleNotFoundError:
    print(
        "You need to install the colorama module. (https://pypi.org/project/colorama/)"
    )
    print(
        "If you have pip (normally installed with python), run this command in a terminal (cmd): pip install colorama"
    )
    sys.exit()

try:
    from InquirerPy import prompt
except ModuleNotFoundError:
    print(
        "You need to install the InquirerPy module. (https://pypi.org/project/inquirerpy/)"
    )
    print(
        "If you have pip (normally installed with python), run this command in a terminal (cmd): pip install inquirerpy"
    )
    sys.exit()


try:
    from qbittorrentapi import Client
except ModuleNotFoundError:
    print(
        "You need to install the qbittorrentapi module. (https://pypi.org/project/qbittorrent-api/)"
    )
    print(
        "If you have pip (normally installed with python), run this command in a terminal (cmd): pip install qbittorrent-api"
    )
    sys.exit()


def init_client():
    config = configparser.ConfigParser()
    config.read('client.ini')
    
    host = config.get('Client', 'host', fallback='localhost:8080')
    username = config.get('Client', 'username', fallback='admin')
    password = config.get('Client', 'password', fallback='adminadmin')
    
    return Client(host=host, username=username, password=password)

def get_files_in_directory(work_dir):
    return {str(p): p.stat().st_size for p in Path(work_dir).rglob('*') if p.is_file()}


def match(torrent, files_in_torrent, files_in_directory, work_dir, dry_run):
    matched_files = set()  # keep track of already matched files
    for torrent_file in files_in_torrent:
        matching_files = [disk_file_abs_path for disk_file_abs_path, size in files_in_directory.items() 
                          if torrent_file.size == size and disk_file_abs_path not in matched_files]
        if len(matching_files) > 1:
            question = [
                {
                    "type": "list",
                    "message": f"Multiple matches found for {torrent_file.name}. Select a file to match:",
                    "choices": matching_files,
                    "name": "file",
                }
            ]
            response = prompt(question)
            selected_file = response["file"]
        elif matching_files:
            selected_file = matching_files[0]
        else:
            continue  # if no matching file found, go to the next torrent file

        matched_files.add(selected_file)  # add selected file to the set of matched files
        
        torrent_file_rel_path = Path(torrent_file.name)
        torrent_save_path = Path(work_dir)
        disk_file_abs_path = Path(selected_file)
        torrent_file_abs_path = torrent_save_path.joinpath(torrent_file_rel_path)
        if torrent_file_abs_path != disk_file_abs_path:
            new_relative_path = disk_file_abs_path.relative_to(torrent_save_path).as_posix()
            if not dry_run:
                print(f"Renaming file:\n{torrent_file.name} ->\n{Fore.GREEN}{new_relative_path}{Style.RESET_ALL}")
                torrent.rename_file(file_id=torrent_file.id, new_file_name=new_relative_path)
            else:
                print(f"{Fore.YELLOW}Dry run: {torrent_file.name} ->\n{new_relative_path}{Style.RESET_ALL}")


def main(torrent_hash, new_directory = '', is_dry_run = False):
    init() #colorama
    client = init_client()
    torrents_info = client.torrents.info(torrent_hashes=torrent_hash)
    if torrents_info:
        torrent = torrents_info[0]
    else:
        print("No torrent found with the given hash")
        return
    if new_directory:
        work_dir = new_directory
    else:
        work_dir = torrent.save_path
    files_in_torrent = torrent.files
    files_in_directory = get_files_in_directory(work_dir)
    if torrent.save_path != work_dir and not is_dry_run:
        client.torrents_set_location(torrent_hashes=torrent_hash, location=work_dir)
        print(f"Changing torrent save location to {work_dir}")
    match(torrent, files_in_torrent, files_in_directory, work_dir, is_dry_run)
    if is_dry_run:
        print(f"{Fore.YELLOW}Performed a dry run, nothing was modified{Style.RESET_ALL}")
    if new_directory and not is_dry_run:
        print(f"{Fore.LIGHTMAGENTA_EX}New save location was set, rechecking torrent{Style.RESET_ALL}")
        client.torrents_recheck(torrent_hash)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool to match torrents added to qBittorent to files on a disk")
    parser.add_argument('torrent_hash', help='The torrent hash.')
    parser.add_argument('directory', nargs='?', default='', help="Set new download directory. Script searches torrent's download directory if not specifed")
    parser.add_argument('-d', action='store_true', help='Perform a dry run without modifying anything.')

    args = parser.parse_args()
    main(args.torrent_hash, args.directory, args.d)