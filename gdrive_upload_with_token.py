#!/usr/bin/env python3
"""
gdrive_upload_with_token.py — upload all SPb pipeline files to Google Drive.
Usage: python3 gdrive_upload_with_token.py <auth_code>
"""
import sys, os, json, time
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CLIENT_ID = "120800155319-22hmllcqfrvue7p4mtrusm8r0q01764u.apps.googleusercontent.com"
CLIENT_SECRET = "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"
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"

HOME = Path('/home/z/my-project')
UPLOAD_DIR = HOME / 'upload'
PERSIST = UPLOAD_DIR / 'scripts_persistent'
TOKEN_FILE = UPLOAD_DIR / 'oauth_token.json'

# Files to upload (small first, big last)
SMALL_FILES = [
    '13_nevskaya_guba.py', 'spb_full_run_v2.py', 'lib_ground.py', 'lib_geo.py',
    'lib_vertical.py', 'lib_profile.py', 'test_guba.py',
    'spb_check_geoshift.py', 'spb_check_geoshift_per_tile.py',
    'spb_water_mask_analysis.py', 'spb_create_bay_polygon.py',
    'spb_missing_17.py', 'spb_missing_17_no_tiff.py',
    'spb_qa_2m.json', 'nevskaya_guba_qa.json', 'nevskaya_guba.geojson',
    'spb_passport_validation_v4.csv', 'spb_passport_validation_v3.csv',
    'spb_geoshift_check.csv', 'spb_geoshift_stats.json',
    'spb_geoshift_per_tile.json', 'spb_missing_17_analysis.csv',
    'spb_water_mask_analysis.json', 'spb_progress.json',
    'README.md', 'ALL_DIRECT_URLS.txt',
    'spb_pipeline_scripts.zip', 'osm_water.zip', 'osm_green.zip',
    'osm_buildings.zip', 'nevskaya_guba.zip',
]
LARGE_FILES = [
    '/home/z/my-project/upload/data_backup/copernicus/cop_spb_3413_30m.tif',
    '/home/z/my-project/upload/data_backup/copernicus/cop_N59_00_E029_00.tif',
    '/home/z/my-project/upload/data_backup/copernicus/cop_N59_00_E030_00.tif',
    '/home/z/my-project/upload/data_backup/copernicus/cop_N60_00_E029_00.tif',
    '/home/z/my-project/upload/data_backup/copernicus/cop_N60_00_E030_00.tif',
    # Big TIF — split into 32 parts (90MB each, ~100MB Drive limit safe)
]


def exchange_code(code):
    """Exchange auth code for tokens."""
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'code': code,
        'grant_type': 'authorization_code',
        'redirect_uri': REDIRECT_URI,
    })
    if r.status_code != 200:
        print(f'✗ Token exchange failed: {r.status_code} {r.text}')
        return None
    tokens = r.json()
    TOKEN_FILE.write_text(json.dumps(tokens))
    print(f'✓ Token saved to {TOKEN_FILE}')
    return tokens


def load_tokens():
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text())


def refresh_token(tokens):
    if 'refresh_token' not in tokens:
        return tokens
    r = requests.post('https://oauth2.googleapis.com/token', data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'refresh_token': tokens['refresh_token'],
        'grant_type': 'refresh_token',
    })
    if r.status_code == 200:
        new_tokens = r.json()
        new_tokens['refresh_token'] = tokens['refresh_token']
        TOKEN_FILE.write_text(json.dumps(new_tokens))
        return new_tokens
    return tokens


def auth(tokens=None):
    if tokens is None:
        tokens = load_tokens()
    if not tokens:
        return None
    tokens = refresh_token(tokens)
    creds = Credentials(
        token=tokens.get('access_token'),
        refresh_token=tokens.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=['https://www.googleapis.com/auth/drive.file'],
    )
    return build('drive', 'v3', credentials=creds)


def find_or_create_folder(service, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, spaces='drive', fields='files(id,name)').execute()
    if res.get('files'):
        return res['files'][0]['id']
    body = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id:
        body['parents'] = [parent_id]
    return service.files().create(body=body, fields='id').execute()['id']


def upload_file(service, path, name, parent_id):
    q = f"name='{name}' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, spaces='drive', fields='files(id,name)').execute()
    
    media = MediaFileUpload(str(path), resumable=True, chunksize=10*1024*1024)
    body = {'name': name}
    if parent_id:
        body['parents'] = [parent_id]
    
    if res.get('files'):
        file_id = res['files'][0]['id']
        request = service.files().update(fileId=file_id, media_body=media, body=body)
    else:
        request = service.files().create(body=body, media_body=media)
    
    response = None
    last_progress = 0
    size_mb = path.stat().st_size / (1024*1024)
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            if progress - last_progress >= 25:
                print(f'      {progress}% ({size_mb:.1f}MB)', flush=True)
                last_progress = progress
    return response


def main():
    if len(sys.argv) < 2 and not TOKEN_FILE.exists():
        print('Usage: python3 gdrive_upload_with_token.py <auth_code>')
        print('Get auth code from:')
        print('https://accounts.google.com/o/oauth2/auth?client_id=120800155319-22hmllcqfrvue7p4mtrusm8r0q01764u.apps.googleusercontent.com&redirect_uri=urn%3Aietf%3Awg%3Aoauth%3A2.0%3Aoob&response_type=code&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.file&access_type=offline&prompt=consent')
        sys.exit(1)
    
    # Exchange code if provided
    if len(sys.argv) >= 2:
        code = sys.argv[1]
        print(f'Exchanging auth code for tokens...')
        tokens = exchange_code(code)
        if not tokens:
            sys.exit(1)
    else:
        tokens = load_tokens()
    
    print(f'Authenticating...')
    service = auth(tokens)
    if not service:
        print('✗ Auth failed')
        sys.exit(1)
    print(f'  ✓ Authenticated')
    
    # Create folder structure
    root_id = find_or_create_folder(service, 'spb-arcticdem-pipeline')
    scripts_id = find_or_create_folder(service, 'scripts', root_id)
    results_id = find_or_create_folder(service, 'results', root_id)
    data_id = find_or_create_folder(service, 'data', root_id)
    
    print(f'\n✓ Folders ready: root={root_id}')
    
    # Upload small files
    print(f'\n=== Uploading {len(SMALL_FILES)} small files ===')
    n_ok = 0
    for fname in SMALL_FILES:
        # Check both PERSIST and other locations
        path = PERSIST / fname
        if not path.exists():
            # Try data_backup
            path = HOME / 'upload' / 'data_backup' / 'copernicus' / fname
        if not path.exists():
            path = HOME / 'upload' / 'data_backup' / 'osm' / fname
        if not path.exists():
            # Try download
            path = HOME / 'download' / fname
        if not path.exists():
            print(f'  ✗ {fname} — NOT FOUND')
            continue
        
        size_kb = path.stat().st_size / 1024
        target = scripts_id if fname.endswith(('.py', '.geojson', '.sh', '.txt')) else (
            results_id if fname.endswith(('.json', '.csv', '.md')) else data_id)
        print(f'  → {fname} ({size_kb:.0f}KB)...', end=' ', flush=True)
        try:
            upload_file(service, path, fname, target)
            print('✓')
            n_ok += 1
        except Exception as e:
            print(f'✗ {e}')
    print(f'  {n_ok}/{len(SMALL_FILES)} uploaded')
    
    # Upload Copernicus + large files
    print(f'\n=== Uploading {len(LARGE_FILES)} large files ===')
    for path_str in LARGE_FILES:
        path = Path(path_str)
        if not path.exists():
            print(f'  ✗ {path.name} — NOT FOUND')
            continue
        size_mb = path.stat().st_size / (1024*1024)
        print(f'  → {path.name} ({size_mb:.0f}MB)...')
        try:
            upload_file(service, path, path.name, data_id)
            print(f'    ✓ done')
        except Exception as e:
            print(f'    ✗ {e}')
    
    # Upload TIF parts (32 × 90MB)
    tif_parts = sorted((HOME / 'upload').glob('spb_tif_part_*'))
    if tif_parts:
        print(f'\n=== Uploading {len(tif_parts)} TIF parts ===')
        tif_folder = find_or_create_folder(service, 'spb_ground_surface_2m_tif_parts', data_id)
        for i, path in enumerate(tif_parts):
            size_mb = path.stat().st_size / (1024*1024)
            print(f'  [{i+1}/{len(tif_parts)}] {path.name} ({size_mb:.0f}MB)...', end=' ', flush=True)
            try:
                upload_file(service, path, path.name, tif_folder)
                print('✓')
            except Exception as e:
                print(f'✗ {e}')
        
        # Upload re-assembly script
        reassemble = """#!/bin/bash
# Re-assemble spb_ground_surface_2m.tif from 32 parts
cat spb_tif_part_*.bin > spb_ground_surface_2m.tif
echo "✓ Final size: $(ls -lh spb_ground_surface_2m.tif | awk '{print $5}')"
"""
        ra_path = PERSIST / 'reassemble_tif.sh'
        ra_path.write_text(reassemble)
        upload_file(service, ra_path, 'reassemble_tif.sh', tif_folder)
        print(f'  ✓ reassemble_tif.sh uploaded')
    
    # List root folder
    print(f'\n=== Drive contents ===')
    res = service.files().list(
        q=f"'{root_id}' in parents and trashed=false",
        fields='files(id,name,mimeType,size)', pageSize=20
    ).execute()
    for f in res.get('files', []):
        size = int(f.get('size', 0))
        size_str = f'{size/1024/1024:.1f}MB' if size > 1024*1024 else f'{size/1024:.0f}KB' if size > 1024 else f'{size}B'
        print(f'  {f["name"]:40} {size_str}')


if __name__ == '__main__':
    main()
