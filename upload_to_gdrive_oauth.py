#!/usr/bin/env python3
"""
upload_to_gdrive_oauth.py — загрузить на Google Drive через OAuth (user flow).
Service account не имеет квоты, нужен user token.
"""
import os, json, sys
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

HOME = Path('/home/z/my-project')
UPLOAD_DIR = HOME / 'upload'
CLIENT_SECRET = UPLOAD_DIR / 'client_secret_120800155319-0m99vuu7pchgqej2lvtj43rvtlv3hm1t.apps.googleusercontent.com.json'
TOKEN_FILE = UPLOAD_DIR / 'oauth_token.json'
PERSIST = UPLOAD_DIR / 'scripts_persistent'

SCOPES = ['https://www.googleapis.com/auth/drive']

FILES_TO_UPLOAD = [
    'spb_full_run_v2.py', 'lib_ground.py', 'lib_vertical.py', 'lib_profile.py', 'lib_geo.py',
    'spb_check_geoshift.py', 'spb_check_geoshift_per_tile.py',
    'spb_water_mask_analysis.py', 'spb_create_bay_polygon.py',
    'spb_missing_17.py', 'spb_missing_17_no_tiff.py',
    '13_nevskaya_guba.py', 'test_guba.py',
    'spb_qa_2m.json', 'spb_passport_validation_v3.csv', 'spb_passport_validation_v4.csv',
    'spb_geoshift_check.csv', 'spb_geoshift_stats.json', 'spb_geoshift_per_tile.json',
    'spb_missing_17_analysis.csv', 'spb_water_mask_analysis.json',
    'nevskaya_guba.geojson', 'nevskaya_guba_qa.json',
]

LARGE_FILES = [
    ('/home/z/my-project/download/spb_ground_surface_2m.tif', 'spb_ground_surface_2m.tif'),
]


def auth():
    """OAuth user flow."""
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
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
            if progress - last_progress >= 20:
                print(f'    {progress}% ({size_mb:.1f}MB)', flush=True)
                last_progress = progress
    return response


def main():
    print('OAuth authentication...')
    try:
        service = auth()
        print('  ✓ Authenticated')
    except Exception as e:
        print(f'  ✗ OAuth failed: {e}')
        print('  Run interactively to authorize via browser')
        return False
    
    root_id = find_or_create_folder(service, 'spb-arcticdem-pipeline')
    scripts_id = find_or_create_folder(service, 'scripts', root_id)
    results_id = find_or_create_folder(service, 'results', root_id)
    large_id = find_or_create_folder(service, 'large_files', root_id)
    
    print(f'\n=== Uploading {len(FILES_TO_UPLOAD)} files ===')
    n_ok = 0
    for fname in FILES_TO_UPLOAD:
        path = PERSIST / fname
        if not path.exists():
            print(f'  ✗ {fname} — NOT FOUND')
            continue
        size_kb = path.stat().st_size / 1024
        target = scripts_id if fname.endswith(('.py', '.geojson')) else results_id
        print(f'  → {fname} ({size_kb:.0f}KB)...', end=' ', flush=True)
        try:
            upload_file(service, path, fname, target)
            print('✓')
            n_ok += 1
        except Exception as e:
            print(f'✗ {e}')
    
    print(f'\n  {n_ok}/{len(FILES_TO_UPLOAD)} uploaded')
    
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
    
    return n_ok > 0


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
