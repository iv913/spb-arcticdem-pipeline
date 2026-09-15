#!/usr/bin/env python3
"""
upload_to_gdrive.py — загрузить все критичные файлы на Google Drive.

Использует service account z-ai-agent-316531f41551.json.
"""
import os, json, sys, time
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HOME = Path('/home/z/my-project')
UPLOAD_DIR = HOME / 'upload'
SA_KEY = UPLOAD_DIR / 'z-ai-agent-316531f41551.json'
PERSIST = UPLOAD_DIR / 'scripts_persistent'

# Files to upload (relative to PERSIST)
FILES_TO_UPLOAD = [
    'spb_full_run_v2.py',
    'lib_ground.py',
    'lib_vertical.py',
    'lib_profile.py',
    'lib_geo.py',
    'spb_check_geoshift.py',
    'spb_check_geoshift_per_tile.py',
    'spb_water_mask_analysis.py',
    'spb_create_bay_polygon.py',
    'spb_missing_17.py',
    'spb_missing_17_no_tiff.py',
    '13_nevskaya_guba.py',
    'test_guba.py',
    'spb_qa_2m.json',
    'spb_passport_validation_v3.csv',
    'spb_passport_validation_v4.csv',
    'spb_geoshift_check.csv',
    'spb_geoshift_stats.json',
    'spb_geoshift_per_tile.json',
    'spb_missing_17_analysis.csv',
    'spb_water_mask_analysis.json',
    'nevskaya_guba.geojson',
    'nevskaya_guba_qa.json',
]

# Also include large files from download/
LARGE_FILES = [
    ('/home/z/my-project/download/spb_ground_surface_2m.tif', 'spb_ground_surface_2m.tif'),
]


def auth():
    """Authenticate with service account."""
    creds = service_account.Credentials.from_service_account_file(
        str(SA_KEY),
        scopes=['https://www.googleapis.com/auth/drive']
    )
    service = build('drive', 'v3', credentials=creds)
    return service


def find_or_create_folder(service, name, parent_id=None):
    """Find folder by name, or create it."""
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, spaces='drive', fields='files(id,name)').execute()
    if res.get('files'):
        return res['files'][0]['id']
    # Create
    body = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id:
        body['parents'] = [parent_id]
    folder = service.files().create(body=body, fields='id').execute()
    return folder['id']


def upload_file(service, path, name, parent_id):
    """Upload file (with retry on large files)."""
    # Check if exists
    q = f"name='{name}' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, spaces='drive', fields='files(id,name)').execute()
    
    media = MediaFileUpload(str(path), resumable=True, chunksize=10*1024*1024)
    body = {'name': name}
    if parent_id:
        body['parents'] = [parent_id]
    
    if res.get('files'):
        # Update existing
        file_id = res['files'][0]['id']
        request = service.files().update(fileId=file_id, media_body=media, body=body)
    else:
        # Create new
        request = service.files().create(body=body, media_body=media)
    
    # Upload with progress
    size_mb = path.stat().st_size / (1024*1024)
    response = None
    last_progress = 0
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            if progress - last_progress >= 20:
                print(f'    {progress}% ({size_mb:.1f}MB)', flush=True)
                last_progress = progress
    return response


def main():
    print('Authenticating with service account...')
    service = auth()
    print(f'  ✓ Service account: {SA_KEY.name}')
    
    # Find or create root folder 'spb-arcticdem-pipeline'
    root_id = find_or_create_folder(service, 'spb-arcticdem-pipeline')
    print(f'  ✓ Root folder: spb-arcticdem-pipeline ({root_id})')
    
    # Subfolders
    scripts_id = find_or_create_folder(service, 'scripts', root_id)
    results_id = find_or_create_folder(service, 'results', root_id)
    large_id = find_or_create_folder(service, 'large_files', root_id)
    
    # Upload scripts + small results
    print(f'\n=== Uploading {len(FILES_TO_UPLOAD)} files ===')
    n_ok = 0
    for fname in FILES_TO_UPLOAD:
        path = PERSIST / fname
        if not path.exists():
            print(f'  ✗ {fname} — NOT FOUND')
            continue
        size_kb = path.stat().st_size / 1024
        target_folder = scripts_id if fname.endswith(('.py', '.geojson')) else results_id
        print(f'  → {fname} ({size_kb:.0f}KB)...', end=' ', flush=True)
        try:
            upload_file(service, path, fname, target_folder)
            print('✓')
            n_ok += 1
        except Exception as e:
            print(f'✗ {e}')
    
    print(f'\n=== Uploaded {n_ok}/{len(FILES_TO_UPLOAD)} small files ===')
    
    # Upload large files
    print(f'\n=== Uploading {len(LARGE_FILES)} large files ===')
    for path_str, name in LARGE_FILES:
        path = Path(path_str)
        if not path.exists():
            print(f'  ✗ {name} — NOT FOUND')
            continue
        size_mb = path.stat().st_size / (1024*1024)
        print(f'  → {name} ({size_mb:.0f}MB)...')
        try:
            upload_file(service, path, name, large_id)
            print(f'    ✓ done')
        except Exception as e:
            print(f'    ✗ {e}')
    
    # List root folder
    print(f'\n=== Drive contents ===')
    res = service.files().list(
        q=f"'{root_id}' in parents and trashed=false",
        fields='files(id,name,mimeType,size)', pageSize=20
    ).execute()
    for f in res.get('files', []):
        size = int(f.get('size', 0))
        size_str = f'{size/1024/1024:.1f}MB' if size > 1024*1024 else f'{size/1024:.0f}KB' if size > 1024 else f'{size}B'
        print(f'  {f["name"]:40} {f.get("mimeType",""):30} {size_str}')


if __name__ == '__main__':
    main()
