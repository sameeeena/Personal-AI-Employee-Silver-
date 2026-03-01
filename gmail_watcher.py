#!/usr/bin/env python3
"""
Gmail Watcher for Silver Tier AI Employee

Monitors Gmail inbox for new emails and creates task files in the Inbox folder.
Uses IMAP to poll Gmail at regular intervals.
"""

import os
import sys
import time
import email
import logging
import imaplib
from pathlib import Path
from datetime import datetime, timedelta
from email.header import decode_header
from dotenv import load_dotenv
from typing import Set, Optional

load_dotenv()

# Configuration
IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993
FROM_EMAIL = os.getenv("SMTP_USERNAME", "")
PASSWORD = os.getenv("SMTP_PASSWORD", "")
POLL_INTERVAL = 30  # seconds


class GmailLogManager:
    """Manages the Gmail watcher log file."""

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
        """Write the initial header for the Gmail log."""
        header = f"""# Gmail Watcher Log
## Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
---
"""
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            f.write(header)

    def log_email(self, subject: str, sender: str, status: str, message: str = ""):
        """Log an email processing event."""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"[{timestamp}] {status} | From: {sender} | Subject: {subject}"
        if message:
            log_entry += f" | {message}"
        log_entry += "\n"

        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(log_entry)

        if status == "SUCCESS":
            self.logger.info(f"Processed: {subject} from {sender}")
        elif status == "DUPLICATE":
            self.logger.warning(f"Duplicate: {subject} from {sender}")
        else:
            self.logger.error(f"Failed: {subject} from {sender} | {message}")


class GmailWatcher:
    """Watches Gmail inbox for new emails."""

    def __init__(self, inbox_dir: str, log_manager: GmailLogManager):
        self.inbox_dir = Path(inbox_dir)
        self.log_manager = log_manager
        self.processed_emails: Set[str] = set()
        self.last_check = datetime.now()
        self.seen_ids: Set[str] = set()

        # Ensure inbox directory exists
        self.inbox_dir.mkdir(parents=True, exist_ok=True)

        self.log_manager.logger.info("Gmail Watcher initialized")
        self.log_manager.logger.info(f"Inbox folder: {self.inbox_dir}")
        self.log_manager.logger.info(f"POLL_INTERVAL: {POLL_INTERVAL} seconds")

    def connect(self) -> Optional[imaplib.IMAP4_SSL]:
        """Connect to Gmail IMAP server."""
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
            mail.login(FROM_EMAIL, PASSWORD)
            mail.select("inbox")
            return mail
        except Exception as e:
            self.log_manager.logger.error(f"Failed to connect to Gmail: {str(e)}")
            return None

    def decode_mime_words(self, s: str) -> str:
        """Decode MIME encoded words in email headers."""
        decoded = ""
        for part, encoding in decode_header(s):
            if isinstance(part, bytes):
                try:
                    decoded += part.decode(encoding or 'utf-8', errors='replace')
                except:
                    decoded += part.decode('latin-1', errors='replace')
            else:
                decoded += part
        return decoded

    def get_email_body(self, msg) -> str:
        """Extract the plain text body from an email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))

                if content_type == "text/plain" and "attachment" not in content_disposition:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        body = part.get_payload(decode=True).decode(charset, errors='replace')
                        break
                    except:
                        continue
        else:
            try:
                charset = msg.get_content_charset() or 'utf-8'
                body = msg.get_payload(decode=True).decode(charset, errors='replace')
            except:
                body = ""

        return body

    def create_task_file(self, subject: str, sender: str, body: str, date: str, msg_id: str) -> Path:
        """Create a task file for a new email."""
        timestamp = datetime.now()
        task_id = f"TSK-{timestamp.strftime('%Y%m%d')}-{timestamp.strftime('%H%M%S')}"

        # Sanitize filename
        safe_subject = "".join(c for c in subject if c.isalnum() or c in " -_").strip()[:50]
        if not safe_subject:
            safe_subject = "no_subject"

        filename = f"{task_id}_gmail_{safe_subject}.md"
        filepath = self.inbox_dir / filename

        content = f"""---
task_id: {task_id}
source: gmail
sender: {sender}
recipient: {FROM_EMAIL}
timestamp: {timestamp.isoformat()}
received_at: {date}
subject: {subject}
message_id: {msg_id}
status: Inbox
priority: normal
tags: [gmail, email, received, automated]
---

# Email Received from Gmail

## Details

| Field | Value |
|-------|-------|
| **From** | {sender} |
| **To** | {FROM_EMAIL} |
| **Subject** | {subject} |
| **Date** | {date} |
| **Message ID** | {msg_id} |

## Message Body

```
{body}
```

---
*Processed by Gmail Watcher*
"""

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)

        return filepath

    def check_for_new_emails(self):
        """Check Gmail for new emails and create task files."""
        mail = self.connect()
        if not mail:
            return

        try:
            # Search for unread emails
            status, messages = mail.search(None, "UNSEEN")

            if status != "OK":
                return

            email_ids = messages[0].split()

            if not email_ids:
                return

            self.log_manager.logger.info(f"Found {len(email_ids)} new email(s)")

            for email_id in email_ids:
                try:
                    email_id_str = email_id.decode()

                    # Skip if already processed
                    if email_id_str in self.seen_ids:
                        continue

                    # Fetch the email
                    status, msg_data = mail.fetch(email_id, "(RFC822)")

                    if status != "OK":
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    # Extract email details
                    subject = self.decode_mime_words(msg.get("Subject", "No Subject"))
                    sender = self.decode_mime_words(msg.get("From", "Unknown"))
                    date = msg.get("Date", "")
                    msg_id = msg.get("Message-ID", email_id_str)
                    body = self.get_email_body(msg)

                    # Create task file
                    filepath = self.create_task_file(subject, sender, body, date, msg_id)

                    # Log success
                    self.log_manager.log_email(
                        subject=subject,
                        sender=sender,
                        status="SUCCESS",
                        message=f"Created task file: {filepath.name}"
                    )

                    # Mark as seen
                    mail.store(email_id, "+FLAGS", "\\Seen")
                    self.seen_ids.add(email_id_str)

                except Exception as e:
                    self.log_manager.logger.error(f"Error processing email {email_id}: {str(e)}")

        except Exception as e:
            self.log_manager.logger.error(f"Error checking emails: {str(e)}")

        finally:
            try:
                mail.close()
                mail.logout()
            except:
                pass

    def run(self):
        """Main loop for the Gmail watcher."""
        self.log_manager.logger.info("Gmail Watcher started")
        self.log_manager.logger.info(f"Checking every {POLL_INTERVAL} seconds")
        self.log_manager.logger.info("Press Ctrl+C to stop...")

        print("\n" + "="*50)
        print("GMAIL WATCHER STATUS")
        print("="*50)
        print(f"Email Account: {FROM_EMAIL}")
        print(f"Inbox Folder: {self.inbox_dir}")
        print(f"Poll Interval: {POLL_INTERVAL} seconds")
        print(f"Status: Running")
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*50)

        try:
            while True:
                self.check_for_new_emails()
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            self.log_manager.logger.info("Gmail Watcher stopped by user")


def main():
    """Main entry point for the Gmail Watcher."""
    base_dir = Path(__file__).parent.absolute()
    inbox_dir = base_dir / "Inbox"
    log_dir = base_dir / "logs"
    log_file_path = log_dir / "gmail_watcher_log.md"

    # Ensure directories exist
    inbox_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Initialize log manager
    log_manager = GmailLogManager(str(log_file_path))

    # Initialize and run watcher
    watcher = GmailWatcher(str(inbox_dir), log_manager)
    watcher.run()


if __name__ == "__main__":
    main()
