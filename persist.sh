#!/bin/bash
# ============================================================
# persist.sh — one-command backup to Alibaba Cloud OSS
# ============================================================
# Usage:
#   1. Fill in scripts/oss_config.env with your credentials
#   2. Run: bash scripts/persist.sh
#
# What it does:
#   - Mounts OSS bucket via ossfs (or falls back to oss2 Python SDK)
#   - Backs up all files from /tmp/my-project/ to OSS
#   - Verifies backup by listing files on OSS
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/oss_config.env"
SOURCE_DIR="${1:-/tmp/my-project}"
MOUNT_POINT="${OSS_MOUNT_POINT:-/home/z/my-project/oss_backup}"

# Load config
if [ ! -f "$CONFIG_FILE" ]; then
    echo "✗ Config file not found: $CONFIG_FILE"
    echo "  Fill it in first with your Alibaba Cloud credentials."
    exit 1
fi
source "$CONFIG_FILE"

# Validate config
if [ -z "$OSS_ACCESS_KEY_ID" ] || [ -z "$OSS_ACCESS_KEY_SECRET" ] || [ -z "$OSS_BUCKET" ]; then
    echo "✗ Missing required config in $CONFIG_FILE"
    echo "  OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET must be set."
    exit 1
fi

echo "=== OSS Backup ==="
echo "  Bucket:   $OSS_BUCKET"
echo "  Endpoint: $OSS_ENDPOINT"
echo "  Source:   $SOURCE_DIR"
echo "  Mount:    $MOUNT_POINT"
echo ""

# ─── Method 1: ossfs (preferred, if installed) ──────────────
if which ossfs &>/dev/null; then
    echo "=== Method: ossfs mount ==="
    
    mkdir -p "$MOUNT_POINT"
    
    # Write credential file
    echo "$OSS_ACCESS_KEY_ID:$OSS_ACCESS_KEY_SECRET" > /tmp/.passwd-ossfs
    chmod 600 /tmp/.passwd-ossfs
    
    # Mount
    ossfs "$OSS_BUCKET" "$MOUNT_POINT" \
        -o url="https://$OSS_ENDPOINT" \
        -o passwd_file=/tmp/.passwd-ossfs \
        -o allow_other \
        -o umask=000 \
        -o max_write=10485760 \
        -o parallel_count=5
    
    if mountpoint -q "$MOUNT_POINT"; then
        echo "✓ OSS mounted at $MOUNT_POINT"
        
        # Backup
        echo "=== Backing up files... ==="
        rsync -av --progress "$SOURCE_DIR/" "$MOUNT_POINT/"
        
        echo "=== Backup complete ==="
        echo "Files on OSS:"
        find "$MOUNT_POINT" -type f | head -20
        echo "..."
        echo "Total: $(find "$MOUNT_POINT" -type f | wc -l) files"
        
        # Unmount
        fusermount -u "$MOUNT_POINT" 2>/dev/null || umount "$MOUNT_POINT"
        rm -f /tmp/.passwd-ossfs
        echo "✓ Unmounted. Done."
    else
        echo "✗ Mount failed"
        rm -f /tmp/.passwd-ossfs
        exit 1
    fi

# ─── Method 2: oss2 Python SDK (fallback) ───────────────────
else
    echo "=== Method: oss2 Python SDK (ossfs not installed) ==="
    
    pip install --quiet oss2 2>/dev/null || pip3 install --quiet oss2 2>/dev/null
    
    python3 << PYEOF
import oss2, os, sys, hashlib
from pathlib import Path

# Config
access_key_id = os.environ.get('OSS_ACCESS_KEY_ID', '$OSS_ACCESS_KEY_ID')
access_key_secret = os.environ.get('OSS_ACCESS_KEY_SECRET', '$OSS_ACCESS_KEY_SECRET')
bucket_name = '$OSS_BUCKET'
endpoint = 'https://$OSS_ENDPOINT'
source_dir = '$SOURCE_DIR'
prefix = '$OSS_PREFIX'

auth = oss2.Auth(access_key_id, access_key_secret)
bucket = oss2.Bucket(auth, endpoint, bucket_name)

# Test connection
try:
    bucket.get_bucket_info()
    print(f"✓ Connected to bucket: {bucket_name}")
except Exception as e:
    print(f"✗ Connection failed: {e}")
    sys.exit(1)

# Upload files
source = Path(source_dir)
files = list(source.rglob('*'))
files = [f for f in files if f.is_file()]
print(f"Found {len(files)} files to upload")

n_ok = 0
n_fail = 0
for i, fpath in enumerate(files):
    rel = str(fpath.relative_to(source))
    oss_key = f"{prefix}{rel}" if prefix else rel
    
    try:
        # Skip very large files (>5GB) — need multipart upload
        size = fpath.stat().st_size
        if size > 5 * 1024**3:
            print(f"  [{i+1}/{len(files)}] SKIP (too large): {rel} ({size/1e9:.1f}GB)")
            continue
        
        # Upload
        bucket.put_object_from_file(oss_key, str(fpath))
        n_ok += 1
        if (i+1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(files)}] ✓ {rel} ({size/1024:.0f}KB)")
    except Exception as e:
        print(f"  [{i+1}/{len(files)}] ✗ {rel}: {e}")
        n_fail += 1

print(f"\n=== Backup complete: {n_ok} uploaded, {n_fail} failed ===")

# List files on OSS
print("\nFiles on OSS:")
for obj in oss2.ObjectIterator(bucket, prefix=prefix, max_keys=20):
    print(f"  {obj.key} ({obj.size} bytes)")
total = sum(1 for _ in oss2.ObjectIterator(bucket, prefix=prefix))
print(f"  ... total: {total} objects")
PYEOF
fi

echo ""
echo "✓ persist.sh done."
