#!/bin/bash

# File to monitor (make sure it matches the one used in the Python script)
FILE="H2tgtPresentStatus.txt"

# --- Script starts here ---

# 1. Check if the file exists
if [ ! -f "$FILE" ]; then
    echo "Error: File $FILE not found."
    exit 1
fi

# 2. Get the current value of 'Alert_H2leak:'
#    Use grep to find the target line, awk to extract the value after ':',
#    and (+0) to convert it to a number (this trims whitespace automatically)
current_status=$(grep "^Alert_H2leak:" "$FILE" | awk -F: '{print $2+0}')

# 3. Determine the new value
if [ "$current_status" = "0" ]; then
    new_status="1"
    echo "Current status (Alert_H2leak): 0"
    echo "Changing to -> 1"
else
    new_status="0"
    echo "Current status (Alert_H2leak): 1 (or non-zero)"
    echo "Changing to -> 0"
fi

# 4. Use sed to replace the value in place
#    -i = edit the file directly (in place)
#    s/pattern/replacement/
#    Pattern:
#      ^\(Alert_H2leak:[[:space:]]*\)  = line starts with "Alert_H2leak:" followed by spaces,
#                                        captured as group 1 (\1)
#      $current_status                 = the old value (0 or 1)
#    Replacement:
#      \1$new_status                   = group 1 + the new value
sed -i "s/^\(Alert_H2leak:[[:space:]]*\)$current_status/\1$new_status/" "$FILE"

echo "---"
echo "Updated line:"
grep "^Alert_H2leak:" "$FILE"
