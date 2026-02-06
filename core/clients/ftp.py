"""
FTP Client for file operations.

Provides FTP read/write operations for packing list CSV/PDF files.
"""

import fnmatch
import logging
from ftplib import FTP, FTP_TLS
from functools import lru_cache
from io import BytesIO
from typing import Optional

from core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class FTPClient:
    """
    FTP client for file operations.

    Usage:
        with FTPClient(host, username, password) as ftp:
            files = ftp.list_files("/path", "*.csv")
            data = ftp.download("/path/file.csv")
            ftp.upload("/path/file.pdf", pdf_bytes)
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 21,
        use_tls: bool = False,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_tls = use_tls
        self._ftp: Optional[FTP] = None

    def connect(self) -> None:
        """Establish FTP connection."""
        if self._ftp is not None:
            return

        try:
            if self.use_tls:
                self._ftp = FTP_TLS()
            else:
                self._ftp = FTP()

            self._ftp.connect(self.host, self.port)
            self._ftp.login(self.username, self.password)

            if self.use_tls:
                self._ftp.prot_p()  # Enable data encryption

            logger.info(f"Connected to FTP server: {self.host}")
        except Exception as e:
            logger.error(f"Failed to connect to FTP server: {e}")
            self._ftp = None
            raise

    def disconnect(self) -> None:
        """Close FTP connection."""
        if self._ftp is not None:
            try:
                self._ftp.quit()
            except Exception:
                pass
            self._ftp = None
            logger.debug("Disconnected from FTP server")

    def __enter__(self) -> "FTPClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()

    def _ensure_connected(self) -> FTP:
        """Ensure we have an active connection."""
        if self._ftp is None:
            self.connect()
        return self._ftp

    def list_files(self, path: str, pattern: str = "*") -> list[dict]:
        """
        List files in a directory matching a pattern.

        Args:
            path: Directory path to list
            pattern: Glob pattern to match (e.g., "PL_*.csv")

        Returns:
            List of dicts with file info: {"name": str, "path": str, "size": int}
        """
        ftp = self._ensure_connected()
        files = []

        try:
            # Get file listing with details
            entries = []
            ftp.retrlines(f"LIST {path}", entries.append)

            for entry in entries:
                # Parse FTP LIST output (varies by server, but common format is:
                # -rw-r--r-- 1 owner group size date name
                parts = entry.split()
                if len(parts) < 9:
                    continue

                # Check if it's a file (not directory)
                if entry.startswith("d"):
                    continue

                name = " ".join(parts[8:])  # Handle filenames with spaces
                size = int(parts[4]) if parts[4].isdigit() else 0

                # Apply pattern filter
                if fnmatch.fnmatch(name, pattern):
                    full_path = f"{path.rstrip('/')}/{name}"
                    files.append({
                        "name": name,
                        "path": full_path,
                        "size": size,
                    })

            logger.debug(f"Found {len(files)} files matching '{pattern}' in {path}")
            return files

        except Exception as e:
            logger.error(f"Failed to list files in {path}: {e}")
            raise

    def list_directories(self, path: str) -> list[str]:
        """
        List subdirectories in a directory.

        Args:
            path: Directory path to list

        Returns:
            List of directory names
        """
        ftp = self._ensure_connected()
        dirs = []

        try:
            entries = []
            ftp.retrlines(f"LIST {path}", entries.append)

            for entry in entries:
                parts = entry.split()
                if len(parts) < 9:
                    continue

                # Check if it's a directory
                if entry.startswith("d"):
                    name = " ".join(parts[8:])
                    if name not in (".", ".."):
                        dirs.append(name)

            return dirs

        except Exception as e:
            logger.error(f"Failed to list directories in {path}: {e}")
            raise

    def download(self, remote_path: str) -> bytes:
        """
        Download a file from FTP.

        Args:
            remote_path: Full path to the file

        Returns:
            File contents as bytes
        """
        ftp = self._ensure_connected()
        buffer = BytesIO()

        try:
            ftp.retrbinary(f"RETR {remote_path}", buffer.write)
            data = buffer.getvalue()
            logger.debug(f"Downloaded {len(data)} bytes from {remote_path}")
            return data

        except Exception as e:
            logger.error(f"Failed to download {remote_path}: {e}")
            raise

    def upload(self, remote_path: str, data: bytes) -> bool:
        """
        Upload a file to FTP.

        Args:
            remote_path: Full path for the file
            data: File contents as bytes

        Returns:
            True if successful
        """
        ftp = self._ensure_connected()
        buffer = BytesIO(data)

        try:
            ftp.storbinary(f"STOR {remote_path}", buffer)
            logger.info(f"Uploaded {len(data)} bytes to {remote_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to upload to {remote_path}: {e}")
            raise

    def file_exists(self, remote_path: str) -> bool:
        """
        Check if a file exists on FTP.

        Args:
            remote_path: Full path to check

        Returns:
            True if file exists
        """
        ftp = self._ensure_connected()

        try:
            # Try to get file size - fails if file doesn't exist
            ftp.size(remote_path)
            return True
        except Exception:
            return False

    def find_files_recursive(
        self,
        base_path: str,
        pattern: str = "*",
        max_depth: int = 3,
    ) -> list[dict]:
        """
        Recursively find files matching a pattern.

        Args:
            base_path: Starting directory
            pattern: Glob pattern to match
            max_depth: Maximum directory depth to search

        Returns:
            List of file info dicts
        """
        all_files = []

        def search_dir(path: str, depth: int):
            if depth > max_depth:
                return

            # Get files in current directory
            try:
                files = self.list_files(path, pattern)
                all_files.extend(files)
            except Exception as e:
                logger.warning(f"Could not list files in {path}: {e}")

            # Recurse into subdirectories
            try:
                subdirs = self.list_directories(path)
                for subdir in subdirs:
                    subpath = f"{path.rstrip('/')}/{subdir}"
                    search_dir(subpath, depth + 1)
            except Exception as e:
                logger.warning(f"Could not list directories in {path}: {e}")

        search_dir(base_path, 0)
        return all_files


class NoOpFTPClient(FTPClient):
    """
    No-op FTP client for testing without actual FTP access.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(host="noop", username="", password="")
        logger.info("Using NoOp FTP client (no actual FTP operations)")

    def connect(self) -> None:
        logger.info("[NOOP FTP] Would connect to FTP server")

    def disconnect(self) -> None:
        logger.info("[NOOP FTP] Would disconnect from FTP server")

    def list_files(self, path: str, pattern: str = "*") -> list[dict]:
        logger.info(f"[NOOP FTP] Would list files in {path} matching {pattern}")
        return []

    def list_directories(self, path: str) -> list[str]:
        logger.info(f"[NOOP FTP] Would list directories in {path}")
        return []

    def download(self, remote_path: str) -> bytes:
        logger.info(f"[NOOP FTP] Would download {remote_path}")
        return b""

    def upload(self, remote_path: str, data: bytes) -> bool:
        logger.info(f"[NOOP FTP] Would upload {len(data)} bytes to {remote_path}")
        return True

    def file_exists(self, remote_path: str) -> bool:
        logger.info(f"[NOOP FTP] Would check if {remote_path} exists")
        return False

    def find_files_recursive(
        self, base_path: str, pattern: str = "*", max_depth: int = 3
    ) -> list[dict]:
        logger.info(f"[NOOP FTP] Would search {base_path} for {pattern}")
        return []


@lru_cache(maxsize=1)
def get_ftp_client(settings: Optional[Settings] = None) -> FTPClient:
    """
    Get or create a cached FTP client instance.

    Args:
        settings: Optional settings (uses get_settings() if not provided)

    Returns:
        FTPClient or NoOpFTPClient if FTP not configured
    """
    if settings is None:
        settings = get_settings()

    if not settings.ftp_host or not settings.ftp_username:
        logger.info("FTP not configured, using NoOp client")
        return NoOpFTPClient()

    return FTPClient(
        host=settings.ftp_host,
        username=settings.ftp_username,
        password=settings.ftp_password,
        port=settings.ftp_port,
        use_tls=settings.ftp_use_tls,
    )
