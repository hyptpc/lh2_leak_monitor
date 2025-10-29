import requests
import json
import argparse
import time

# --- Constants ---
DEFAULT_CONTROLLER_PORT = 8000
IP_BASE = "192.168.20." # Assuming the first three parts are fixed
RETRY_DELAY_SECONDS = 2

def send_turn_off_command(controller_ip_last: str, target_port_id: int):
    """
    Sends the TURN_OFF command to the specified port on the HV controller.
    Retries indefinitely until successful.
    """
    controller_ip = IP_BASE + controller_ip_last
    api_url = f"http://{controller_ip}:{DEFAULT_CONTROLLER_PORT}/serial/command"
    payload = {
        "port_id": target_port_id,
        "command_type": "TURN_OFF",
    }

    while True: # Loop indefinitely until success
        try:
            # Send HTTP POST request
            response = requests.post(
                api_url,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=10 # Set timeout to 10 seconds
            )

            # Check server response
            response.raise_for_status() # Raise exception for non-2xx status codes

            # If successful, break the loop
            # No print statement for success as requested
            # print("Success!")
            # print(f"Server response: {response.json()}")
            break # Exit the while loop on success

        except requests.exceptions.ConnectionError as e:
            print(f"\nError: Could not connect to controller at {controller_ip}:{DEFAULT_CONTROLLER_PORT}.")
            print(f"Details: {e}")
            print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")

        except requests.exceptions.Timeout:
            print(f"\nError: Request to controller timed out.")
            print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")

        except requests.exceptions.RequestException as e:
            print(f"\nAn error occurred: {e}")
            try:
                # Attempt to print server error details if available
                print(f"Server error details: {response.text}")
            except NameError:
                pass # response might not be defined if connection failed early
            except Exception as detail_err:
                 print(f"(Could not get error details: {detail_err})")
            print(f"Retrying in {RETRY_DELAY_SECONDS} seconds...")

        # Wait before retrying
        time.sleep(RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send TURN_OFF command to HV Controller.")
    parser.add_argument(
        "--ip_last",
        type=str,
        required=True,
        help="The last section of the HV Controller's IP address (e.g., '40' for 192.168.20.40)."
    )
    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="The target Port ID to turn off."
    )

    args = parser.parse_args()

    # Call the function to send the command with retries
    send_turn_off_command(args.ip_last, args.port)
    # The script will only exit this function call upon success.
    print(f"TURN_OFF command successfully sent to Port {args.port} on {IP_BASE}{args.ip_last}.")
