# Инструкция для второго AI агента: PolarFS как расширенное пространство

## TL;DR

В sandbox по умолчанию **9.9 GB на rootfs** — этого мало для больших данных
(geotiff на гигабайты, наборы данных и т.д.). Используйте **PolarFS** —
встроенную распределённую файловую систему с 29 PB свободного места.

---

## Что доступно в sandbox

| Точка монтирования | Тип | Объём | Скорость | Персистентность |
|---|---|---|---|---|
| `/` (rootfs) | overlay | 9.9 GB | ~1.6 GB/s | теряется при сбросе сессии |
| `/tmp` | tmpfs | ~2 GB | ~1.6 GB/s | теряется |
| `/tmp/my-project` | **PolarFS** | **29 PB свободно** | ~76 MB/s | **переживает сбросы** |
| `/home/user_skills` | PolarFS | 29 PB | ~76 MB/s | переживает |
| `/home/z/my-project/upload` | OSS (ossfs) | 16 EB | ~54 MB/s | переживает |
| `/home/sync` | OSS | 16 EB | ~54 MB/s | переживает |
| `/home/official_skills` | OSS (ro) | 16 EB | — | только чтение |

---

## Где PolarFS смонтирован

Проверьте:
```bash
mount | grep PolarFS
df -hT | grep pfs
```

Должно показать два mountpoint:
- `/tmp/my-project` — **рабочая директория проекта** (рекомендуется)
- `/home/user_skills` — для skills (обычно пустая)

Оба указывают на один и тот же PolarFS volume:
`PolarFS:pcs-ue6ju0nuiu0hz7tjc-0e3odv6t4dackr8s3`

---

## Структура /tmp/my-project (рекомендуемая)

```
/tmp/my-project/
├── download/          # большие скачиваемые файлы (TIF, ZIP)
├── scripts/           # Python скрипты
├── storage/           # постоянное хранилище данных между сессиями
├── upload/            # файлы, загруженные пользователем
├── tool-results/      # выводы инструментов
├── worklog.md         # ОБЩИЙ worklog между агентами
├── .env               # переменные окружения
├── .gdrive-folder-id  # ID папки Google Drive
├── .earthdata_token   # токен NASA Earthdata
└── .initial_snapshot.json  # снимок состояния при инициализации
```

---

## Базовые операции

### 1. Проверить доступное место

```bash
df -h /tmp/my-project
# Покажет ~29 PB available
```

### 2. Записать файл в PolarFS

```bash
# Прямая запись
cp large_file.tif /tmp/my-project/storage/

# Или через Python
python3 -c "
import shutil
shutil.copy('large_file.tif', '/tmp/my-project/storage/')
"
```

### 3. Проверить, что файл пережил сброс сессии

```bash
ls -lh /tmp/my-project/storage/large_file.tif
# Должен показать тот же размер и дату модификации
```

---

## Важные особенности PolarFS

### 1. Медленнее, чем rootfs

- **rootfs**: ~1.6 GB/s (overlay на NVMe)
- **PolarFS**: ~76 MB/s (fuse.pfs через сеть)
- **OSS**: ~54 MB/s (fuse.ossfs через сеть)

Стратегия:
- Промежуточные вычисления — на rootfs (`/tmp/` или `/home/z/my-project/`)
- Финальные результаты — копируйте в PolarFS (`/tmp/my-project/storage/`)

### 2. FUSE — нет O_DIRECT

- `O_DIRECT` не работает — используйте buffered I/O
- `mmap()` может быть медленным для больших файлов
- `fsync()` работает, но медленнее, чем на локальном диске

### 3. Inotify не работает

FUSE не поддерживает inotify по умолчанию. Если ваш код ждёт событий
файловой системы (`watchdog`, `inotifywait`), это не сработает на PolarFS.
Используйте polling.

### 4. Большие chunk'и работают лучше

```python
# Плохо: много мелких writes (медленно)
with open('/tmp/my-project/storage/big.bin', 'wb') as f:
    for chunk in small_chunks:
        f.write(chunk)  # каждый write — сетевой round-trip

# Хорошо: один большой write
with open('/tmp/my-project/storage/big.bin', 'wb') as f:
    f.write(big_buffer)  # один round-trip
```

### 5. Символические ссылки

FUSE поддерживает символические ссылки, но они работают только внутри
одного mountpoint. Не делайте symlink с PolarFS на rootfs — он сломается
при сбросе сессии.

---

## Стратегия для больших файлов (≥1 GB)

### Скачивание

```bash
# Скачивайте прямо в PolarFS
cd /tmp/my-project/download/
wget https://example.com/large_file.tif
```

### Обработка

Для обработки временно копируйте на rootfs (быстрее I/O):
```bash
cp /tmp/my-project/storage/large_file.tif /tmp/working.tif
python3 process.py /tmp/working.tif /tmp/result.tif
cp /tmp/result.tif /tmp/my-project/storage/
rm /tmp/working.tif /tmp/result.tif
```

### Разделение для передачи

Если нужно передать файл через ограниченный канал (Google Drive API,
Mail.ru Cloud), разделите его:
```bash
split -b 100M /tmp/my-project/storage/large_file.tif /tmp/my-project/storage/large_file.part_
# Передача по частям
# На стороне получателя:
cat large_file.part_* > large_file.tif
```

---

## Проверка персистентности

PolarFS переживает сбросы сессии. Проверьте:

```bash
# Перед сбросом
echo "test_persistence_$(date +%s)" > /tmp/my-project/storage/persistence_test.txt

# После перезапуска sandbox
cat /tmp/my-project/storage/persistence_test.txt
# Должно показать то же содержимое
```

Если файл пропал — значит PolarFS не смонтирован в новой сессии. Проверьте:
```bash
mount | grep PolarFS
# Если пусто — PolarFS не подключён, нужно поднять тикет
```

---

## Что НЕЛЬЗЯ делать

1. **Не храните критичные данные только на rootfs** (`/home/z/my-project/`,
   `/tmp/`) — они теряются при сбросе сессии.

2. **Не используйте `/tmp/my-project/upload/`** для больших данных — это
   вложенная папка, может конфликтовать с OSS-mount. Используйте
   `/tmp/my-project/storage/` для данных.

3. **Не пишите в `/home/official_skills/`** — это read-only.

4. **Не запускайте `fsck` на PolarFS** — это сетевая ФС, она не нуждается в
   проверке и может зависнуть.

5. **Не используйте PolarFS для swap** — FUSE не поддерживает swap.

---

## Общий worklog между агентами

Файл `/tmp/my-project/worklog.md` — общий журнал работы всех агентов.
Добавляйте свои записи в формате:

```markdown
---
Task ID: <уникальный ID>
Agent: <имя/версия>
Task: <краткое описание задачи>

Work Log:
- <шаг 1>
- <шаг 2>

Stage Summary:
- <ключевые результаты>
- <артефакты (пути к файлам)>
```

Перед началом работы читайте worklog — там история прошлых сессий.

---

## Чтение .env и конфигурации

При старте сессии sandbox читает `/tmp/my-project/.env`:
```bash
cat /tmp/my-project/.env
```

Если нужно сохранить переменные между сессиями, добавьте их в этот файл:
```bash
echo 'MY_API_KEY=...' >> /tmp/my-project/.env
```

---

## Связь с Google Drive

Если предыдущий агент уже авторизовался на Google Drive, OAuth-токен
сохранён в:
- `/tmp/my-project/upload/oauth_token.json` (access + refresh)
- `/tmp/my-project/.gdrive-folder-id` (ID папки на Drive)

Используйте тот же токен — не нужно повторно авторизоваться. Скрипт:
`/tmp/my-project/scripts/gdrive_upload_with_token.py`

---

## Связь с NASA Earthdata

Если есть `.earthdata_token` — токен для скачивания ICESat-2, ArcticDEM
и других данных. Обновляется каждые 60 дней.

---

## Быстрая проверка при старте новой сессии

```bash
#!/bin/bash
# startup_check.sh — что должно быть на месте

echo "=== PolarFS ==="
df -h /tmp/my-project | tail -1

echo ""
echo "=== Worklog last 5 entries ==="
grep "^Task ID:" /tmp/my-project/worklog.md | tail -5

echo ""
echo "=== Storage contents ==="
ls -lh /tmp/my-project/storage/ 2>/dev/null | head -10

echo ""
echo "=== Scripts available ==="
ls /tmp/my-project/scripts/*.py 2>/dev/null | head -10

echo ""
echo "=== Env config ==="
cat /tmp/my-project/.env 2>/dev/null

echo ""
echo "=== Google Drive token? ==="
ls -la /tmp/my-project/upload/oauth_token.json 2>/dev/null && echo "✓ Found" || echo "✗ Not found"

echo ""
echo "=== Earthdata token? ==="
ls -la /tmp/my-project/.earthdata_token 2>/dev/null && echo "✓ Found" || echo "✗ Not found"
```

---

## Если PolarFS не смонтирован

В новой сессии PolarFS может не подключиться автоматически. Симптомы:
```bash
df -h /tmp/my-project
# Показывает rootfs (9.9 GB) вместо PolarFS (64 PB)

mount | grep PolarFS
# Пусто
```

Что делать:
1. Проверьте, что директория `/tmp/my-project` существует
2. Если нет — создайте: `mkdir -p /tmp/my-project`
3. Сообщите пользователю — нужно поднять тикет или перезапустить sandbox

---

## Сводка

| Что | Где | Объём | Персистентность |
|---|---|---|---|
| **PolarFS (главное)** | `/tmp/my-project/` | 29 PB | ✅ |
| OSS (резерв) | `/home/z/my-project/upload/` | 16 EB | ✅ |
| rootfs | `/` и `/tmp/` | 9.9 GB | ❌ |

**Используйте `/tmp/my-project/` как основное рабочее пространство.**
Все скрипты, данные, результаты — туда. Они переживут сброс сессии.
