#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Activate Python environment if using one (uncomment and modify if needed)
# source /path/to/your/venv/bin/activate

# Change to the script directory
cd "$SCRIPT_DIR"

# Run the Python script
python3 youtube_processor.py >> "$SCRIPT_DIR/youtube_processor.log" 2>&1 