import time
import os
import subprocess
import threading
import requests
import sys
from dotenv import load_dotenv

# --- Load environment variables from .env file ---
load_dotenv()

# Define ANSI color codes
class COLORS:
  HEADER = '\033[95m'
  OKBLUE = '\033[94m'
  OKCYAN = '\033[96m'
  OKGREEN = '\033[92m'
  WARNING = '\033[93m'
  FAIL = '\033[91m'
  ENDC = '\033[0m'
  BOLD = '\033[1m'
  DIM = '\033[2m' # Faint/Grey

# Define the lock
action_lock = threading.Lock()

# --- Settings ---
# Specify the path to the text file you want to monitor
# FILE_TO_WATCH = '/home/oper/share/monitor-tmp/H2tgtPresentStatus.txt'
FILE_TO_WATCH = 'debug/H2tgtPresentStatus.txt'
# Polling interval (seconds)
POLLING_INTERVAL = 1

# --- Action Settings (Customize these) ---
PYTHON_SCRIPT_TO_RUN = "turn_off_hv.py"

# --- Remote Pi Settings (Pi B) ---
TARGET_PI_USER = "sks" 
TARGET_PI_HOSTS = [
  "192.168.20.12",
  "192.168.20.13"
]
REMOTE_COMMANDS_TO_RUN = [
  "sudo /usr/sbin/uhubctl -l 1-1 -p 1 -a 0",
  "sudo /usr/sbin/uhubctl -l 1-1 -p 2 -a 0",
  "sudo /usr/sbin/uhubctl -l 1-1 -p 3 -a 0",
  "sudo /usr/sbin/uhubctl -l 1-1 -p 4 -a 0"
]
WAIT_TIME_SECONDS = 15 * 60 # 15 minutes

# Define trigger files
SKIP_TRIGGER_FILE = "/tmp/skip.now"     # Skips wait, runs in 5s
CANCEL_TRIGGER_FILE = "/tmp/cancel.now"   # Cancels uhubctl
EXTEND_TRIGGER_FILE = "/tmp/extend.now"   # Resets the wait timer
# ------------------------------------

# --- 3. Load Discord URL from .env file ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# -----------------------------------------------

# --- 4. Check if the URL was loaded ---
if not DISCORD_WEBHOOK_URL:
  print(f"{COLORS.FAIL}ERROR: DISCORD_WEBHOOK_URL not found in .env file.{COLORS.ENDC}")
  print(f"{COLORS.DIM}Please create a '.env' file in the same directory and add:{COLORS.ENDC}")
  print(f"{COLORS.DIM}DISCORD_WEBHOOK_URL=https://your-url-here{COLORS.ENDC}")
  sys.exit(1) # Exit if the URL is not configured
# -----------------------------------------------


def read_h2_alert_status(filepath):
  """
  Safely read the status file and extract the 'Alert_H2leak' value.
  """
  try:
    if not os.path.exists(filepath):
      return None
    with open(filepath, 'r', encoding='utf-8') as f:
      for line in f:
        if line.strip().startswith("Alert_H2leak:"):
          parts = line.split(':')
          if len(parts) > 1:
            value = parts[1].strip()
            return value
      print(f"{COLORS.FAIL}Warning: 'Alert_H2leak:' not found in {filepath}{COLORS.ENDC}")
      return None
  except FileNotFoundError:
    print(f"{COLORS.FAIL}Error: {filepath} not found.{COLORS.ENDC}")
    return None
  except Exception as e:
    print(f"{COLORS.FAIL}File read error: {e}{COLORS.ENDC}")
    return None

def send_discord_notification(message):
  """
  Sends a message to the configured Discord Webhook.
  """
  # URL is guaranteed to exist because of the check at startup
  data = {
    "content": f"[{time.ctime()}] {message}",
    "username": "LH2 Monitor Bot"
  }
  try:
    response = requests.post(DISCORD_WEBHOOK_URL, json=data)
    if response.status_code == 204:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 4: Discord notification sent.{COLORS.ENDC}")
    else:
      print(f"  {COLORS.FAIL}(Action Log) ERROR: Failed to send Discord notification (Status code: {response.status_code}){COLORS.ENDC}")
  except Exception as e:
    print(f"  {COLORS.FAIL}(Action Log) ERROR sending Discord notification: {e}{COLORS.ENDC}")


def run_actions():
  """
  Run the sequence of actions. This wait can be skipped,
  canceled, or extended using trigger files.
  """
  if not action_lock.acquire(blocking=False):
    print(f"  {COLORS.FAIL}(Action Log) ERROR: Could not acquire lock, actions already running.{COLORS.ENDC}")
    return

  try:
    print(f"\n  {COLORS.OKCYAN}(Action Log) --- Starting Action Sequence (Lock Acquired) ---{COLORS.ENDC}")

    # 1. Run Python script
    print(f"  {COLORS.OKCYAN}(Action Log) Action 1: Running Python script '{PYTHON_SCRIPT_TO_RUN}'...{COLORS.ENDC}")
    try:
      for port in range(4):
        subprocess.run(["python3", PYTHON_SCRIPT_TO_RUN, "--ip_last", "12", "--port", str(port)], check=True)
      for port in range(4):
        subprocess.run(["python3", PYTHON_SCRIPT_TO_RUN, "--ip_last", "13", "--port", str(port)], check=True)
      print(f"  {COLORS.OKCYAN}(Action Log) Action 1: Script finished.{COLORS.ENDC}")
    except Exception as e:
      print(f"  {COLORS.FAIL}(Action Log) ERROR running script: {e}{COLORS.ENDC}")

    # 2. Wait (with trigger logic)
    future_epoch_time = time.time() + WAIT_TIME_SECONDS
    future_time_str = time.ctime(future_epoch_time)
    print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Waiting for {WAIT_TIME_SECONDS} seconds ({WAIT_TIME_SECONDS / 60.0} minutes)...{COLORS.ENDC}")
    print(f"  {COLORS.OKCYAN}(Action Log)   -> USB power off scheduled for: {COLORS.BOLD}{future_time_str}{COLORS.ENDC}")
    print(f"  {COLORS.OKCYAN}(Action Log)   -> (To skip:   {COLORS.BOLD}touch {SKIP_TRIGGER_FILE}{COLORS.ENDC}{COLORS.OKCYAN}){COLORS.ENDC}")
    print(f"  {COLORS.OKCYAN}(Action Log)   -> (To cancel: {COLORS.BOLD}touch {CANCEL_TRIGGER_FILE}{COLORS.ENDC}{COLORS.OKCYAN}){COLORS.ENDC}")
    print(f"  {COLORS.OKCYAN}(Action Log)   -> (To extend: {COLORS.BOLD}touch {EXTEND_TRIGGER_FILE}{COLORS.ENDC}{COLORS.OKCYAN}){COLORS.ENDC}")

    start_time = time.time()
    wait_duration = WAIT_TIME_SECONDS
    run_uhubctl = True # Default: run uhubctl after wait
    wait_skipped = False

    while (time.time() - start_time) < wait_duration:
      
      # Check for CANCEL (Priority 1)
      if os.path.exists(CANCEL_TRIGGER_FILE):
        print(f"\n  {COLORS.WARNING}(Action Log) Action 2: CANCEL file found! Aborting uhubctl.{COLORS.ENDC}")
        os.remove(CANCEL_TRIGGER_FILE)
        run_uhubctl = False # Do not run uhubctl
        break # Exit wait loop

      # Check for SKIP (Priority 2)
      if os.path.exists(SKIP_TRIGGER_FILE):
        print(f"\n  {COLORS.WARNING}(Action Log) Action 2: SKIP file found! Running in 5 seconds...{COLORS.ENDC}")
        os.remove(SKIP_TRIGGER_FILE)
        wait_skipped = True
        break # Exit wait loop

      # Check for EXTEND (Priority 3)
      if os.path.exists(EXTEND_TRIGGER_FILE):
        print(f"\n  {COLORS.WARNING}(Action Log) Action 2: EXTEND file found! Resetting timer.{COLORS.ENDC}")
        os.remove(EXTEND_TRIGGER_FILE)
        start_time = time.time() # Reset the timer
        new_future_time = time.ctime(time.time() + wait_duration)
        print(f"  {COLORS.OKCYAN}(Action Log)   -> WAIT EXTENDED. New shutdown time: {COLORS.BOLD}{new_future_time}{COLORS.ENDC}")
        
      time.sleep(1)

    # --- End of wait loop ---

    # Cleanup any lingering files (safety)
    for f in [SKIP_TRIGGER_FILE, CANCEL_TRIGGER_FILE, EXTEND_TRIGGER_FILE]:
      if os.path.exists(f):
        try: os.remove(f)
        except Exception as e: print(f"{COLORS.FAIL}Error cleaning up trigger file {f}: {e}{COLORS.ENDC}")

    # Process wait results
    if wait_skipped:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait skipped. Waiting 5s...{COLORS.ENDC}")
      time.sleep(5)
    elif run_uhubctl:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait finished (Timeout).{COLORS.ENDC}")
    else:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait Canceled by user.{COLORS.ENDC}")


    # 3. Run uhubctl REMOTELY via SSH
    all_success = True
    if run_uhubctl:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Running remote uhubctl commands...{COLORS.ENDC}")
      try:
        for host in TARGET_PI_HOSTS:
          print(f"  {COLORS.OKCYAN}(Action Log)   Targeting Host: {host}{COLORS.ENDC}")
          for cmd in REMOTE_COMMANDS_TO_RUN:
            ssh_command_list = [
                "ssh", "-T",
                "-o", "StrictHostKeyChecking=no",
                f"{TARGET_PI_USER}@{host}",
                cmd
            ]
            print(f"  {COLORS.OKCYAN}(Action Log)     Executing: {' '.join(ssh_command_list)}{COLORS.ENDC}")
            subprocess.run(ssh_command_list, check=True)
        print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Remote uhubctl commands finished for all hosts.{COLORS.ENDC}")
      except Exception as e:
        print(f"  {COLORS.FAIL}(Action Log) ERROR running remote uhubctl: {e}{COLORS.ENDC}")
        all_success = False
    else:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Skipped (Canceled).{COLORS.ENDC}")


    # 4. Send Discord notification
    print(f"  {COLORS.OKCYAN}(Action Log) Action 4: Sending Discord notification...{COLORS.ENDC}")
    
    if not run_uhubctl:
      send_discord_notification(f"Process CANCELED by user. Ran '{PYTHON_SCRIPT_TO_RUN}' but uhubctl was NOT executed.")
    elif all_success:
      send_discord_notification(f"Process complete. Ran '{PYTHON_SCRIPT_TO_RUN}' and executed all uhubctl commands.")
    else:
      send_discord_notification(f"Process complete. Ran '{PYTHON_SCRIPT_TO_RUN}' but ERROR occurred during uhubctl execution.")
      
    print(f"  {COLORS.OKCYAN}(Action Log) --- Action Sequence Finished ---{COLORS.ENDC}")

  finally:
    action_lock.release()
    print(f"  {COLORS.OKCYAN}(Action Log) Lock Released.{COLORS.ENDC}")


def monitor_status_change(filepath, interval):
  """
  Monitors the 'Alert_H2leak' value for a change from '0' to '1'.
  """
  print(f"{COLORS.HEADER}Monitoring started: {filepath} (Interval: {interval}s){COLORS.ENDC}")
  print(f"{COLORS.HEADER}Will trigger actions on 'Alert_H2leak:' -> '1' change. (Ctrl+C to stop){COLORS.ENDC}")
  last_status = '0'
  initial_content = read_h2_alert_status(filepath)
  if initial_content is not None:
    last_status = initial_content
    print(f"Current initial state (Alert_H2leak): '{last_status}'")
  else:
    print(f"{COLORS.FAIL}File not found or key missing. Assuming '{last_status}' state.{COLORS.ENDC}")
  try:
    while True:
      current_status = read_h2_alert_status(filepath)
      if current_status is None:
        print(f"{COLORS.FAIL}Monitoring... (File read error or key missing) [Last check: {time.ctime()}] {COLORS.ENDC}\r", end="")
        time.sleep(interval)
        continue
      if current_status == '1' and last_status == '0':
        print(f"\n{COLORS.WARNING}{COLORS.BOLD}--- LH2 leak flag is detected ---{COLORS.ENDC}")
        print(f"{COLORS.WARNING}Timestamp: {time.ctime()}{COLORS.ENDC}")
        if action_lock.locked():
          pass
        else:
          print(f"{COLORS.WARNING}Status changed from '0' to '1'. Starting actions in background...{COLORS.ENDC}")
          action_thread = threading.Thread(target=run_actions)
          action_thread.start()
        last_status = current_status
      elif current_status == '0' and last_status == '1':
        print(f"\n{COLORS.OKBLUE}({time.ctime()}) Status changed back to '0'.{COLORS.ENDC}")
        last_status = current_status
      elif current_status != last_status:
        last_status = current_status
      if last_status == '1':
          status_color = COLORS.WARNING
      else:
          status_color = COLORS.OKGREEN
      print(f"{COLORS.DIM}Monitoring... (Alert_H2leak: {status_color}{last_status}{COLORS.ENDC}{COLORS.DIM}) [Last check: {time.ctime()}] {COLORS.ENDC}\r", end="")
      time.sleep(interval)
  except KeyboardInterrupt:
    print("\nMonitoring stopped.")


if __name__ == "__main__":
    
    # Clean up ALL trigger files on startup
    print(f"{COLORS.HEADER}Trigger files:{COLORS.ENDC}")
    print(f"{COLORS.HEADER}  Skip:   {SKIP_TRIGGER_FILE}{COLORS.ENDC}")
    print(f"{COLORS.HEADER}  Cancel: {CANCEL_TRIGGER_FILE}{COLORS.ENDC}")
    print(f"{COLORS.HEADER}  Extend: {EXTEND_TRIGGER_FILE}{COLORS.ENDC}")

    trigger_files = [SKIP_TRIGGER_FILE, CANCEL_TRIGGER_FILE, EXTEND_TRIGGER_FILE]
    
    for f in trigger_files:
        if os.path.exists(f):
            try:
                os.remove(f)
                print(f"{COLORS.WARNING}Found and removed old trigger file on startup: {f}{COLORS.ENDC}")
            except Exception as e:
                print(f"{COLORS.FAIL}ERROR: Could not remove old trigger file '{f}'. Exiting: {e}{COLORS.ENDC}")
                sys.exit(1) # Exit if we can't clean up
    
    # Start the main monitoring logic
    monitor_status_change(FILE_TO_WATCH, POLLING_INTERVAL)
