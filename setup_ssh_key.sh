#!/bin/sh
set -e

SSH_DIR="/root/.ssh"
KEY_SRC="/keys/pve_id_rsa"
KEY_DST="$SSH_DIR/id_rsa"

# Ensure SSH directory exists
mkdir -p "$SSH_DIR"

# Create symlink to the Proxmox key if it's not already there
if [ ! -e "$KEY_DST" ]; then
    ln -s "$KEY_SRC" "$KEY_DST"
fi

# Adjust permissions if the key is present; ignore errors otherwise
if [ -f "$KEY_SRC" ]; then
    chmod 600 "$KEY_SRC"
fi
