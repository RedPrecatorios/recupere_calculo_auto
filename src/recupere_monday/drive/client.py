from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import structlog

logger = structlog.get_logger(__name__)

PDF_MIME = "application/pdf"
FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Pastas de data no padrão dd-mm-aa ou dd-mm-aaaa (ex: 16-06-26, 16-06-2026)
DATE_FOLDER_PATTERN = re.compile(r"^\d{2}-\d{2}-\d{2,4}$")


@dataclass(frozen=True)
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    modified_time: str
    relative_path: str = ""

    @property
    def safe_local_name(self) -> str:
        """Nome único achatado (fallback)."""
        if self.relative_path:
            prefix = self.relative_path.replace("/", "__")
            return f"{prefix}__{self.name}"
        return f"{self.file_id}_{self.name}"

    def local_path_under(self, base_dir: Path) -> Path:
        """Caminho local espelhando a estrutura do Drive."""
        if self.relative_path:
            return base_dir / self.relative_path / self.name
        return base_dir / self.name


class DriveClient:
    def __init__(self, service_account_file: Path) -> None:
        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_file),
            scopes=SCOPES,
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def list_pdfs_in_folder(
        self,
        folder_id: str,
        *,
        recursive: bool = True,
        only_date_named_folders: bool = False,
        root_folder_name: str | None = None,
        max_depth: int = 10,
    ) -> list[DriveFile]:
        if not recursive:
            return self._list_pdfs_direct(folder_id)

        files = self._collect_pdfs_recursive(
            folder_id=folder_id,
            path_prefix="",
            depth=0,
            max_depth=max_depth,
            filter_date_folders_at_root=only_date_named_folders,
            root_folder_name=root_folder_name,
            is_root=True,
        )
        logger.info(
            "drive_files_listed",
            count=len(files),
            folder_id=folder_id,
            recursive=True,
            only_date_folders=only_date_named_folders,
            root_folder_name=root_folder_name,
        )
        return files

    def _list_pdfs_direct(self, folder_id: str) -> list[DriveFile]:
        query = (
            f"'{folder_id}' in parents "
            f"and mimeType='{PDF_MIME}' "
            "and trashed=false"
        )
        files: list[DriveFile] = []
        for item in self._paginate_files(query):
            files.append(self._to_drive_file(item))
        logger.info("drive_files_listed", count=len(files), folder_id=folder_id, recursive=False)
        return files

    def list_date_folders(self, folder_id: str) -> list[str]:
        """Lista pastas de data (dd-mm-aa / dd-mm-aaaa) filhas diretas, da mais antiga à mais nova."""
        query = (
            f"'{folder_id}' in parents "
            f"and mimeType='{FOLDER_MIME}' "
            "and trashed=false"
        )
        names: list[str] = []
        for item in self._paginate_files(query):
            name = str(item.get("name") or "").strip()
            if DATE_FOLDER_PATTERN.match(name):
                names.append(name)
        names.sort(key=_date_folder_sort_key)
        logger.info(
            "drive_date_folders_listed",
            count=len(names),
            folder_id=folder_id,
            oldest=names[0] if names else None,
            newest=names[-1] if names else None,
        )
        return names

    def _collect_pdfs_recursive(
        self,
        folder_id: str,
        path_prefix: str,
        depth: int,
        max_depth: int,
        filter_date_folders_at_root: bool,
        root_folder_name: str | None,
        is_root: bool,
    ) -> list[DriveFile]:
        if depth > max_depth:
            logger.warning("drive_max_depth_reached", folder_id=folder_id, depth=depth)
            return []

        pdfs: list[DriveFile] = []
        query = f"'{folder_id}' in parents and trashed=false"

        for item in self._paginate_files(query):
            mime_type = item.get("mimeType", "")
            name = item.get("name", "")

            if mime_type == FOLDER_MIME:
                if is_root and filter_date_folders_at_root and not DATE_FOLDER_PATTERN.match(name):
                    logger.debug("drive_folder_skipped", name=name, reason="date_pattern_mismatch")
                    continue
                if is_root and root_folder_name and name != root_folder_name:
                    logger.debug(
                        "drive_folder_skipped",
                        name=name,
                        reason="root_folder_name_mismatch",
                        expected=root_folder_name,
                    )
                    continue

                child_prefix = f"{path_prefix}/{name}" if path_prefix else name
                pdfs.extend(
                    self._collect_pdfs_recursive(
                        folder_id=item["id"],
                        path_prefix=child_prefix,
                        depth=depth + 1,
                        max_depth=max_depth,
                        filter_date_folders_at_root=False,
                        root_folder_name=None,
                        is_root=False,
                    )
                )
            elif mime_type == PDF_MIME:
                pdfs.append(
                    DriveFile(
                        file_id=item["id"],
                        name=name,
                        mime_type=PDF_MIME,
                        modified_time=item.get("modifiedTime", ""),
                        relative_path=path_prefix,
                    )
                )

        return pdfs

    def _paginate_files(self, query: str) -> list[dict]:
        items: list[dict] = []
        page_token: str | None = None

        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageToken=page_token,
                    pageSize=100,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return items

    @staticmethod
    def _to_drive_file(item: dict) -> DriveFile:
        return DriveFile(
            file_id=item["id"],
            name=item["name"],
            mime_type=item.get("mimeType", PDF_MIME),
            modified_time=item.get("modifiedTime", ""),
        )

    def download_file(self, file_id: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(fileId=file_id, supportsAllDrives=True)

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        destination.write_bytes(buffer.getvalue())
        logger.info("drive_file_downloaded", file_id=file_id, path=str(destination))
        return destination


def parse_drive_date_folder(name: str) -> datetime | None:
    """Converte nome de pasta dd-mm-aa ou dd-mm-aaaa em datetime (só data)."""
    text = str(name or "").strip()
    if not DATE_FOLDER_PATTERN.match(text):
        return None
    day_s, month_s, year_s = text.split("-")
    year = int(year_s)
    if len(year_s) == 2:
        year += 2000
    try:
        return datetime(year, int(month_s), int(day_s))
    except ValueError:
        return None


def _date_folder_sort_key(name: str) -> tuple:
    parsed = parse_drive_date_folder(name)
    if parsed is None:
        return (1, name)
    return (0, parsed.toordinal())
