#!/usr/bin/env python3
"""
WhatsApp Watcher for Silver Tier AI Employee

Monitors WhatsApp messages (via exported chat files or integration)
and creates task files in the Inbox folder.
"""

import os
import sys
import time
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from typing import Set, Dict, Optional, List
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

load_dotenv()

# Configuration
WHATSAPP_EXPORT_DIR = os.getenv("WHATSAPP_EXPORT_DIR", "")
POLL_INTERVAL = 30  # seconds


class WhatsAppLogManager:
    """Manages the WhatsApp watcher log file."""

    def __init__(self, log_file_path: str):
        self.log_file_path = Path(log_file_path)
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_file_path, mode='a'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

        if not self.log_file_path.exists():
            self._write_log_header()

    def _write_log_header(self):
        """Write the initial header for the WhatsApp log."""
        header = f"""# WhatsApp Watcher Log
## Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---
"""
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            f.write(header)

    def log_message(self, contact: str, preview: str, status: str, message: str = ""):
        """Log a WhatsApp message processing event."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {status} | From: {contact} | Preview: {preview[:50]}..."
        if message:
            log_entry += f" | {message}"
        log_entry += "\n"

        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)

        if status == "SUCCESS":
            self.logger.info(f"Processed: {contact} - {preview[:30]}")
        elif status == "DUPLICATE":
            self.logger.warning(f"Duplicate: {contact} - {preview[:30]}")
        else:
            self.logger.error(f"Failed: {contact} - {preview[:30]} | {message}")


class WhatsAppChatParser:
    """Parses WhatsApp chat export files."""

    def __init__(self, chat_file: Path):
        self.chat_file = chat_file
        self.messages = []

    def parse(self) -> List[Dict]:
        """Parse the WhatsApp chat file and extract messages."""
        messages = []

        try:
            with open(self.chat_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            current_message = None

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # WhatsApp export format: [DD/MM/YY, HH:MM:SS] Name: Message
                # Or: DD/MM/YY, HH:MM:SS - Name: Message
                parsed = self._parse_line(line)
                if parsed:
                    if current_message and parsed.get('continuation'):
                        # Continuation of previous message
                        current_message['body'] += "\n" + parsed['body']
                    else:
                        if current_message:
                            messages.append(current_message)
                        current_message = parsed

            if current_message:
                messages.append(current_message)

        except Exception as e:
            logging.error(f"Error parsing chat file {self.chat_file}: {str(e)}")

        self.messages = messages
        return messages

    def _parse_line(self, line: str) -> Optional[Dict]:
        """Parse a single line from the chat export."""
        import re

        # Pattern 1: [DD/MM/YY, HH:MM:SS] Name: Message
        pattern1 = r'\[(\d{2}/\d{2}/\d{2},\s*\d{2}:\d{2}:\d{2})\]\s*(.+?):\s*(.*)'
        # Pattern 2: DD/MM/YY, HH:MM:SS - Name: Message
        pattern2 = r'(\d{2}/\d{2}/\d{2},\s*\d{2}:\d{2}:\d{2})\s*-\s*(.+?):\s*(.*)'

        for pattern in [pattern1, pattern2]:
            match = re.match(pattern, line)
            if match:
                return {
                    'timestamp': match.group(1),
                    'sender': match.group(2),
                    'body': match.group(3),
                    'continuation': False
                }

        # Check if this is a continuation of a previous message
        # (no timestamp, just text)
        if not re.match(r'^\d{2}/\d{2}/\d{2}', line):
            return {
                'timestamp': '',
                'sender': '',
                'body': line,
                'continuation': True
            }

        return None


class WhatsAppWatcherHandler(FileSystemEventHandler):
    """Handles file system events for WhatsApp chat files."""

    def __init__(self, watch_dir: str, inbox_dir: str, log_manager: WhatsAppLogManager):
        super().__init__()
        self.watch_dir = Path(watch_dir)
        self.inbox_dir = Path(inbox_dir)
        self.log_manager = log_manager
        self.processed_files: Set[str] = set()
        self.message_hashes: Set[str] = set()

        # Ensure directories exist
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        self.log_manager.logger.info("WhatsApp Watcher Handler initialized")
        self.log_manager.logger.info(f"Watch Directory: {self.watch_dir}")
        self.log_manager.logger.info(f"Inbox Folder: {self.inbox_dir}")

    def on_created(self, event):
        """Handle new chat file creation."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() in ['.txt', '.zip']:
            self._process_chat_file(file_path)

    def on_modified(self, event):
        """Handle chat file modifications."""
        if event.is_directory:
            return

        file_path = Path(event.src_path)
        if file_path.suffix.lower() in ['.txt', '.zip']:
            self._process_chat_file(file_path)

    def _process_chat_file(self, file_path: Path):
        """Process a WhatsApp chat file."""
        try:
            # Skip if already processed recently
            file_hash = hashlib.md5(str(file_path).encode()).hexdigest()
            if file_hash in self.message_hashes:
                self.log_manager.logger.debug(f"Skipping already processed file: {file_path.name}")
                return

            # Wait for file to be fully written
            time.sleep(0.5)

            # Parse the chat file
            parser = WhatsAppChatParser(file_path)
            messages = parser.parse()

            if not messages:
                return

            # Create task files for new messages
            new_message_count = 0
            for msg in messages:
                if msg.get('continuation') or not msg.get('sender'):
                    continue

                # Create unique hash for this message
                msg_hash = hashlib.md5(
                    f"{msg['timestamp']}{msg['sender']}{msg['body']}".encode()
                ).hexdigest()

                if msg_hash in self.message_hashes:
                    continue

                # Create task file
                filepath = self._create_task_file(msg, file_path.name)
                self.message_hashes.add(msg_hash)
                new_message_count += 1

                self.log_manager.log_message(
                    contact=msg['sender'],
                    preview=msg['body'],
                    status="SUCCESS",
                    message=f"Created: {filepath.name}"
                )

            # Mark file as processed
            self.message_hashes.add(file_hash)
            self.processed_files.add(str(file_path))

            self.log_manager.logger.info(
                f"Processed {file_path.name}: {new_message_count} new message(s)"
            )

        except Exception as e:
            self.log_manager.logger.error(f"Error processing chat file {file_path}: {str(e)}")

    def _create_task_file(self, message: Dict, source_file: str) -> Path:
        """Create a task file for a WhatsApp message."""
        timestamp = datetime.now()
        task_id = f"TSK-{timestamp.strftime('%Y%m%d')}-{timestamp.strftime('%H%M%S')}"

        # Sanitize contact name for filename
        safe_contact = "".join(
            c for c in message['sender'] if c.isalnum() or c in " -_"
        ).strip()[:30]
        if not safe_contact:
            safe_contact = "unknown"

        filename = f"{task_id}_whatsapp_{safe_contact}.md"
        filepath = self.inbox_dir / filename

        content = f"""---
task_id: {task_id}
source: whatsapp
sender: {message['sender']}
timestamp: {timestamp.isoformat()}
received_at: {message.get('timestamp', '')}
status: Inbox
priority: normal
tags: [whatsapp, message, received, automated]
source_file: {source_file}
---

# WhatsApp Message Received

## Details

| Field | Value |
|-------|-------|
| **From** | {message['sender']} |
| **Timestamp** | {message.get('timestamp', 'N/A')} |
| **Source File** | {source_file} |

## Message

```
{message['body']}
```

---
*Processed by WhatsApp Watcher*
"""

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        return filepath


class WhatsAppWatcher:
    """Main WhatsApp Watcher class."""

    def __init__(self, watch_dir: str, inbox_dir: str, log_manager: WhatsAppLogManager):
        self.watch_dir = Path(watch_dir)
        self.inbox_dir = Path(inbox_dir)
        self.log_manager = log_manager

        # Ensure directories exist
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

    def run(self):
        """Main loop for the WhatsApp watcher."""
        handler = WhatsAppWatcherHandler(
            str(self.watch_dir),
            str(self.inbox_dir),
            self.log_manager
        )

        observer = Observer()
        observer.schedule(handler, str(self.watch_dir), recursive=False)

        try:
            observer.start()
            self.log_manager.logger.info("WhatsApp Watcher started")
            self.log_manager.logger.info(f"Monitoring: {self.watch_dir}")
            self.log_manager.logger.info(f"Poll Interval: {POLL_INTERVAL} seconds")
            self.log_manager.logger.info("Press Ctrl+C to stop...")

            print("\n" + "="*50)
            print("WHATSAPP WATCHER STATUS")
            print("="*50)
            print(f"Watch Directory: {self.watch_dir}")
            print(f"Inbox Folder: {self.inbox_dir}")
            print(f"Poll Interval: {POLL_INTERVAL} seconds")
            print(f"Status: Running")
            print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*50)

            while True:
                time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            observer.stop()
            self.log_manager.logger.info("WhatsApp Watcher stopped by user")

        except Exception as e:
            self.log_manager.logger.error(f"Error in WhatsApp Watcher: {str(e)}")

        finally:
            observer.join()
            self.log_manager.logger.info("WhatsApp Watcher terminated")


def main():
    """Main entry point for the WhatsApp Watcher."""
    base_dir = Path(__file__).parent.absolute()

    # Use configured export dir or default to whatsapp_exports folder
    watch_dir = Path(WHATSAPP_EXPORT_DIR) if WHATSAPP_EXPORT_DIR else base_dir / "whatsapp_exports"
    inbox_dir = base_dir / "Inbox"
    log_dir = base_dir / "logs"
    log_file_path = log_dir / "whatsapp_watcher_log.md"

    # Ensure directories exist
    watch_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Initialize log manager
    log_manager = WhatsAppLogManager(str(log_file_path))

    # Initialize and run watcher
    watcher = WhatsAppWatcher(str(watch_dir), str(inbox_dir), log_manager)
    watcher.run()


if __name__ == "__main__":
    main()
