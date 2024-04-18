from __future__ import annotations

import argparse
import configparser
import os
import sys
import traceback
from pathlib import Path, PurePath, PureWindowsPath
from time import sleep
from typing import TYPE_CHECKING, Any

from qbittorrentapi import TorrentFilesList

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

if sys.version_info < (3, 7, 0):
    sys.exit(f"This script requires at least python 3.7, you are running {sys.version_info}. Please upgrade your Python installation.")

try:  # sourcery skip: remove-redundant-exception, simplify-single-exception-tuple
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
    from collections.abc import Sequence

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

    if not Path(config_file).exists():
        print("client.ini not found, initializing new config...")
        make_new_config(default_config, config, config_file)

    config.read(config_file)
    host = config.get("Client", "host", fallback=default_config["Client"]["host"])
    username = config.get("Client", "username", fallback=default_config["Client"]["username"])
    password = config.get("Client", "password", fallback=default_config["Client"]["password"])

    return host, username, password

def make_new_config(
    default_config: dict[str, dict[str, str]],
    config: configparser.ConfigParser,
    config_file: str,
):
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
    with Path.cwd().joinpath(config_file).open("w", encoding="utf-8") as f:
        config.write(f)
    print("client.ini created")

def init_client() -> Client:
    host, username, password = get_config()
    return Client(host=host, username=username, password=password)

def windows_get_size_on_disk(file_path: os.PathLike | str) -> int:
    # Define GetCompressedFileSizeW from the Windows API
    GetCompressedFileSizeW = ctypes.windll.kernel32.GetCompressedFileSizeW
    GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    GetCompressedFileSizeW.restype = wintypes.DWORD

    # Prepare the high-order DWORD
    filesizehigh = wintypes.DWORD()

    # Call GetCompressedFileSizeW
    low = GetCompressedFileSizeW(str(file_path), ctypes.byref(filesizehigh))

    if low == 0xFFFFFFFF:  # Check for an error condition.
        error = ctypes.GetLastError()
        if error:
            raise ctypes.WinError(error)

    # Combine the low and high parts
    size_on_disk = (filesizehigh.value << 32) + low

    return size_on_disk

def get_size_on_disk(file_path: os.PathLike | str):
    """Returns the size on disk of the file at file_path in bytes."""
    if os.name == "posix":
        # st_blocks are 512-byte blocks
        return os.stat(file_path).st_blocks * 512  # type: ignore[attr-defined]

    return windows_get_size_on_disk(file_path)

# From stackoverflow.
def are_all_paths_same(
    paths: Sequence[os.PathLike | str],
) -> bool:
    # sourcery skip: assign-if-exp, hoist-statement-from-if, remove-redundant-condition
    file_identifiers = set()
    for path in paths:
        try:
            resolved_path: Path = Path(path).resolve(strict=True)
            file_stat = os.stat(resolved_path, follow_symlinks=True)
            file_identifier: tuple[int, int] = (file_stat.st_dev, file_stat.st_ino)
            file_identifiers.add(file_identifier)
        except FileNotFoundError:
            # Handle the case where the path does not exist
            print(f"{Fore.YELLOW}Warning: The path '{path}' was not found.")
            return False

    # If all paths refer to the same device and inode/file index, the set will contain only one unique identifier
    return len(file_identifiers) == 1

def hardlink_largest_file(
    matching_files: list[str] | list[Path | str] | list[Path],
):
    """Find the largest file by 'size on disk' among matching_files and hardlink it."""
    existing_files: set[Path] = {Path(file).resolve() for file in matching_files if Path(file).resolve().exists()}
    if not existing_files:
        print("No files exist on disk to hardlink!")
        return
    largest_file: Path = max(existing_files, key=get_size_on_disk)
    if not largest_file.exists():
        return
    for file in matching_files:
        r_file = Path(file).resolve()
        if r_file == largest_file:
            continue

        r_file.parent.mkdir(exist_ok=True, parents=True)
        if r_file.exists() and r_file.is_dir():  # unlink will fail if it's somehow a folder.
            print(f"'{file}' is somehow a directory, rmdir()")
            r_file.rmdir()
        else:
            print(f"Deleting file '{file}'")
            r_file.unlink(missing_ok=True)

        # Create hardlink
        print(f"Creating hardlink for '{largest_file}' <-> '{file}'")
        os.link(largest_file, r_file)

def is_relative_to(path1: Path, path2: Path) -> bool:
    """pathlib.Path in later versions of python already have this builtin"""
    try:  # pylint: disable=R1705
        path1.relative_to(path2)
    except ValueError:
        return False
    else:
        return True

def get_matching_files_in_dir_and_subdirs(
    search_path: Path,
    sizes: set[int],
    use_hardlinks: bool,
) -> list[tuple[str, int]]:
    files_in_directory: list[str] = [
        os.path.join(dirpath, name)
        for dirpath, _, filenames in os.walk(search_path)
        for name in filenames
    ]
    #print(f"Found {len(files_in_directory)} files in the search directory")

    files_and_sizes: list[tuple[str, int]] = []
    for file in files_in_directory:
        try:
            size: int = os.path.getsize(file)
            if size <= 512 and use_hardlinks:  # don't do anything with small files if hardlinking.
                continue
        except FileNotFoundError as e:
            print(f"{Fore.RED}Somehow os.walk found a file that does not exist: {e}{Fore.RESET}")
            continue
        files_and_sizes.append((file, size))

    return [pair for pair in files_and_sizes if pair[1] in sizes]

IGNORED_SUBFOLDERS: set[Path] = set()  # To keep track of ignored subfolders
IGNORED_EXTENSIONS: set[str] = set()   # To keep track of ignored file extensions

def match(
    torrent: TorrentDictionary,
    files_in_directory: list[tuple[str, int]],
    match_extension: bool,
    download_path: Path,
    use_hardlinks: bool,
    is_dry_run: bool,
    no_redownload: bool,
) -> bool:
    global IGNORED_EXTENSIONS  # pylint: disable=W0602
    global IGNORED_SUBFOLDERS  # pylint: disable=W0602

    made_change: bool = False
    matched_files: set[str] = set()  # keep track of already matched files
    torrent_file: TorrentFile
    for torrent_file in torrent.files:  # type: ignore[reportAttributeAccessError]
        if torrent_file.priority == 0:
            continue

        original_relpath_str: str = str(torrent_file.name)
        original_relpath = PurePath(original_relpath_str)
        original_file_path: Path = download_path / original_relpath_str

        matching_files: list[str] = [
            disk_file_abs_path
            for disk_file_abs_path, disk_file_size in files_in_directory
            if torrent_file.size == disk_file_size
            and (
                not match_extension
                or Path(disk_file_abs_path).suffix.lower()
                == original_relpath.suffix.lower()
            )
            and disk_file_abs_path not in matched_files
        ]

        if len(matching_files) > 1:
            if are_all_paths_same(matching_files):
                continue  # all hard/symlinked to the same file.
            if is_dry_run:
                print("Multiple files found (dryrun - would normally prompt to select)")
                continue
            subfolder_to_ignore: Path = Path(matching_files[0]).parent
            if subfolder_to_ignore in IGNORED_SUBFOLDERS:
                continue
            extension_to_ignore: str = original_relpath.suffix.lower()
            if extension_to_ignore in IGNORED_EXTENSIONS:
                continue

            skip_file_option = "<Skip this file>"
            subfolder_ignore_option = f"<Don't ask again (skip) for all files in '{subfolder_to_ignore}'>"
            extension_ignore_option = f"<Don't ask again (skip) for all files with '{extension_to_ignore}' extensions>"
            hardlink_option = "<Hardlink all matches (experimental)>"

            choices: list[str] = [
                *matching_files,
                skip_file_option,
                subfolder_ignore_option,
                extension_ignore_option,
                hardlink_option,
            ]
            print("\n")
            question: list[dict[str, Any]] = [
                {
                    "type": "list",
                    "message": f"Multiple matches found (all {torrent_file.size} bytes) for '{original_relpath_str}'. Select a file to match:",
                    "choices": choices,
                    "name": "file",
                },
            ]
            response = prompt(question)
            if response["file"] == skip_file_option:
                continue
            if response["file"] == subfolder_ignore_option:
                IGNORED_SUBFOLDERS.add(subfolder_to_ignore)
                #print(f"Ignoring subfolder '{subfolder_to_ignore}' for this session.")
                continue
            if response["file"] == extension_ignore_option:
                IGNORED_EXTENSIONS.add(extension_to_ignore)
                #print(f"Ignoring file extension '{extension_to_ignore}' for this session.")
                continue
            if response["file"] == hardlink_option:
                hardlink_largest_file(matching_files)
                if torrent_file.priority in (0, "0"):
                    print(f"setting file priority of '{torrent_file.name}' to 1.")
                    torrent.file_priority(
                        file_ids=torrent_file.index,
                        priority=1,
                    )  # type: ignore[reportCallIssue]
                #made_change = True
                continue

            selected_file_path = response["file"]
            assert isinstance(selected_file_path, str), f"Invalid response: '{selected_file_path}'"

        elif matching_files:  # Single match.
            selected_file_path = matching_files[0]

        else:
            print(f"{Fore.YELLOW}No matches found for '{original_relpath_str}'!{Style.RESET_ALL}")
            if no_redownload:
                print(f"Setting file priority of '{torrent_file.name}' to 0.")
                if is_dry_run:
                    continue
                torrent.file_priority(
                    file_ids=int(torrent_file.index),
                    priority=0,
                )  # type: ignore[reportCallIssue]
                #made_change = True
            continue

        matched_files.add(selected_file_path)

        new_relative_path: Path = Path(selected_file_path).relative_to(download_path)
        new_relative_path_str: str = new_relative_path.as_posix()
        if new_relative_path == original_relpath:
            #print(f"{original_relpath_str} already synced, left as is")
            continue

        if is_dry_run:
            print(f"{Fore.YELLOW}Dry run:{Style.RESET_ALL}\n{original_relpath_str} ->\n{Fore.YELLOW}{new_relative_path_str}{Style.RESET_ALL}")
            continue

        if use_hardlinks:
            print(f"Hardlinking file:\n{original_relpath_str} <--vv\n{Fore.GREEN}{new_relative_path_str}{Style.RESET_ALL}")
            args_list: list[Path | str] = [original_file_path, selected_file_path]
            hardlink_largest_file(args_list)
            if torrent_file.priority in (0, "0"):
                print(f"setting file priority of '{torrent_file.name}' to 1.")
                torrent.file_priority(
                    file_ids=torrent_file.index,
                    priority=1,
                )  # type: ignore[reportCallIssue]
            #made_change = True
            continue

        try:
            torrent.rename_file(
                file_id=torrent_file.id,
                new_file_name=new_relative_path_str,
            )  # type: ignore[reportCallIssue]
            if torrent_file.priority in (0, "0"):
                print(f"setting file priority of '{torrent_file.name}' to 1.")
                torrent.file_priority(
                    file_ids=torrent_file.index,
                    priority=1,
                )  # type: ignore[reportCallIssue]
        except Conflict409Error as e:
            print(f"{Fore.RED}'{original_relpath_str}' error:", e)
            if original_file_path.suffix.lower() in IGNORED_EXTENSIONS:
                continue
            hardlink_question: list[dict[str, Any]] = [
                {
                    "type": "list",
                    "message": "Would you like to attempt hardlinking instead?",
                    "choices": ["yes", "no"],
                },
            ]
            response = prompt(hardlink_question)
            if response[0] == "yes":
                args_list = [original_file_path, selected_file_path]
                hardlink_largest_file(args_list)
                if torrent_file.priority in (0, "0"):
                    print(f"setting file priority of '{torrent_file.name}' to 1.")
                    torrent.file_priority(
                        file_ids=torrent_file.index,
                        priority=1,
                    )  # type: ignore[reportCallIssue]
                #made_change = True
            elif no_redownload:
                print(f"setting file priority of '{torrent_file.name}' to 0.")
                torrent.file_priority(
                    file_ids=torrent_file.index,
                    priority=0,
                )  # type: ignore[reportCallIssue]
                #made_change = True
        else:
            print(f"Renaming file:\n{original_relpath_str} ->\n{Fore.GREEN}{new_relative_path}{Style.RESET_ALL}")
            #made_change = True

    return made_change

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
            print(f"Skipping {torrent.name}: Search path {input_search_path} must be a sub directory of {raw_download_path}\n")
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
    use_hardlinks: bool = False,
    no_redownload: bool = False,
    is_dry_run: bool = False,
    priority_settings: list[tuple[str, int, bool]] = [],
    delete_nodls: bool = False,
):
    qb_client: Client = init_client()  # this doesn't mean we actually connected yet.
    if input_torrent_hashes:
        torrents: TorrentInfoList = qb_client.torrents.info(torrent_hashes=input_torrent_hashes)
        print("Connected to api!")
        if not torrents:
            sys.exit(f"{Fore.RED}No torrents found matching any of the provided hashes.{Style.RESET_ALL}")
        else:
            found_hashes: list[str] = [torrent["hash"].upper() for torrent in torrents]  # Extracting found hashes
            for hash_value in input_torrent_hashes:
                if hash_value not in found_hashes:
                    print(f"{Fore.RED}Torrent with hash '{hash_value}' not found.{Style.RESET_ALL}")
    elif sync_all or priority_settings:
        torrents = qb_client.torrents_info()
        print("Connected to api!")
        if not torrents:
            sys.exit(f"{Fore.RED}No torrents found found anywhere in your qBittorrent{Style.RESET_ALL}")
    else:
        sys.exit("Nothing to do?")
    # TODO: move above code to separate function that'll return (qb_client, torrents)

    torrent: TorrentDictionary
    for torrent in torrents:
        torrent_hash: str = torrent["hash"].upper()  # type: ignore[union-attr]
        torrent_save_path = Path(torrent.save_path)  # Get the save path of the torrent
        #print(f"\nTarget torrent: {torrent.name}")
        torrent_file: TorrentFile
        for torrent_file in torrent.files:  # type: ignore[reportAttributeAccessIssue]
            assert isinstance(torrent_file, TorrentFile)
            t_filename_check = PureWindowsPath(torrent_file.name.replace("/", "\\")).name.lower()
            t_absolute_path = torrent_save_path / str(torrent_file.name)
            delete_pattern_found = False
            for pattern, priority_value, should_delete in priority_settings:
                if pattern.lower() in t_filename_check.lower():
                    delete_pattern_found = priority_value in {0, "0"}
                    if torrent_file.priority in {int(priority_value), str(priority_value)}:
                        continue
                    print(f"Setting priority of file '{torrent_file.name}' to {priority_value} as it matches the pattern '{pattern}'.")
                    if is_dry_run:
                        continue
                    qb_client.torrents_file_priority(  # type: ignore[reportCallIssue]
                        torrent_hash=torrent_hash,
                        file_ids=torrent_file.index,
                        priority=priority_value,
                    )
                    torrent.file_priority(
                        file_ids=torrent_file.index,
                        priority=priority_value,
                    )  # type: ignore[reportCallIssue]
                    continue
            if not delete_nodls and not delete_pattern_found:  # Don't delete files when the pattern wasn't found. Stops unrelated 0-priority files that weren't scanned from being deleted.
                continue
            torlist: TorrentFilesList = qb_client.torrents.files(torrent_hash, indexes=torrent_file.index)  # type: ignore[reportArgumentType]
            refreshed_torrent: TorrentFile = torlist[0]
            if refreshed_torrent.priority == 0 and (delete_nodls or should_delete):
                if is_dry_run:
                    print(f"dryrun: would delete '{t_absolute_path}'")
                    continue
                if not t_absolute_path.exists() or not t_absolute_path.is_file():
                    continue
                should_resume = False
                try:
                    t_absolute_path.unlink(missing_ok=True)
                except PermissionError as e:
                    if os.name == "nt":
                        should_resume = True
                        print("could not delete, qb might be using it. Pausing torrent temporarily...")
                        # We must pause as qb could still be accessing the file...
                        qb_client.torrents_pause(  # type: ignore[reportCallIssue]
                            torrent_hashes=torrent_hash,
                        )
                        sleep(1)  # Wait for the torrent to pause.
                        try:
                            t_absolute_path.unlink(missing_ok=True)
                        except PermissionError:
                            print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
                            should_resume = False
                        else:
                            print(f"{Fore.MAGENTA}Deleted '{t_absolute_path}'{Style.RESET_ALL}")
                else:
                    print(f"{Fore.MAGENTA}Deleted '{t_absolute_path}'{Style.RESET_ALL}")
                if os.name == "nt" and should_resume:
                    qb_client.torrents_resume(  # type: ignore[reportCallIssue]
                        torrent_hashes=torrent_hash,
                    )
        if priority_settings or delete_nodls:
            continue  # priority settings aren't compatible with any other cli args (yet)
        search_path , download_path = set_search_and_download_paths(
            torrent,
            input_search_path,
            input_download_path,
            use_torrent_save_path_as_search_path,
        )
        if not search_path or not download_path:
            # print(f"Skipping '{torrent.name}', no search path determined.\n")
            continue

        print(f"Search directory '{search_path}'\nDownload directory '{download_path}'")

        # Unfortunately hashing individual files isn't possible (or at least practical), so we match with their sizes.
        torrent_file_sizes: set[int] = {file.size for file in torrent.files if file.size}

        files_in_directory: list[tuple[str, int]] = get_matching_files_in_dir_and_subdirs(search_path, torrent_file_sizes, use_hardlinks)
        #print(f"Found {len(files_in_directory)} matches in search path '{search_path}'")
        made_change: bool = match(torrent, files_in_directory, match_extension, download_path, use_hardlinks, is_dry_run, no_redownload)

        if input_download_path and input_download_path != torrent.save_path and not is_dry_run:
            print(f"Changing torrent save location to {input_download_path}")
            qb_client.torrents_set_location(torrent_hashes=torrent_hash, location=str(input_download_path))
            print(f"{Fore.LIGHTMAGENTA_EX}Rechecking torrent{Style.RESET_ALL}")
            qb_client.torrents_recheck(torrent_hash)

        elif made_change and not is_dry_run:
            print("Change made, rechecking torrent...")
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
    parser.add_argument("-l", "-link", action="store_true", help="Creates hardlinks instead of renaming.")
    parser.add_argument("-nodl", "-no_download", action="store_true", help="If file not found on disk, tell qBittorrent to set priority of that file to 0.")
    parser.add_argument("-p", "--priority", action="append", nargs=2, metavar=('PATTERN', 'PRIORITY'),
                        help="Set priority for files matching the pattern. Pattern is either an int (0-4) or the literal str 'DELETE'.")
    parser.add_argument("-del", "--delete", action="store_true", help="Delete files that have the 'no download' priority.")
    #parser.add_argument("-f", "-find", action="store_true", help="Searches filenames to find matching torrents, when that file can't be found in another torrent.")

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
    # Process priority pattern and values
    priority_settings = []
    if args.priority:
        for pattern, priority in args.priority:
            delete_file = False  # Default delete flag to False
            try:
                priority_value = int(priority)  # Convert priority to an integer
            except ValueError:  # not an int
                if priority.upper() != "DELETE":
                    raise ValueError(f"Bad priority value {priority}, expected a number between 0-3 or literal str 'DELETE'")
                delete_file = True
                priority_value = 0
            pattern_path = Path(pattern)
            if pattern_path.is_absolute() and pattern_path.is_file():  # Check if it's an absolute file path
                pattern_path_str = str(pattern_path)
                pattern_prompt_response = None
                if (  # really validate it's a path.
                    len(pattern_path_str) < 8
                    and len(pattern_path.parts) <= 2
                ):
                    pattern_path_question: list[dict[str, Any]] = [
                        {
                            "type": "list",
                            "message": f"You've entered the pattern '{pattern_path_str}', is this a file on disk with the patterns or the pattern itself?",
                            "choices": ["It's a filepath", "It's a pattern"],
                        },
                    ]
                    pattern_prompt_response = prompt(pattern_path_question)
                if pattern_prompt_response is None or pattern_prompt_response[0] == "It's a filepath":
                    with pattern_path.open(mode="r", encoding="utf-8") as file:
                        for line in file:
                            stripped_line = line.strip()
                            if not stripped_line:
                                continue
                            priority_settings.append((stripped_line, priority_value, delete_file))
            else:
                priority_settings.append((pattern, priority_value, delete_file))

    if args.a and args.input:
        parser.print_help()
        sys.exit("Cannot use both '-a' and input hash in the same command.")
    if not args.input and not args.a and not priority_settings:
        parser.print_help()
        sys.exit("Nothing to do? (must pass `-all` OR an input torrent hash/file)")
    delete_nodls = False
    if args.delete:
        delete_nodls = True
        

    matcher(
        input_torrent_hashes=hashes,
        sync_all=args.a,
        input_search_path=input_search_path,
        input_download_path=input_download_path,
        use_torrent_save_path_as_search_path=args.fd,
        match_extension=args.e,
        use_hardlinks=args.l,
        no_redownload=args.nodl,
        is_dry_run=args.dry,
        priority_settings=priority_settings,
        delete_nodls=delete_nodls,
    )

if __name__ == "__main__":
    main()
