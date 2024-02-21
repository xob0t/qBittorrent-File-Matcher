from __future__ import annotations

import argparse
import configparser
import os
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from colorama import Fore, Style, init
    from InquirerPy.resolver import prompt
    from qbittorrentapi import Client, Conflict409Error, TorrentDictionary, TorrentFile
except (ImportError, ModuleNotFoundError):
    print(traceback.format_exc())
    print("You need to install the dependencies.")
    print("If you have pip (normally installed with python), run this command in a terminal (cmd):")
    print("pip install colorama inquirerpy qbittorrent-api")
    sys.exit()

if TYPE_CHECKING:
    from qbittorrentapi import TorrentInfoList

def get_config() -> tuple[str, str, str]:
    default_config: dict[str, dict[str, str]] = {
        "Client": {
            "host": "localhost:8080",
            "username": "admin",
            "password": "adminadmin",
        },
    }
    config_file = "client.ini"

    config = configparser.ConfigParser()

    if not os.path.exists(config_file):
        print("client.ini not found")
        make_new_config(default_config, config, config_file)

    config.read(config_file)
    host = config.get("Client", "host", fallback=default_config["Client"]["host"])
    username = config.get("Client", "username", fallback=default_config["Client"]["username"])
    password = config.get("Client", "password", fallback=default_config["Client"]["password"])

    return host, username, password

def make_new_config(default_config, config, config_file):
    host = input(f"Enter qBittorrent Web UI host (Empty to use {default_config['Client']['host']}): ")
    host = host.strip() or default_config["Client"]["host"]
    print(f"Using qBittorrent Web UI host: {host}")

    username = input(f"Enter qBittorrent Web UI username (Empty to use {default_config['Client']['username']}): ")
    username = username.strip() or default_config["Client"]["username"]
    print(f"Using qBittorrent Web UI username: {username}")

    password = input(f"Enter qBittorrent Web UI password (Empty to use {default_config['Client']['password']}): ")
    password = password.strip() or default_config["Client"]["password"]
    print("Using qBittorrent Web UI password: *****")  # For security, we only print asterisks for the password

    # Save the new credentials to client.ini
    config["Client"] = {}
    config["Client"]["host"] = host
    config["Client"]["username"] = username
    config["Client"]["password"] = password
    with open(config_file, "w", encoding="utf-8") as f:
        config.write(f)
    print("client.ini created")

def init_client() -> Client:
    host, username, password = get_config()
    return Client(host=host, username=username, password=password)


def get_matching_files_in_dir_and_subdirs(search_path, sizes: set[int]) -> list[tuple[str, int]]:
    files_in_directory: list[str] = [os.path.join(dirpath, name) for dirpath, _, filenames in os.walk(search_path) for name in filenames]
    print(f"Found {len(files_in_directory)} files in the search directory")

    files_and_sizes = map(lambda file: (file, os.path.getsize(file)), files_in_directory)

    return [pair for pair in files_and_sizes if pair[1] in sizes]

def match(
    torrent: TorrentDictionary,
    files_in_directory,
    match_extension,
    download_path,
    is_dry_run: bool,
) -> None:
    matched_files = set()  # keep track of already matched files
    ignored_subfolders: set[Path] = set()  # To keep track of ignored subfolders
    ignored_extensions = set()  # To keep track of ignored file extensions

    torrent_file: TorrentFile
    for torrent_file in torrent.files:
        if torrent_file.priority == 0:
            continue

        matching_files: list[str] = [
            disk_file_abs_path
            for disk_file_abs_path, disk_file_size in files_in_directory
            if torrent_file.size == disk_file_size
            and (
                not match_extension
                or Path(disk_file_abs_path).suffix.lower()
                == Path(torrent_file.name).suffix.lower()
            )
            and disk_file_abs_path not in matched_files
            and all(
                ignored_subfolder not in Path(disk_file_abs_path).parents
                for ignored_subfolder in ignored_subfolders
            )
            and Path(disk_file_abs_path).suffix.lower() not in ignored_extensions
        ]
        if len(matching_files) > 1:
            subfolder_to_ignore: Path = Path(matching_files[0]).parent
            extension_to_ignore: str = Path(torrent_file.name).suffix.lower()

            subfolder_ignore_question = f"<Ignore all files in '{subfolder_to_ignore}'>"
            extension_ignore_question = f"<Ignore all files with '{extension_to_ignore}' extensions>"

            matching_files.append("<Skip this file>")
            matching_files.append(subfolder_ignore_question)
            matching_files.append(extension_ignore_question)
            print("\n")
            question: list[dict[str, Any]] = [
                {
                    "type": "list",
                    "message": f"Multiple matches found for '{torrent_file.name}'. Select a file to match:",
                    "choices": matching_files,
                    "name": "file",
                },
            ]
            response = prompt(question)
            if response["file"] == "<Skip this file>":
                continue
            if response["file"] == subfolder_ignore_question:
                ignored_subfolders.add(subfolder_to_ignore)
                print(f"Ignoring subfolder '{subfolder_to_ignore}' for this session.")
                continue
            if response["file"] == extension_ignore_question:
                ignored_extensions.add(extension_to_ignore)
                print(f"Ignoring file extension '{extension_to_ignore}' for this session.")
                continue

            selected_file_path = response["file"]
            assert isinstance(selected_file_path, str)

        elif matching_files:  # Single match.
            selected_file_path = matching_files[0]

        else:
            print(f"{Fore.YELLOW}No matches found for '{torrent_file.name}'!{Style.RESET_ALL}")
            continue

        matched_files.add(selected_file_path)
        new_relative_path = Path(selected_file_path).relative_to(download_path).as_posix()
        if new_relative_path == torrent_file.name:
            print(f"{torrent_file.name} already synced, left as is")
            continue
        if is_dry_run:
            print(f"{Fore.YELLOW}Dry run:{Style.RESET_ALL}\n{torrent_file.name} ->\n{Fore.YELLOW}{new_relative_path}{Style.RESET_ALL}")
            continue

        try:
            torrent.rename_file(file_id=torrent_file.id, new_file_name=new_relative_path)  # type: ignore[reportCallIssue]
        except Conflict409Error as e:
            print(f"{Fore.RED}Skipping {torrent_file.name} due to error:", e)
        else:
            print(f"Renaming file:\n{torrent_file.name} ->\n{Fore.GREEN}{new_relative_path}{Style.RESET_ALL}")

def is_relative_to(path1: Path, path2: Path):
    try:  # pylint: disable=R1705
        path1.relative_to(path2)
    except Exception:
        return False
    else:
        return True

def set_search_and_download_paths(
    torrent: TorrentDictionary,
    input_search_path: Path | None,
    input_download_path: Path | None,
    use_torrent_save_path_as_search_path: bool,
) -> tuple[Path, Path] | tuple[None, None]:
    content_path: Path = Path(torrent.content_path)
    raw_download_path = torrent.save_path

    if input_download_path:
        content_path = input_download_path.joinpath(content_path.relative_to(raw_download_path))
        raw_download_path = input_download_path

    download_path: Path = Path(raw_download_path).resolve()
    search_path: Path | None = None
    if input_search_path:
        if not is_relative_to(input_search_path, download_path):
            print(f"Search path {input_search_path} must be a sub directory of {raw_download_path}\n")
            return None, None
        search_path = input_search_path

    elif use_torrent_save_path_as_search_path or not content_path.exists():
        search_path = download_path

    else:
        search_path = content_path if content_path.is_dir() else content_path.parent

    if not search_path:
        sys.exit(f"Search path '{search_path}' does not exist")
    if not download_path:
        sys.exit(f"Download path '{download_path}' does not exist")

    return search_path, download_path


def matcher(
    input_torrent_hashes: list[str],
    sync_all: bool = False,
    input_search_path: Path | None = None,
    input_download_path: Path | None = None,
    use_torrent_save_path_as_search_path: bool = False,
    match_extension: bool = False,
    is_dry_run: bool = False,
):
    qb_client: Client = init_client()
    print("Connected to api!")
    if input_torrent_hashes:
        torrents: TorrentInfoList = qb_client.torrents.info(torrent_hashes=input_torrent_hashes)
        if not torrents:
            sys.exit(f"{Fore.RED}No torrents found matching any of the provided hashes.{Style.RESET_ALL}")
        else:
            found_hashes: list[str] = [torrent["hash"].upper() for torrent in torrents]  # Extracting found hashes
            for hash_value in input_torrent_hashes:
                if hash_value not in found_hashes:
                    print(f"{Fore.RED}Torrent with hash '{hash_value}' not found.{Style.RESET_ALL}")
    elif sync_all:
        torrents = qb_client.torrents_info()
        if not torrents:
            sys.exit(f"{Fore.RED}No torrents found found anywhere in your qBittorrent{Style.RESET_ALL}")
    else:
        print("Nothing to do? (check the cmdline args passed)")
        return

    for torrent in torrents:
        torrent_hash = torrent["hash"]
        print(f"\nTarget torrent: {torrent.name}")
        search_path , download_path = set_search_and_download_paths(
            torrent,
            input_search_path,
            input_download_path,
            use_torrent_save_path_as_search_path
        )
        if not search_path or not download_path:
            # print(f"Skipping '{torrent.name}', no search path determined.\n")
            continue

        print(f"Search directory '{search_path}'\nDownload directory '{download_path}'")

        # Unfortunately hashing individual files isn't possible (or at least practical), so we match with their sizes.
        # Probably need a minimum size to consider, otherwise it'll always match 0-byte files.
        torrent_file_sizes: set[int] = {file.size for file in torrent.files}

        print("Scanning files in search directory")
        files_in_directory: list[tuple[str, int]] = get_matching_files_in_dir_and_subdirs(search_path, torrent_file_sizes)
        print(f"Found {len(files_in_directory)} matches in '{search_path}'")

        print("Executing matchmaking logic...")
        match(torrent, files_in_directory, match_extension, download_path, is_dry_run)

        if input_download_path and input_download_path != torrent.save_path and not is_dry_run:
            print(f"Changing torrent save location to {input_download_path}")
            qb_client.torrents_set_location(torrent_hashes=torrent_hash, location=str(input_download_path))
            print(f"{Fore.LIGHTMAGENTA_EX}Rechecking torrent{Style.RESET_ALL}")
            qb_client.torrents_recheck(torrent_hash)
        if is_dry_run:
            print(f"{Fore.YELLOW}Performed a dry run, nothing was modified{Style.RESET_ALL}")


def main() -> None:

    init()  # colorama

    parser = argparse.ArgumentParser(description="Tool to match torrents added to qBittorent to files on a disk")
    parser.add_argument("input", nargs="?", default=None, help="Torrent hash, or a txt with a list of hashes.")
    parser.add_argument("-a", "-all", action="store_true", help="Look for matches for every qBT torrent. Ignored if used with input hash(es).")
    parser.add_argument("-s", "-spath", default=None, help="Specifies search path. Must be a subpath of the download path.")
    parser.add_argument("-d", "-dpath", default=None, help="Sets new download path for the torrent.")
    parser.add_argument("-fd", action="store_true", help="Forces search in torrent's download directory. Default is torrent's content directory. Ignored if passed along with search.")
    parser.add_argument("-e", "-ext", action="store_true", help="Forces matched files to share an extension.")
    parser.add_argument("-dry", action="store_true", help="Performs a dry run without modifying anything.")

    args = parser.parse_args()

    path: Path | None = Path(args.input) if args.input else None
    if path and path.exists() and path.is_file():  # TODO: determine whether it's a hash or a filepath.
        with path.open(mode="r", encoding="utf-8") as file:
            hashes: list[str] = [line.strip().upper() for line in file if line.strip()]
    else:
        hashes = [args.input.upper()] if args.input else []
    
    input_search_path: Path | None = Path(args.s) if args.s else None
    if input_search_path and (not input_search_path.exists() or input_search_path.is_file()):
        sys.exit(f"bad search path: '{input_search_path}' (either nonexistent or not a directory)")

    input_download_path: Path | None = Path(args.d) if args.d else None
    if input_download_path and (not input_download_path.exists() or input_download_path.is_file()):
        sys.exit(f"bad download path: '{input_download_path}' (either nonexistent or not a directory)")

    matcher(
        hashes,
        args.a,
        input_search_path,
        input_download_path,
        args.fd,
        args.e,
        args.dry,
    )

if __name__ == "__main__":
    main()
