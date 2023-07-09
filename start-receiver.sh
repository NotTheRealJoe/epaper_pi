#!/bin/bash
SCRIPT_DIR="$(realpath "$(dirname "${BASH_SOURCE[0]}")")"

if ! cd "$SCRIPT_DIR"; then
	echo "Unable to cd to entrypoint script's directory -- this should not be possible. Check permissions"
	exit 1
fi

source ./.venv/bin/activate

while true; do
	python3 receiver.py
	# sleep for 5s then start the program again in case it crashes
	sleep 5
done
