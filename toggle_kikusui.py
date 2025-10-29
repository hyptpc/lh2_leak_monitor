#!/usr/bin/env python3
import socket
import sys
import time

# --- Settings ---
IP = "192.168.20.42"  # IP address of the power supply
PORT = 5025            # Port number
VOLTAGE_ON = 5.0       # Voltage when ON (V)
# --- End Settings ---

def scpi_send(sock, cmd):
    """Send SCPI command (includes wait)"""
    sock.sendall((cmd + "\n").encode("ascii"))
    time.sleep(0.1)  # Wait for stability between commands

def main():
    # 1. Check arguments
    if len(sys.argv) != 2 or sys.argv[1].lower() not in ["on", "off"]:
        print("Usage: python simple_power.py [on|off]")
        sys.exit(1)  # Exit with error

    command = sys.argv[1].lower()

    # 2. Connect to power supply and send commands
    try:
        with socket.create_connection((IP, PORT), timeout=3) as s:
            if command == "on":                
                # Set Voltage, Current, and OCP
                scpi_send(s, f"VOLT {VOLTAGE_ON:.1f}")
                
                # Output ON
                scpi_send(s, "OUTP ON")
                print("Power ON complete.")

            elif command == "off":
                # Output OFF
                scpi_send(s, "OUTP OFF")
                print("Power OFF complete.")


    except Exception as e:
        print(f"Error: Connection or command failed.")
        print(f"Details: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
