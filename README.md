## Usage

### 1. Start the Monitor
Run `monitor.py` in your terminal.

python monitor.py

The script will start and begin monitoring the file specified in `FILE_TO_WATCH`.

---

### 2. Test (Optional)
To test if the monitor is working, open a **separate terminal** and run `toggle.sh`.

```
# (If you don't have execute permissions: chmod +x toggle.sh)
./debug/toggle.sh
```

This will flip the `Alert_H2leak` value, and `monitor.py` should detect the change (it will log in yellow).

---

### 3. Control During Wait (After Alert)
When an alert (`0`->`1`) is detected, the script enters a 15-minute wait (Action 2).
If you want to override this wait, create one of the following "trigger files" from a **separate terminal**.

* **To Skip Wait (Runs USB power-off in 5 seconds):**
    ```
      touch /tmp/skip.now
    ```
* **To Cancel USB Power-Off (Stops the wait and ends the sequence):**
    ```
        touch /tmp/cancel.now
    ```
* **To Extend Wait (Resets the timer to 15 minutes):**
    ```
        touch /tmp/extend.now
    ```
