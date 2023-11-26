import argparse
import configparser
import os
import sys
from pathlib import Path

try:
    from colorama import Fore, Style, init
    from InquirerPy import prompt
    from qbittorrentapi import Client
except ModuleNotFoundError:
    print("You need to install the dependencies.")
    print("If you have pip (normally installed with python), run this command in a terminal (cmd):")
    print('pip install colorama inquirerpy qbittorrent-api')
    sys.exit()

def get_config():
    default_config = {
        'Client': {
            'host': 'localhost:8080',
            'username': 'admin',
            'password': 'adminadmin'
        }
    }
    config_file = 'client.ini'
    
    config = configparser.ConfigParser()

    if not os.path.exists(config_file):
        print('client.ini not found')
        make_new_config(default_config, config, config_file)

    config.read(config_file)
    host = config.get('Client', 'host', fallback=default_config['Client']['host'])
    username = config.get('Client', 'username', fallback=default_config['Client']['username'])
    password = config.get('Client', 'password', fallback=default_config['Client']['password'])

    return host, username, password

def make_new_config(default_config, config, config_file):
    host = input(f"Enter qBittorrent Web UI host (Empty to use {default_config['Client']['host']}): ")
    host = host.strip() or default_config['Client']['host']
    print(f"Using qBittorrent Web UI host: {host}")

    username = input(f"Enter qBittorrent Web UI username (Empty to use {default_config['Client']['username']}): ")
    username = username.strip() or default_config['Client']['username']
    print(f"Using qBittorrent Web UI username: {username}")

    password = input(f"Enter qBittorrent Web UI password (Empty to use {default_config['Client']['password']}): ")
    password = password.strip() or default_config['Client']['password']
    print("Using qBittorrent Web UI password: *****")  # For security, we only print asterisks for the password

    # Save the new credentials to client.ini
    config['Client'] = {}
    config['Client']['host'] = host
    config['Client']['username'] = username
    config['Client']['password'] = password
    with open(config_file, 'w') as f:
        config.write(f)
    print("client.ini created")

def init_client():
    host, username, password = get_config()
    return Client(host=host, username=username, password=password)


def get_matching_files_in_dir_and_subdirs(search_path, sizes):
    files_in_directory = [os.path.join(dirpath, name) for dirpath, _, filenames in os.walk(search_path) for name in filenames]
    print(f"Found {len(files_in_directory)} files in the search directory")

    files_and_sizes = map(lambda file: [file, os.path.getsize(file)], files_in_directory)

    matched_files = [pair for pair in files_and_sizes if pair[1] in sizes]
    return matched_files

def match(torrent, files_in_directory, match_extension, download_path, is_dry_run):
    matched_files = set()  # keep track of already matched files
    for torrent_file in torrent.files:
        if torrent_file.priority == 0:
            continue
        matching_files = []
        for disk_file_abs_path, disk_file_size in files_in_directory:
            if (torrent_file.size == disk_file_size and
                (not match_extension or Path(disk_file_abs_path).suffix == Path(torrent_file.name).suffix) and
                disk_file_abs_path not in matched_files):
                matching_files.append(disk_file_abs_path)

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
            selected_file_path = response["file"]
        elif matching_files:
            selected_file_path = matching_files[0]
        else:
            print(f"{Fore.RED}No matches found for {torrent_file.name}!{Style.RESET_ALL}")
            continue

        matched_files.add(selected_file_path)
        new_relative_path = Path(selected_file_path).relative_to(download_path).as_posix()
        if new_relative_path == torrent_file.name:
            print(f"File:\n{torrent_file.name} left as is")
            continue
        if is_dry_run:
            print(f"{Fore.YELLOW}Dry run:{Style.RESET_ALL}\n{torrent_file.name} ->\n{Fore.YELLOW}{new_relative_path}{Style.RESET_ALL}")
            continue
        print(f"Renaming file:\n{torrent_file.name} ->\n{Fore.GREEN}{new_relative_path}{Style.RESET_ALL}")
        torrent.rename_file(file_id=torrent_file.id, new_file_name=new_relative_path)

def set_search_and_download_paths(torrent, input_search_path, input_download_path, use_torrent_save_path_as_search_path):
    content_path = torrent.content_path
    download_path = torrent.save_path
    search_path = ''

    if input_download_path:
        content_path = str(Path(input_download_path).joinpath(Path(content_path).relative_to(download_path)))
        download_path = input_download_path
    if input_search_path.startswith(str(Path(download_path).resolve())):
        search_path = input_search_path
    elif input_search_path and not input_search_path.startswith(str(Path(download_path).resolve())):
        sys.exit(f"Search path {input_search_path} must be sub directory of {download_path}")
    elif use_torrent_save_path_as_search_path or not Path(content_path).exists():
        search_path = download_path
    else:
        search_path = content_path

    search_path = search_path if Path(search_path).exists() else sys.exit(f"Search path {search_path} does not exist")
    download_path = download_path if Path(download_path).exists() else sys.exit(f"Download path {download_path} does not exist")

    return search_path, download_path


def main(torrent_hash, input_search_path = None, input_download_path = None, use_torrent_save_path_as_search_path = False, match_extension = False, is_dry_run = False):
    init() #colorama
    client = init_client()
    print("connected to api")
    torrents_info = client.torrents.info(torrent_hashes=torrent_hash)

    torrent = torrents_info[0] if torrents_info else sys.exit("No torrent found with the given hash")
    print(f"Target torrent: {torrent.name}")

    search_path , download_path = set_search_and_download_paths(torrent, input_search_path, input_download_path, use_torrent_save_path_as_search_path)
    print(f"Search directory {search_path}\nDownload directory {download_path}")
    torrent_file_sizes = set(file.size for file in torrent.files)
    files_in_directory = get_matching_files_in_dir_and_subdirs(search_path, torrent_file_sizes)
    print("Looking for matches")
    match(torrent, files_in_directory, match_extension, download_path, is_dry_run)
    if input_download_path and input_download_path != torrent.save_path and not is_dry_run:
        print(f"Changing torrent save location to {input_download_path}")
        client.torrents_set_location(torrent_hashes=torrent_hash, location=input_download_path)
        print(f"{Fore.LIGHTMAGENTA_EX}Rechecking torrent{Style.RESET_ALL}")
        client.torrents_recheck(torrent_hash)
    if is_dry_run:
        print(f"{Fore.YELLOW}Performed a dry run, nothing was modified{Style.RESET_ALL}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tool to match torrents added to qBittorent to files on a disk")
    parser.add_argument('hash', help='Torrent hash, or a path to a txt with a list of hashes.')
    parser.add_argument('-s', '-spath', default='', help="Specifies search path. Must be a subpath of the download path.")
    parser.add_argument('-d', '-dpath', default='', help="Sets new download path for the torrent.")
    parser.add_argument('-fd', action='store_true', help="Forces search in torrent's download directory. Default is torrent's content directory. Ignored if passed along with search.")
    parser.add_argument('-e', '-ext', action='store_true', help="Forces matched files to share an extension.")
    parser.add_argument('-dry', action='store_true', help="Performs a dry run without modifying anything.")

    args = parser.parse_args()

    if os.path.isfile(args.hash):
        with open(args.hash, "r") as file:
            hashes = file.read()
        for hash in hashes.split('\n'):
            main(hash, args.s, args.d, args.fd, args.e, args.dry)
    else:
        main(args.hash, args.s, args.d, args.fd, args.e, args.dry)
