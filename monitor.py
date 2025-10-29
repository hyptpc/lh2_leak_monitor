#!/usr/bin/env python3
import time
import os
import subprocess
import threading
import requests
import sys
from dotenv import load_dotenv

# --- Load environment variables from .env file ---
load_dotenv()

# --- 1. Define Absolute Paths ---
# Get the directory where this script (monitor.py) is located
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
# ---------------------------------

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
# Use the absolute path
# FILE_TO_WATCH = os.path.join(SCRIPT_DIR, 'debug/H2tgtPresentStatus.txt')
FILE_TO_WATCH = '/home/sks/share/monitor-tmp/H2tgtPresentStatus.txt'
# Polling interval (seconds)
POLLING_INTERVAL = 1

# --- Action Settings (Customize these) ---
# Use the absolute path
HV_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "turn_off_hv.py")
KIKUSUI_SCRIPT_PATH = os.path.join(SCRIPT_DIR, "toggle_kikusui.py")

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

# --- Load Discord URL from .env file ---
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")
# -----------------------------------------------

# --- Check if the URL was loaded ---
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
      print(f"  {COLORS.OKCYAN}(Action Log) Action 5: Discord notification sent.{COLORS.ENDC}")
    else:
      print(f"  {COLORS.FAIL}(Action Log) Action 5: ERROR sending Discord notification (Status code: {response.status_code}){COLORS.ENDC}")
  except Exception as e:
    print(f"  {COLORS.FAIL}(Action Log) Action 5: ERROR sending Discord notification: {e}{COLORS.ENDC}")


def run_actions():
  """
  Run the sequence of actions. This wait can be skipped,
  canceled, or extended using trigger files.
  """
  if not action_lock.acquire(blocking=False):
    print(f"  {COLORS.FAIL}(Action Log) ERROR: Could not acquire lock, actions already running.{COLORS.ENDC}")
    return

  # Store error messages for the final report
  error_messages = []

  try:
    print(f"\n  {COLORS.OKCYAN}(Action Log) --- Starting Action Sequence (Lock Acquired) ---{COLORS.ENDC}")

    # 1. Run Python script (HV Off)
    print(f"  {COLORS.OKCYAN}(Action Log) Action 1: Running Python script '{HV_SCRIPT_PATH}' (HV OFF)...{COLORS.ENDC}")
    try:
      for port in range(4):
        subprocess.run(["python3", HV_SCRIPT_PATH, "--ip_last", "12", "--port", str(port)], check=True)
      for port in range(4):
        subprocess.run(["python3", HV_SCRIPT_PATH, "--ip_last", "13", "--port", str(port)], check=True)
      print(f"  {COLORS.OKCYAN}(Action Log) Action 1: Script finished.{COLORS.ENDC}")
    except Exception as e:
      error_msg = f"Action 1 (HV Off) failed: {e}"
      print(f"  {COLORS.FAIL}(Action Log) ERROR: {error_msg}{COLORS.ENDC}")
      error_messages.append(error_msg)

    # 2. Wait (with trigger logic AND countdown)
    future_epoch_time = time.time() + WAIT_TIME_SECONDS
    future_time_str = time.ctime(future_epoch_time)
    
    start_time = time.time()
    wait_duration = WAIT_TIME_SECONDS
    run_shutdown = True # Default: run shutdown after wait
    wait_skipped = False

    while True:
      elapsed = time.time() - start_time
      remaining = wait_duration - elapsed

      if remaining <= 0:
        break # Time's up
        
      # Check for CANCEL (Priority 1)
      if os.path.exists(CANCEL_TRIGGER_FILE):
        os.system('clear')
        print(f"\n{COLORS.WARNING}(Action Log) Action 2: CANCEL file found! Aborting shutdown.{COLORS.ENDC}")
        os.remove(CANCEL_TRIGGER_FILE)
        run_shutdown = False # Do not run shutdown
        break # Exit wait loop

      # Check for SKIP (Priority 2)
      if os.path.exists(SKIP_TRIGGER_FILE):
        os.system('clear')
        print(f"\n{COLORS.WARNING}(Action Log) Action 2: SKIP file found! Running in 5 seconds...{COLORS.ENDC}")
        os.remove(SKIP_TRIGGER_FILE)
        wait_skipped = True
        break # Exit wait loop

      # Check for EXTEND (Priority 3)
      if os.path.exists(EXTEND_TRIGGER_FILE):
        os.system('clear')
        print(f"\n{COLORS.WARNING}(Action Log) Action 2: EXTEND file found! Resetting timer.{COLORS.ENDC}")
        os.remove(EXTEND_TRIGGER_FILE)
        start_time = time.time() # Reset the timer
        wait_duration = WAIT_TIME_SECONDS # Ensure it uses the original duration
        new_future_time = time.ctime(time.time() + wait_duration)
        print(f"  {COLORS.OKCYAN}(Action Log)   -> WAIT EXTENDED. New shutdown time: {COLORS.BOLD}{new_future_time}{COLORS.ENDC}")
        # Continue the loop
        
      os.system('clear') # Clear the terminal
      print(f"{COLORS.OKCYAN}{COLORS.BOLD}--- ACTION 2: WAITING FOR SHUTDOWN ---{COLORS.ENDC}")
      print(f"{COLORS.OKCYAN}Shutdown scheduled for: {COLORS.BOLD}{time.ctime(start_time + wait_duration)}{COLORS.ENDC}")
      print(f"{COLORS.OKCYAN}Trigger files (use 'touch' in another terminal):{COLORS.ENDC}")
      print(f"  {COLORS.BOLD}Skip:  {SKIP_TRIGGER_FILE}{COLORS.ENDC}")
      print(f"  {COLORS.BOLD}Cancel:{CANCEL_TRIGGER_FILE}{COLORS.ENDC}")
      print(f"  {COLORS.BOLD}Extend:{EXTEND_TRIGGER_FILE}{COLORS.ENDC}")
      print("-" * 30)

      mins_left, secs_left = divmod(int(remaining), 60)
      countdown_str = f"{mins_left:02}:{secs_left:02}"
      print(f"{COLORS.WARNING}{COLORS.BOLD}Waiting... {countdown_str} remaining {COLORS.ENDC}")
        
      time.sleep(1)

    # --- End of wait loop ---
    os.system('clear') # Clear the countdown

    # Cleanup any lingering files (safety)
    for f in [SKIP_TRIGGER_FILE, CANCEL_TRIGGER_FILE, EXTEND_TRIGGER_FILE]:
      if os.path.exists(f):
        try: os.remove(f)
        except Exception as e: print(f"{COLORS.FAIL}Error cleaning up trigger file {f}: {e}{COLORS.ENDC}")

    # Process wait results
    if wait_skipped:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait skipped. Waiting 5s...{COLORS.ENDC}")
      time.sleep(5)
    elif run_shutdown:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait finished (Timeout).{COLORS.ENDC}")
    else:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 2: Wait Canceled by user.{COLORS.ENDC}")


    # 3. Run uhubctl REMOTELY via SSH
    if run_shutdown:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Running remote uhubctl commands (USB OFF)...{COLORS.ENDC}")
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
        print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Remote uhubctl commands finished.{COLORS.ENDC}")
      except Exception as e:
        error_msg = f"Action 3 (uhubctl) failed: {e}"
        print(f"  {COLORS.FAIL}(Action Log) ERROR: {error_msg}{COLORS.ENDC}")
        error_messages.append(error_msg)
    else:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 3: Skipped (Canceled).{COLORS.ENDC}")

    # 4. turn off kikusui for BLC2 (Main Output Off)
    if run_shutdown:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 4: Running Python script '{KIKUSUI_SCRIPT_PATH}' (Main OFF)...{COLORS.ENDC}")
      try:
        subprocess.run(["python3", KIKUSUI_SCRIPT_PATH, "off"], check=True)
        print(f"  {COLORS.OKCYAN}(Action Log) Action 4: Script finished.{COLORS.ENDC}")
      except Exception as e:
        error_msg = f"Action 4 (Kikusui BLC2 Off) failed: {e}"
        print(f"  {COLORS.FAIL}(Action Log) ERROR: {error_msg}{COLORS.ENDC}")
        error_messages.append(error_msg)
    else:
      print(f"  {COLORS.OKCYAN}(Action Log) Action 4: Skipped (Canceled).{COLORS.ENDC}")

    # 5. Send Discord notification
    print(f"  {COLORS.OKCYAN}(Action Log) Action 5: Sending Discord notification...{COLORS.ENDC}")
    
    if not run_shutdown:
      send_discord_notification(f"Process CANCELED by user. Ran Action 1 (HV Off) but Actions 3 & 4 were NOT executed.")
    elif error_messages:
      # Send a failure message
      errors_str = "; ".join(error_messages)
      send_discord_notification(f"Process FAILED. Errors occurred: {errors_str}")
    else:
      # All actions (that ran) succeeded
      send_discord_notification(f"Process complete. All actions (HV Off, uhubctl, Kikusui Off) executed successfully.")
      
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
      
      # If actions are running, the 'run_actions' function controls the screen
      if action_lock.locked():
        time.sleep(interval)
        continue

      current_status = read_h2_alert_status(filepath)
      if current_status is None:
        # Handle file read error
        os.system('clear')
        print(f"{COLORS.FAIL}Monitoring... (File read error or key missing){COLORS.ENDC}")
        print(f"{COLORS.FAIL}Last check: {time.ctime()}{COLORS.ENDC}")
        time.sleep(interval)
        continue
      
      if current_status == '1' and last_status == '0':
        os.system('clear') # Clear screen for the log
        print(f"\n{COLORS.WARNING}{COLORS.BOLD}--- LH2 leak flag is detected ---{COLORS.ENDC}")
        print(f"{COLORS.WARNING}Timestamp: {time.ctime()}{COLORS.ENDC}")
        
        # (Lock is guaranteed to be free here, but we check just in case)
        if not action_lock.locked():
          print(f"{COLORS.WARNING}Status changed from '0' to '1'. Starting actions in background...{COLORS.ENDC}")
          action_thread = threading.Thread(target=run_actions)
          action_thread.start()
        
        last_status = current_status
      
      elif current_status == '0' and last_status == '1':
        os.system('clear') # Clear screen for the log
        print(f"\n{COLORS.OKBLUE}({time.ctime()}) Status changed back to '0'.{COLORS.ENDC}")
        last_status = current_status
      
      elif current_status != last_status:
        last_status = current_status

      os.system('clear')
      print(f"{COLORS.HEADER}--- LH2 MONITOR ---{COLORS.ENDC}")
      
      if last_status == '1':
          status_color = COLORS.WARNING
          status_text = "ALERT DETECTED"
      else:
          status_color = COLORS.OKGREEN
          status_text = "Normal"
          
      print(f"Status (Alert_H2leak): {status_color}{last_status} ({status_text}){COLORS.ENDC}")
      print(f"{COLORS.DIM}Monitoring file: {filepath}{COLORS.ENDC}")
      print(f"{COLORS.DIM}Last check: {time.ctime()}{COLORS.ENDC}")
      print("\n(Monitoring... Ctrl+C to stop)")
      
      time.sleep(interval)
  
  except KeyboardInterrupt:
    print("\nMonitoring stopped.")
  finally:
    # Ensure cursor is visible on exit
    print("\033[?25h") 


if __name__ == "__main__":
    
    # Hide cursor
    print("\033[?25l", end="")

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
    
    print("--- Starting Monitor ---")
    time.sleep(1) # Give user time to read startup messages
    
    # Start the main monitoring logic
    monitor_status_change(FILE_TO_WATCH, POLLING_INTERVAL)
