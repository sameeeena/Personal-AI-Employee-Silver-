#!/usr/bin/env python3
"""
File Watcher for Silver Tier AI Employee

Monitors the Inbox folder for new files, moves them to Needs_Action,
and maintains ingestion logs with timestamps and duplicate handling.
"""

import os
import sys
import time
import shutil
import logging
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from typing import Set


class IngestionLogManager:
    """Manages the ingestion log file with thread-safe operations."""

    def __init__(self, log_file_path: str):
        self.log_file_path = Path(log_file_path)
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Setup logging to file
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file_path, mode='a'),
                logging.StreamHandler()  # Also print to console
            ]
        )
        self.logger = logging.getLogger(__name__)

        # Initialize the log file with header if it doesn't exist
        if not self.log_file_path.exists():
            self._write_log_header()

    def _write_log_header(self):
        """Write the initial header for the ingestion log."""
        header = f"""# Ingestion Log
## Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---
"""
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            f.write(header)

    def log_ingestion(self, source_path: str, dest_path: str, status: str, message: str = ""):
        """Log an ingestion event to the log file."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {status} | Source: {source_path} | Destination: {dest_path}"
        if message:
            log_entry += f" | Message: {message}"
        log_entry += "\n"

        # Append to file
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)

        # Also log to configured logger
        if status == "SUCCESS":
            self.logger.info(f"Ingested: {source_path} -> {dest_path}")
        elif status == "DUPLICATE":
            self.logger.warning(f"Duplicate: {source_path} -> {dest_path}")
        else:
            self.logger.error(f"Failed: {source_path} -> {dest_path} | {message}")


class FileWatcherHandler(FileSystemEventHandler):
    """Handles file system events for the File Watcher."""

    def __init__(self, inbox_dir: str, needs_action_dir: str, log_manager: IngestionLogManager):
        super().__init__()
        self.inbox_dir = Path(inbox_dir)
        self.needs_action_dir = Path(needs_action_dir)
        self.log_manager = log_manager
        self.processed_files: Set[str] = set()

        # Ensure destination directory exists
        self.needs_action_dir.mkdir(parents=True, exist_ok=True)

        # Log initialization
        self.log_manager.logger.info(f"File Watcher initialized:")
        self.log_manager.logger.info(f"  Inbox: {self.inbox_dir}")
        self.log_manager.logger.info(f"  Needs Action: {self.needs_action_dir}")
        self.log_manager.logger.info(f"  Log File: {self.log_manager.log_file_path}")

    def on_created(self, event):
        """Handle file creation events in the monitored directory."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        self._process_new_file(file_path)

    def on_moved(self, event):
        """Handle file move events in the monitored directory."""
        if event.is_directory:
            return

        # Only handle moves INTO the inbox (not out of it)
        dest_path = Path(event.dest_path)
        if dest_path.parent == self.inbox_dir:
            self._process_new_file(dest_path)

    def _process_new_file(self, file_path: Path):
        """Process a new file detected in the inbox."""
        try:
            # Verify file exists and is in inbox directory
            if not file_path.exists() or file_path.parent != self.inbox_dir:
                return

            # Wait briefly to ensure file is completely written
            time.sleep(0.5)

            # Check if file is still being written to
            initial_size = file_path.stat().st_size
            time.sleep(0.1)
            if file_path.stat().st_size != initial_size:
                # File is still growing, wait a bit more
                time.sleep(1)

            # Generate destination path with duplicate handling
            dest_path = self._generate_destination_path(file_path)

            # Move the file to Needs_Action directory
            try:
                shutil.move(str(file_path), str(dest_path))

                # Log successful move
                self.log_manager.log_ingestion(
                    str(file_path),
                    str(dest_path),
                    "SUCCESS",
                    "File moved to Needs_Action"
                )

                # Add to processed files set
                self.processed_files.add(str(file_path))

            except FileExistsError:
                # Handle duplicate file names gracefully
                counter = 1
                stem = dest_path.stem
                suffix = dest_path.suffix
                parent = dest_path.parent

                while dest_path.exists():
                    new_name = f"{stem}_{counter}{suffix}"
                    dest_path = parent / new_name
                    counter += 1

                shutil.move(str(file_path), str(dest_path))
                self.log_manager.log_ingestion(
                    str(file_path),
                    str(dest_path),
                    "DUPLICATE",
                    f"Renamed to avoid conflict: {dest_path.name}"
                )

            except Exception as e:
                self.log_manager.log_ingestion(
                    str(file_path),
                    str(dest_path),
                    "ERROR",
                    f"Failed to move file: {str(e)}"
                )

        except Exception as e:
            self.log_manager.logger.error(f"Error processing file {file_path}: {str(e)}")

    def _generate_destination_path(self, source_path: Path) -> Path:
        """Generate destination path in Needs_Action directory."""
        filename = source_path.name
        return self.needs_action_dir / filename


def main():
    """Main entry point for the File Watcher."""
    # Define absolute paths
    base_dir = Path(__file__).parent.absolute()
    inbox_dir = base_dir / "Inbox"
    needs_action_dir = base_dir / "Needs_Action"
    log_dir = base_dir / "logs"
    log_file_path = log_dir / "file_ingestion_log.md"

    # Ensure required directories exist
    inbox_dir.mkdir(parents=True, exist_ok=True)
    needs_action_dir.mkdir(parents=True, exist_ok=True)

    # Initialize log manager
    log_manager = IngestionLogManager(str(log_file_path))

    # Initialize file handler
    event_handler = FileWatcherHandler(
        str(inbox_dir),
        str(needs_action_dir),
        log_manager
    )

    # Initialize observer
    observer = Observer()
    observer.schedule(event_handler, str(inbox_dir), recursive=False)

    try:
        observer.start()
        log_manager.logger.info("File Watcher started")
        log_manager.logger.info(f"Monitoring: {inbox_dir}")
        log_manager.logger.info("Press Ctrl+C to stop...")

        # Print initial system status
        print("\n" + "="*50)
        print("FILE WATCHER STATUS")
        print("="*50)
        print(f"Inbox Directory: {inbox_dir}")
        print(f"Needs Action Directory: {needs_action_dir}")
        print(f"Log File: {log_file_path}")
        print(f"Status: Running")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*50)

        # Keep the program running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        observer.stop()
        log_manager.logger.info("File Watcher stopped by user")

    except Exception as e:
        log_manager.logger.error(f"Unexpected error in file watcher: {str(e)}")

    finally:
        observer.join()
        log_manager.logger.info("File Watcher terminated")


if __name__ == "__main__":
    main()
