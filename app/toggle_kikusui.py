#!/usr/bin/env python3
import socket
import sys
import time

# --- Settings ---
IP_BASE = "192.168.20." # Common part of the IP address
PORT = 5025            # Port number
VOLTAGE_ON = 5.0       # Voltage when ON (V)
# --- End Settings ---

def scpi_send(sock, cmd):
    """Send SCPI command (includes wait)"""
    sock.sendall((cmd + "\n").encode("ascii"))
    time.sleep(0.1)  # Wait for stability between commands

def scpi_query(sock, cmd):
    """Send SCPI query and return response string"""
    scpi_send(sock, cmd)
    data = sock.recv(1024).decode("ascii").strip()
    return data

def main():
    USAGE = f"Usage: {sys.argv[0]} <ip_last_octet> [on|off]"
    
    # --- 1. Parse Arguments ---
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(USAGE)
        sys.exit(1)

    try:
        # Construct the IP address from the first argument
        ip_last_octet = sys.argv[1]
        int(ip_last_octet) # Simple check to ensure it's a number
        IP = IP_BASE + ip_last_octet
    except ValueError:
        print(f"Error: Invalid IP octet '{sys.argv[1]}'. Must be a number.")
        print(USAGE)
        sys.exit(1)

    # Determine the mode (status check or control)
    mode = "status"
    command = ""
    if len(sys.argv) == 3:
        mode = "control"
        command = sys.argv[2].lower()
        if command not in ["on", "off"]:
            print(f"Error: Invalid command '{command}'. Must be 'on' or 'off'.")
            print(USAGE)
            sys.exit(1)

    # --- 2. Connect and Execute ---
    try:
        print(f"Connecting to {IP}:{PORT}...")
        with socket.create_connection((IP, PORT), timeout=3) as s:
            
            if mode == "status":
                # Only IP octet provided: check status
                print(f"Querying status for {IP}...")
                outp_state = scpi_query(s, "OUTP?")
                meas_v = scpi_query(s, "MEAS:VOLT?")
                meas_i = scpi_query(s, "MEAS:CURR?")
                print(f"  Output: {'ON' if outp_state.strip() == '1' else 'OFF'}")
                print(f"  Measured Voltage: {float(meas_v):.3f} V")
                print(f"  Measured Current: {float(meas_i):.3f} A")

            elif mode == "control":
                # IP octet and [on|off] provided: send command
                if command == "on":
                    scpi_send(s, f"VOLT {VOLTAGE_ON:.1f}")
                    scpi_send(s, "OUTP ON")
                    print(f"Power ON complete for {IP}. Set voltage = {VOLTAGE_ON:.1f} V")
                elif command == "off":
                    scpi_send(s, "OUTP OFF")
                    print(f"Power OFF complete for {IP}.")

    except Exception as e:
        print(f"{Colors.FAIL}Error: Connection or command failed for {IP}.{Colors.ENDC}")
        print(f"Details: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # Define simple color class for error, as COLORS class is not global
    class Colors:
        FAIL = '\033[91m'
        ENDC = '\033[0m'
        
    main()
