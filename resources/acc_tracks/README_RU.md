# База трасс Assetto Corsa Competizione

Дата подготовки: 1 июля 2026 года  
Количество трасс: **25**

## Файлы

- `acc_tracks.sqlite` — основная SQLite-база для интеграции.
- `acc_tracks.json` — та же информация в JSON.
- `acc_tracks.csv` — краткий табличный список.
- `maps/*.svg` — простые виды сверху.
- `maps_preview.html` — визуальный каталог всех схем.
- `CODEX_PROMPT_FIX_ALL_TELEMETRY_RU.txt` — готовый промпт для Codex.

## Важное ограничение карт

SVG и `track_map_points` — **оригинальные схематичные приближения**, созданные для резервного отображения.
Они:

- не являются геодезическими данными;
- не повторяют точную гоночную траекторию;
- не синхронизированы с реальными мировыми координатами ACC;
- не должны заменять `world_position_x/world_position_z` из телеметрии.

Правильный приоритет для приложения:

1. Валидные координаты и длина из телеметрии игры.
2. Проверенные данные текущей сессии.
3. Эта база как fallback для подписи оси, ограничения графика и статичной схемы.

## Схема SQLite

### `tracks`

Основные поля:

- `id` — стабильный внутренний ключ.
- `acc_track_id` — канонический идентификатор ACC.
- `name`, `short_name`.
- `country_code`.
- `length_m`.
- `pack`.
- `layout_name`.
- `map_kind`, `map_accuracy`.
- `svg_relative_path`.
- поля источника и лицензии.

### `track_aliases`

Позволяет сопоставлять разные варианты названий, включая:
- пробелы/дефисы;
- короткие названия;
- Nürburgring/Nurburgring;
- Bathurst/Mount Panorama;
- исторические опечатки `murburgring` и `indianpolis`.

### `track_map_points`

Нормализованные координаты `0..1` для резервного рисунка:

```sql
SELECT point_index, x_normalized, y_normalized
FROM track_map_points
WHERE track_id = ?
ORDER BY point_index;
```

### `track_maps`

Полный SVG-текст, если удобнее загружать карту прямо из SQLite.

## Список трасс

| № | ACC ID | Трасса | Длина, м | Набор |
|---:|---|---|---:|---|
| 1 | `barcelona` | Barcelona | 4 675 | Base Game |
| 2 | `brands-hatch` | Brands Hatch | 3 916 | Base Game |
| 3 | `hungaroring` | Hungaroring | 4 381 | Base Game |
| 4 | `misano` | Misano | 4 200 | Base Game |
| 5 | `monza` | Monza | 5 793 | Base Game |
| 6 | `nurburgring` | Nürburgring GP | 5 137 | Base Game |
| 7 | `paul-ricard` | Paul Ricard | 5 770 | Base Game |
| 8 | `silverstone` | Silverstone | 5 890 | Base Game |
| 9 | `spa` | Spa-Francorchamps | 7 004 | Base Game |
| 10 | `zandvoort` | Zandvoort | 4 259 | Base Game |
| 11 | `zolder` | Zolder | 4 010 | Base Game |
| 12 | `snetterton` | Snetterton | 4 779 | British GT Pack |
| 13 | `oulton-park` | Oulton Park | 4 333 | British GT Pack |
| 14 | `donington` | Donington | 4 020 | British GT Pack |
| 15 | `kyalami` | Kyalami | 4 580 | Intercontinental GT Pack |
| 16 | `suzuka` | Suzuka | 5 807 | Intercontinental GT Pack |
| 17 | `laguna-seca` | Laguna Seca | 3 602 | Intercontinental GT Pack |
| 18 | `mount-panorama` | Mount Panorama | 6 213 | Intercontinental GT Pack |
| 19 | `imola` | Imola | 4 909 | 2020 GT World Challenge Pack |
| 20 | `watkins-glen` | Watkins Glen | 5 552 | American Track Pack |
| 21 | `cota` | COTA | 5 513 | American Track Pack |
| 22 | `indianapolis` | Indianapolis | 4 170 | American Track Pack |
| 23 | `valencia` | Valencia | 4 005 | 2023 GT World Challenge Pack |
| 24 | `red-bull-ring` | Red Bull Ring | 4 318 | GT2 Pack |
| 25 | `nurburgring-24h` | Nürburgring 24H | 25 378 | 24H Nürburgring Pack |

## Пример Python-интеграции

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def find_track(db_path: Path, raw_name: str) -> dict | None:
    normalized = raw_name.strip().casefold()

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            '''
            SELECT t.*
            FROM track_aliases a
            JOIN tracks t ON t.id = a.track_id
            WHERE lower(a.alias) = ?
            LIMIT 1
            ''',
            (normalized,),
        ).fetchone()

    return dict(row) if row else None
```

Для production-кода лучше сделать отдельный read-only repository и не выполнять SQL прямо из UI.

## Как ограничить графики

Для выбранной трассы:

```python
x_min = 0.0
x_max = float(track.length_m)
```

При этом:

- не накапливать `lap_distance_m` между кругами;
- не использовать session distance в качестве lap distance;
- отбрасывать `NaN`, `inf` и очевидные выбросы;
- при переходе через старт/финиш начинать новый набор точек;
- не соединять конец предыдущего круга с началом следующего;
- валидную длину из игры считать приоритетной;
- базу использовать как fallback.

## Источники и лицензирование

Список 25 трасс проверен по актуальному перечню ACC с базовой игрой и DLC.

Длины и game-oriented идентификаторы собраны на основе проекта **Lovely Track Data**.
Проект указывает лицензию **CC BY-NC-SA 4.0**. Перед коммерческим распространением
проверь совместимость лицензий и сохрани атрибуцию.

Схематичные SVG-карты в этом архиве созданы заново и являются fallback-рисунками,
а не копиями официальных карт трасс.
