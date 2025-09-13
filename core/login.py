import subprocess
import re
import sys
import json
from datetime import datetime

STATUS_FILE = "status.json"

def update_status(status, url=None):
    """Update status.json with status, timestamp, and optional URL."""
    data = {
        "status": status,
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "url": url
    }
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[STATUS] {status} | URL: {url or 'N/A'}")
    except Exception as e:
        print(f"[!] Failed to update status: {e}")

def get_current_status():
    """Read and return the current status data from status.json."""
    try:
        with open(STATUS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        print("[!] Status file not found.")
        return None
    except json.JSONDecodeError:
        print("[!] Status file corrupted or empty.")
        return None
    except Exception as e:
        print(f"[!] Failed to read status: {e}")
        return None


def extract_url_from_line(line):
    match = re.search(r'https://.*cloudflare.*', line)
    return match.group(0) if match else None

def detect_auth_success(line):
    # Adjust this based on actual cloudflared success message
    return "successfully logged" in line.lower()

def run_command_live(cmd, log_file_path="output.log"):
    update_status("starting")
    current_url = None
    process = None
    log_file = None
    status_set_to_error = False  # Track if error was reported

    try:
        if log_file_path:
            log_file = open(log_file_path, 'w')

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True
        )

        for line in iter(process.stdout.readline, ''):
            if not line:
                break
            sys.stdout.write(line)
            sys.stdout.flush()
            if log_file:
                log_file.write(line)
                log_file.flush()

            # Extract URL if present and update status if changed
            url = extract_url_from_line(line)
            if url and url != current_url:
                current_url = url
                update_status("url_found", current_url)

            # Detect successful authentication
            if detect_auth_success(line):
                update_status("completed", current_url)

    except KeyboardInterrupt:
        print("\n[!] Interrupted. Killing subprocess...")
        update_status("manually_stopped", current_url)
        if process:
            process.terminate()
            process.wait()
        raise

    except Exception as e:
        print(f"\n[!] Error: {e}")
        update_status("error", current_url)
        status_set_to_error = True
        if process:
            process.terminate()
            process.wait()
        raise

    finally:
        if process:
            process.wait()  # Ensure process is done
        if log_file:
            log_file.write(f"\n[+] Process exited with code {process.returncode if process else 'N/A'}\n")
            log_file.close()

    # Check return code AFTER process finishes
    return_code = process.returncode if process else -1
    if return_code != 0 and not status_set_to_error:
        update_status(f"error_exit_{return_code}", current_url)
    elif not status_set_to_error:
        update_status("completed", current_url)

    print(f"\n[+] Process exited with code {return_code}")
    return return_code
