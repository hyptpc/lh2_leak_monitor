import caen_libs.caenhvwrapper as hv
import sys
import time

host = '192.168.20.51' # caenhv1
systype = 'SY1527'
linktype = 'TCPIP'

# --- Target Settings ---
TARGET_SLOT = 8
# (Monitoring parameters removed as they are not needed)
# PARAM_V_SET = 'V0Set'
# PARAM_V_MON = 'VMon'
# PARAM_I_MON = 'IMon'

# --- Channel Groups ---
PMT_CHANNELS = [
    0, 1, 2, 3, 4,  # UP-PMT 1-5
    5, 6, 7, 8, 9 # Down-PMT 1-5
]

BOOSTER_CHANNELS = [
    10, 11, 12, # Booster 1 (1-3)
    13, 14, 15, # Booster 2 (1-3)
    16, 17, 18  # Booster 3 (1-3)
]

# --- Wait Times (seconds) ---
BOOSTER_RAMP_WAIT_SEC = 5 # Booster ramp-down wait time

#______________________________________________________________________________
def main():
  try:
    with hv.Device.open(hv.SystemType[systype], hv.LinkType[linktype],
                         host, 'admin', 'admin') as device:

      print(f"--- Powering OFF Slot {TARGET_SLOT} (A1535) ---")

      # === Step 1: Power OFF Boosters ===
      print(f"\n[Step 1] Turning OFF Booster channels ({len(BOOSTER_CHANNELS)} channels)...")
      device.set_ch_param(TARGET_SLOT, BOOSTER_CHANNELS, 'Pw', 0)

      print(f"Waiting {BOOSTER_RAMP_WAIT_SEC} seconds for Boosters to ramp down...")
      time.sleep(BOOSTER_RAMP_WAIT_SEC)

      # === Step 2: Power OFF PMTs ===
      print(f"\n[Step 2] Turning OFF PMT channels ({len(PMT_CHANNELS)} channels)...")
      device.set_ch_param(TARGET_SLOT, PMT_CHANNELS, 'Pw', 0)

      # === Step 3: Final Status Check (REMOVED) ===
      print("\n[Info] Power-off commands have been sent.")
      print("--------------------------------------------------")

      # The monitoring loop has been removed as requested.

  except hv.Error as e:
    print(f"\n[CAEN HV Error] {e}", file=sys.stderr)
  except KeyboardInterrupt:
    print("\n[Notice] Operation interrupted by user.", file=sys.stderr)
  except Exception as e:
    print(f"\n[Error] An error occurred: {e}", file=sys.stderr)

#______________________________________________________________________________
if __name__ == '__main__':
  main()
