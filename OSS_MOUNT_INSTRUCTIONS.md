# Инструкция для AI-агента: OSS-mount Alibaba Cloud в sandbox

## TL;DR

В sandbox есть **3 OSS-mount точки** от Alibaba Cloud Object Storage, 
смонтированные через FUSE (ossfs). Используйте `/home/z/my-project/upload/` 
как основное персистентное хранилище — 16 EB, переживает сбросы сессии.

---

## Доступные точки монтирования

| Точка | Тип | Права | Объём | Назначение |
|---|---|---|---|---|
| `/home/z/my-project/upload` | ossfs | **rw** | 16 EB | Основное хранилище (рекомендуется) |
| `/home/sync` | ossfs | **rw** | 16 EB | Синхронизация (общий bucket) |
| `/home/official_skills` | ossfs | **ro** | 16 EB | Только чтение (skills) |

**Все три точки — один и тот же физический bucket** (проверено: `/home/sync/upload/` 
содержит те же файлы, что и `/home/z/my-project/upload/`).

---

## Характеристики

### Производительность

| Операция | Скорость | Примечание |
|---|---|---|
| Write (последовательный) | ~106 MB/s | 100MB за 1 сек |
| Read (последовательный) | ~296 MB/s | 100MB за 0.35 сек |
| Создание 100 мелких файлов | ~4.4 сек | ~44 файла/сек |
| Concurrent writes (5 × 20MB) | параллельно | работает без блокировок |

### Ограничения FUSE

| Операция | Поддержка | Примечание |
|---|---|---|
| Read/Write | ✅ | |
| mkdir / rmdir | ✅ | |
| Rename (mv) | ✅ | |
| Append (>>) | ✅ | |
| Concurrent writes | ✅ | |
| Symlinks | ✅ | В пределах одного bucket |
| Hardlinks | ❌ | `Operation not supported` |
| O_DIRECT | ❌ | Только buffered I/O |
| mmap (большие файлы) | ⚠️ | Медленно |
| fsync | ✅ | Но медленнее, чем локальный диск |
| inotify | ❌ | Не работает, используйте polling |
| fallocate | ⚠️ | Может не работать |
| flock | ⚠️ | Не надёжно для cross-process |

### Персистентность

✅ **Файлы переживают сброс сессии.** Проверено: файлы, записанные в 
одной сессии, доступны в следующей после перезапуска sandbox.

---

## Базовые операции

### Записать файл

```bash
# Прямая запись
cp file.txt /home/z/my-project/upload/

# Через Python
import shutil
shutil.copy('file.txt', '/home/z/my-project/upload/')

# Большой файл (>1 GB) — лучше стримить
with open('big.tif', 'rb') as src, open('/home/z/my-project/upload/big.tif', 'wb') as dst:
    while chunk := src.read(10 * 1024 * 1024):  # 10MB chunks
        dst.write(chunk)
```

### Прочитать файл

```bash
cat /home/z/my-project/upload/file.txt

# Большой файл — читайте по чанкам
with open('/home/z/my-project/upload/big.tif', 'rb') as f:
    while chunk := f.read(10 * 1024 * 1024):
        process(chunk)
```

### Создать директорию

```bash
mkdir -p /home/z/my-project/upload/data_backup/subdir
```

### Удалить файл / директорию

```bash
rm /home/z/my-project/upload/old_file.txt
rm -rf /home/z/my-project/upload/old_dir/
```

---

## Рекомендуемая структура

```
/home/z/my-project/upload/
├── scripts_persistent/   # Скрипты и маленькие результаты (<1MB каждый)
├── data_backup/          # Большие файлы (DEM, OSM, TIF)
│   ├── copernicus/       # Copernicus DEM тайлы
│   ├── osm/              # OSM GeoJSON
│   └── blocks/           # Per-block TIFF parts
├── spb_ground_surface_2m.tif  # Финальный растр (2.8 GB)
└── oauth_token.json      # OAuth токены (если есть)
```

---

## Стратегия для больших файлов (≥1 GB)

### Скачивание

```bash
# Скачивайте прямо в OSS
cd /home/z/my-project/upload/data_backup/
wget https://example.com/large_file.tif
```

### Обработка

Для обработки временно копируйте на rootfs (быстрее I/O — 1.6 GB/s vs 106 MB/s):

```bash
# Копируем на быстрый диск
cp /home/z/my-project/upload/big.tif /tmp/working.tif

# Обрабатываем
python3 process.py /tmp/working.tif /tmp/result.tif

# Сохраняем результат обратно в OSS
cp /tmp/result.tif /home/z/my-project/upload/

# Чистим
rm /tmp/working.tif /tmp/result.tif
```

### Разделение для передачи

Если нужно передать файл через ограниченный канал (Google Drive API, email):

```bash
# Разделить на части по 90 MB
split -b 90M /home/z/my-project/upload/big.tif \
    /home/z/my-project/upload/big.part_

# Передача по частям...

# На стороне получателя
cat big.part_* > big.tif
```

---

## Что НЕЛЬЗЯ делать

1. **Не используйте `/home/official_skills/`** — это read-only.

2. **Не создавайте hardlinks** — FUSE не поддерживает.

3. **Не используйте `O_DIRECT`** — только buffered I/O.

4. **Не полагайтесь на `inotify`** — FUSE его не поддерживает. 
   Используйте polling:
   ```python
   import time, os
   while not os.path.exists('/home/z/my-project/upload/ready.flag'):
       time.sleep(1)
   ```

5. **Не используйте `mmap` для больших файлов** — медленно через FUSE.

6. **Не запускайте `fsck`** — это сетевое хранилище, проверка бессмысленна.

7. **Не используйте OSS для swap** — FUSE не поддерживает.

8. **Не открывайте много мелких файлов одновременно** — 
   100 файлов за 4.4 сек = ~23 файла/сек. Если нужно открыть 10 000 файлов, 
   это займёт ~7 минут. Для большого количества мелких файлов используйте 
   tar/zip архив.

---

## Оптимизация производительности

### 1. Большие chunks

```python
# Плохо: 1 KB writes (медленно — каждый write = сетевой round-trip)
with open(path, 'wb') as f:
    for line in lines:
        f.write(line.encode())  # ~23 writes/sec

# Хорошо: 10 MB chunks (быстро)
with open(path, 'wb') as f:
    buffer = b''
    for line in lines:
        buffer += line.encode()
        if len(buffer) >= 10 * 1024 * 1024:
            f.write(buffer)
            buffer = b''
    if buffer:
        f.write(buffer)
```

### 2. Batch операции

```python
# Плохо: 100 отдельных файлов
for i in range(100):
    with open(f'/home/z/my-project/upload/file_{i}.txt', 'w') as f:
        f.write(data[i])

# Хорошо: 1 tar-архив
import tarfile
with tarfile.open('/home/z/my-project/upload/batch.tar.gz', 'w:gz') as tar:
    for i, data in enumerate(items):
        info = tarfile.TarInfo(f'file_{i}.txt')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
```

### 3. Streaming для больших файлов

```python
# Не загружайте весь файл в RAM
import requests
r = requests.get(url, stream=True)
with open('/home/z/my-project/upload/big.bin', 'wb') as f:
    for chunk in r.iter_content(chunk_size=10 * 1024 * 1024):
        f.write(chunk)
```

### 4. Concurrent writes

OSS поддерживает параллельные записи в разные файлы:

```python
from concurrent.futures import ThreadPoolExecutor

def download_and_save(url, path):
    r = requests.get(url)
    with open(path, 'wb') as f:
        f.write(r.content)

urls = [(f'https://example.com/file_{i}.tif', 
         f'/home/z/my-project/upload/file_{i}.tif') for i in range(10)]

with ThreadPoolExecutor(max_workers=5) as executor:
    for url, path in urls:
        executor.submit(download_and_save, url, path)
```

---

## Сравнение хранилищ в sandbox

| Хранилище | Точка | Объём | Скорость | Персистентность | Использовать для |
|---|---|---|---|---|---|
| **rootfs** | `/`, `/tmp/` | 9.9 GB | 1.6 GB/s | ❌ теряется | Промежуточные вычисления |
| **PolarFS** | `/tmp/my-project/` | 29 PB | 76 MB/s | ✅ | Основное рабочее пространство |
| **OSS (ossfs)** | `/home/z/my-project/upload/` | 16 EB | 106 MB/s | ✅ | Файлы для передачи, backup |
| **OSS sync** | `/home/sync/` | 16 EB | 106 MB/s | ✅ | Тот же bucket, другой mountpoint |
| **OSS ro** | `/home/official_skills/` | 16 EB | — | ro | Skills (только чтение) |

**Рекомендация:**
- **PolarFS** (`/tmp/my-project/`) — для рабочих данных и скриптов (быстрее для I/O)
- **OSS** (`/home/z/my-project/upload/`) — для backup, передачи, больших файлов

---

## Проверка при старте новой сессии

```bash
#!/bin/bash
# oss_check.sh — проверка OSS-mount

echo "=== OSS mounts ==="
mount | grep ossfs

echo ""
echo "=== Disk space ==="
df -h /home/z/my-project/upload/

echo ""
echo "=== Write test ==="
echo "test $(date)" > /home/z/my-project/upload/oss_test.txt
if [ "$(cat /home/z/my-project/upload/oss_test.txt)" == "test $(date)" ]; then
    echo "✓ OSS write/read works"
else
    echo "✗ OSS write/read failed"
fi
rm /home/z/my-project/upload/oss_test.txt

echo ""
echo "=== Existing files ==="
ls -lh /home/z/my-project/upload/ | head -10
echo "..."
echo "Total: $(ls /home/z/my-project/upload/ | wc -l) files"
```

---

## Если OSS не смонтирован

Симптомы:
```bash
mount | grep ossfs
# Пусто — OSS не подключён

ls /home/z/my-project/upload/
# Error: No such file or directory
```

**OSS монтируется платформой Alibaba FC** при старте контейнера. 
Вы **не можете** смонтировать его сами:
- Нет `ossfs` binary
- Нет credentials (AccessKey/SecretKey)
- Нет metadata service (100.100.100.200)
- Нет `oss2` Python SDK

Что делать:
1. Проверьте `/home/sync/` — это тот же bucket, может быть смонтирован
2. Проверьте PolarFS (`/tmp/my-project/`) — альтернативное персистентное хранилище
3. Сообщите пользователю — нужен перезапуск sandbox

---

## Интеграция с Google Drive

Если есть OAuth-токен в `/home/z/my-project/upload/oauth_token.json`:

```python
import json
from pathlib import Path
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import requests

# Загрузка и обновление токена
tokens = json.loads(Path('/home/z/my-project/upload/oauth_token.json').read_text())
r = requests.post('https://oauth2.googleapis.com/token', data={
    'client_id': '<CLIENT_ID>',
    'client_secret': '<CLIENT_SECRET>',
    'refresh_token': tokens['refresh_token'],
    'grant_type': 'refresh_token',
})
new_tokens = r.json()
new_tokens['refresh_token'] = tokens['refresh_token']
Path('/home/z/my-project/upload/oauth_token.json').write_text(json.dumps(new_tokens))

# Загрузка файла на Drive
creds = Credentials(
    token=new_tokens['access_token'],
    refresh_token=new_tokens['refresh_token'],
    token_uri='https://oauth2.googleapis.com/token',
    client_id='<CLIENT_ID>',
    client_secret='<CLIENT_SECRET>',
    scopes=['https://www.googleapis.com/auth/drive.file'],
)
service = build('drive', 'v3', credentials=creds)
media = MediaFileUpload('/home/z/my-project/upload/file.txt', resumable=True)
file = service.files().create(body={'name': 'file.txt'}, media_body=media).execute()
```

---

## Итог

**Используйте `/home/z/my-project/upload/` для:**
- Backup скриптов и результатов
- Больших файлов (TIF, ZIP, кэш)
- OAuth токенов и конфигов
- Файлов для передачи на Google Drive / другие сервисы

**Не используйте для:**
- Промежуточных вычислений (медленнее rootfs в 15×)
- Inotify-based file watching
- Hardlinks
- O_DIRECT I/O
