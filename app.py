from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
import requests
import html

# ========== НОВЫЕ ИМПОРТЫ ДЛЯ OCR ==========
import easyocr
import io
from PIL import Image

app = Flask(__name__)
CORS(app)

# ---------- НАСТРОЙКА ХРАНИЛИЩА ----------
UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

STORAGE_API_KEY = os.environ.get('STORAGE_API_KEY')
STORAGE_UPLOAD_URL = "https://relaxdev.ru/api/v1/storage/upload"

# ========== ИНИЦИАЛИЗАЦИЯ EASYOCR (ГЛОБАЛЬНО) ==========
reader = easyocr.Reader(['en'], gpu=False)

# ---------- БАЗА ДАННЫХ ----------
def get_db():
    db_path = os.path.join(os.path.dirname(__file__), 'meter_data.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS readings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            qr TEXT,
            meter_reading TEXT,
            photo_filename TEXT,
            timestamp TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ---------- ФУНКЦИЯ БЭКАПА В STORAGE ----------
def backup_readings_to_storage():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("Нет данных для бэкапа")
            return

        si = StringIO()
        cw = csv.writer(si, delimiter=';', quoting=csv.QUOTE_ALL)
        cw.writerow(['ID', 'QR', 'Показания', 'Ссылка на фото', 'Время отправки (МСК)', 'Время сохранения (МСК)'])

        moscow_tz = timezone(timedelta(hours=3))

        for row in rows:
            ts_str = row['timestamp'] or ''
            if ts_str:
                try:
                    dt_utc = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    dt_moscow = dt_utc.astimezone(moscow_tz)
                    ts_formatted = dt_moscow.strftime('%d.%m.%y %H:%M')
                except:
                    ts_formatted = ts_str
            else:
                ts_formatted = ''

            created_str = row['created_at'] or ''
            if created_str:
                try:
                    dt = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
                    created_formatted = dt.strftime('%d.%m.%y %H:%M')
                except:
                    created_formatted = created_str
            else:
                created_formatted = ''

            cw.writerow([
                row['id'],
                row['qr'] or '',
                row['meter_reading'] or '',
                row['photo_filename'] or '',
                ts_formatted,
                created_formatted
            ])

        csv_data = si.getvalue().encode('utf-8-sig')
        filename = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        files = {'file': (filename, csv_data, 'text/csv')}
        data = {'path': 'data/backups', 'webp': 'false'}
        headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
        response = requests.post(STORAGE_UPLOAD_URL, headers=headers, files=files, data=data)

        if response.status_code == 200:
            print(f"✅ Бэкап сохранён: data/backups/{filename}")
        else:
            print(f"❌ Ошибка: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Исключение: {e}")

# ---------- ЭНДПОИНТ ДЛЯ РАСПОЗНАВАНИЯ (OCR) ----------
@app.route('/recognize', methods=['POST'])
def recognize():
    try:
        print("=== /recognize вызван ===")
        if 'photo' not in request.files:
            print("Нет файла photo в запросе")
            return jsonify({"error": "No photo"}), 400

        photo_file = request.files['photo']
        print(f"Имя файла: {photo_file.filename}")
        
        image_data = photo_file.read()
        print(f"Размер фото: {len(image_data)} байт")
        
        if len(image_data) == 0:
            print("Пустое изображение")
            return jsonify({"digits": ""}), 200

        # Открываем изображение через PIL
        img = Image.open(io.BytesIO(image_data))
        print(f"Изображение открыто: {img.size}")

        # Распознаём текст
        result = reader.readtext(img, detail=1, paragraph=False)
        print(f"Результат распознавания: {result}")

        if not result:
            print("Ничего не распознано")
            return jsonify({"digits": ""}), 200

        # Собираем все цифры
        all_digits = ""
        for (bbox, text, confidence) in result:
            print(f"Распознан текст: '{text}' с уверенностью {confidence:.2f}")
            digits = ''.join(ch for ch in text if ch.isdigit())
            if digits:
                all_digits += digits

        print(f"Итоговые цифры: '{all_digits}'")
        return jsonify({"digits": all_digits}), 200

    except Exception as e:
        print(f"OCR ошибка: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ---------- ОСНОВНОЙ ЭНДПОИНТ /upload ----------
@app.route('/upload', methods=['POST'])
def upload():
    try:
        meter_value = request.form.get('meter_reading', 'не_указано')
        qr_data = request.form.get('qr', 'нет_qr')
        timestamp = request.form.get('timestamp', datetime.now().isoformat())
        photo_file = request.files.get('photo')

        # Проверка на рост показаний
        if qr_data and qr_data != 'нет_qr':
            conn_check = get_db()
            cursor_check = conn_check.cursor()
            cursor_check.execute(
                'SELECT meter_reading FROM readings WHERE qr = ? ORDER BY created_at DESC LIMIT 1',
                (qr_data,)
            )
            last_row = cursor_check.fetchone()
            conn_check.close()

            if last_row:
                try:
                    last_val = float(last_row['meter_reading'].replace(',', '.'))
                    new_val = float(meter_value.replace(',', '.'))
                    if new_val <= last_val:
                        return jsonify({
                            "status": "error",
                            "message": f"Новое показание ({meter_value}) должно быть больше предыдущего ({last_row['meter_reading']})"
                        }), 400
                except ValueError:
                    pass

        # Очищаем QR для имени файла
        qr_clean = ''.join(c for c in qr_data if c.isalnum() or c in '-_')
        if not qr_clean:
            qr_clean = "unknown"

        moscow_tz = timezone(timedelta(hours=3))
        moscow_now = datetime.now(moscow_tz).strftime("%Y-%m-%d_%H-%M-%S")
        base_filename = f"{qr_clean}_{moscow_now}"

        photo_url = None
        if photo_file and photo_file.filename != '':
            filename = secure_filename(f"{base_filename}.jpg")
            files = {'file': (filename, photo_file.stream, 'image/jpeg')}

            if not STORAGE_API_KEY:
                print("STORAGE_API_KEY не найден!")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                photo_file.save(filepath)
                photo_url = f"/local_photo/{filename}"
            else:
                headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
                response = requests.post(
                    STORAGE_UPLOAD_URL,
                    headers=headers,
                    files=files,
                    data={'webp': 'false'}
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get('success'):
                        photo_url = data.get('url')
                        print(f"Фото загружено: {photo_url}")
                    else:
                        print(f"Ошибка API: {data}")
                else:
                    print(f"Ошибка загрузки фото: {response.status_code} - {response.text}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    photo_file.save(filepath)
                    photo_url = f"/local_photo/{filename}"

        # Сохранение в БД
        moscow_now_db = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, photo_url, timestamp, moscow_now_db))
        conn.commit()
        conn.close()

        try:
            backup_readings_to_storage()
        except Exception as e:
            print(f"Ошибка бэкапа: {e}")

        return jsonify({
            "status": "ok",
            "message": f"OK. Meter reading: {meter_value}",
            "id": cursor.lastrowid,
            "photo_url": photo_url
        }), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ---------- ДОПОЛНИТЕЛЬНЫЕ ЭНДПОИНТЫ ----------
@app.route('/')
def index():
    return 'Server for meter readings is running!'

@app.route('/readings', methods=['GET'])
def get_readings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.route('/download_db')
def download_db():
    db_path = os.path.join(os.path.dirname(__file__), 'meter_data.db')
    if not os.path.exists(db_path):
        return "База данных ещё не создана.", 404
    return send_file(db_path, as_attachment=True)

@app.route('/export')
def export_csv():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si, delimiter=';', quoting=csv.QUOTE_ALL)
    cw.writerow(['ID', 'QR', 'Показания', 'Ссылка на фото', 'Время отправки (UTC)', 'Время сохранения (МСК)'])
    for row in rows:
        cw.writerow([
            row['id'],
            row['qr'] or '',
            row['meter_reading'] or '',
            row['photo_filename'] or '',
            row['timestamp'] or '',
            row['created_at'] or ''
        ])

    response = app.response_class(
        si.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename=readings_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'}
    )
    return response

@app.route('/local_photo/<filename>')
def get_local_photo(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------- СТРАНИЦА ДЛЯ ПРОСМОТРА ПОСЛЕДНЕГО БЭКАПА ----------
@app.route('/readings_view')
def readings_view():
    try:
        list_url = "https://relaxdev.ru/api/v1/storage/files?path=data/backups"
        headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
        response = requests.get(list_url, headers=headers)
        if response.status_code != 200:
            return f"Ошибка получения списка: {response.status_code}", 500

        data = response.json()
        if not data.get('success'):
            return "API вернул ошибку", 500

        files = data.get('files', [])
        if not files:
            return "Нет бэкапов в папке data/backups", 404

        files_sorted = sorted(files, key=lambda f: f.get('lastModified', ''), reverse=True)

        unique_records = {}
        for file_info in files_sorted:
            file_path = file_info['path']
            if file_path.startswith('/'):
                file_url = f"https://cdn.relaxdev.ru{file_path}"
            else:
                file_url = f"https://cdn.relaxdev.ru/{file_path}"

            csv_response = requests.get(file_url)
            if csv_response.status_code != 200:
                continue

            csv_content = csv_response.content.decode('utf-8-sig')
            reader_csv = csv.reader(StringIO(csv_content), delimiter=';')
            rows = list(reader_csv)
            if not rows:
                continue

            for row in rows[1:]:
                if len(row) < 6:
                    continue
                qr = row[1].strip()
                if not qr:
                    continue
                created_at = row[5].strip()
                key = (qr, created_at)
                if key not in unique_records:
                    unique_records[key] = {
                        'qr': qr,
                        'meter_reading': row[2].strip(),
                        'photo_url': row[3].strip(),
                        'timestamp': row[4].strip(),
                        'created_at': created_at
                    }

        if not unique_records:
            return "Нет данных в бэкапах", 404

        grouped = {}
        for key, rec in unique_records.items():
            qr = rec['qr']
            if qr not in grouped:
                grouped[qr] = []
            grouped[qr].append(rec)

        result_rows = []
        for qr, recs in grouped.items():
            sorted_recs = sorted(recs, key=lambda x: x['created_at'], reverse=True)
            current = sorted_recs[0] if len(sorted_recs) > 0 else None
            previous = sorted_recs[1] if len(sorted_recs) > 1 else None

            diff = ''
            if previous and current:
                try:
                    prev_val = float(previous['meter_reading'].replace(',', '.'))
                    curr_val = float(current['meter_reading'].replace(',', '.'))
                    diff_val = curr_val - prev_val
                    if diff_val.is_integer():
                        diff = f"{int(diff_val)} кВт⋅ч"
                    else:
                        diff = f"{diff_val:.1f} кВт⋅ч"
                except:
                    diff = '—'

            result_rows.append({
                'qr': qr,
                'previous_meter': previous['meter_reading'] if previous else '',
                'current_meter': current['meter_reading'] if current else '',
                'previous_created_at': previous['created_at'] if previous else '',
                'current_created_at': current['created_at'] if current else '',
                'diff': diff,
                'photo_url': current['photo_url'] if current else '',
            })

        result_rows.sort(key=lambda x: x['qr'])

        last_modified_raw = files_sorted[0]['lastModified']
        dt_utc = datetime.fromisoformat(last_modified_raw.replace('Z', '+00:00'))
        moscow_tz = timezone(timedelta(hours=3))
        dt_moscow = dt_utc.astimezone(moscow_tz)
        last_modified_str = dt_moscow.strftime('%d.%m.%Y %H:%M:%S')

        page = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Показания счётчиков (последние два)</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; background: #e9edf2; padding: 20px; margin: 0; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: #ffffff; padding: 24px; border-radius: 16px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); border: 1px solid #dce3ec; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }}
        .title {{ font-size: 22px; font-weight: 600; color: #1e2a3a; }}
        .badge {{ font-size: 14px; color: #4a5b6e; background: #eef2f7; padding: 4px 12px; border-radius: 20px; }}
        .refresh-btn {{ background: #3a6b9b; color: white; border: none; padding: 8px 18px; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; transition: background 0.2s; }}
        .refresh-btn:hover {{ background: #2c5780; }}
        .refresh-btn:disabled {{ opacity: 0.6; cursor: not-allowed; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 14px; background: #ffffff; border-radius: 8px; overflow: hidden; }}
        th {{ background: #dce3ec; color: #1e2a3a; padding: 14px 16px; text-align: center; font-weight: 600; font-size: 13px; letter-spacing: 0.02em; border-bottom: 2px solid #c0ccdb; }}
        td {{ padding: 14px 16px; border-bottom: 1px solid #e6ecf3; text-align: center; color: #1e2a3a; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:nth-child(even) td {{ background: #f8faff; }}
        tr:hover td {{ background: #e6edf6; }}
        .photo-link {{ display: inline-block; background: #e0ecf9; color: #1e4a76; padding: 4px 14px; border-radius: 6px; text-decoration: none; font-weight: 500; font-size: 13px; transition: background 0.2s; }}
        .photo-link:hover {{ background: #b5cde0; }}
        .no-photo {{ color: #7a8a9e; }}
        .footer {{ margin-top: 20px; font-size: 13px; color: #4a5b6e; text-align: center; border-top: 1px solid #dce3ec; padding-top: 16px; }}
        @media (max-width: 600px) {{ body {{ padding: 10px; }} .container {{ padding: 16px; }} th, td {{ padding: 10px 8px; font-size: 12px; }} .title {{ font-size: 18px; }} }}
    </style>
    <script>
        function reloadPage() {{
            var btn = document.getElementById('refreshBtn');
            btn.disabled = true;
            btn.textContent = '⏳ Обновление...';
            setTimeout(function() {{
                location.reload();
            }}, 1000);
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <span class="title">📊 Показания счётчиков</span>
            <span class="badge">Обновлено: {last_modified_str} (МСК)</span>
            <button id="refreshBtn" class="refresh-btn" onclick="reloadPage()">🔄 Обновить</button>
        </div>
        <div style="overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th rowspan="2">Счётчик (QR)</th>
                        <th rowspan="2">Предыдущие показания, кВт⋅ч</th>
                        <th rowspan="2">Текущие показания, кВт⋅ч</th>
                        <th colspan="3">Затрачено кВт⋅ч</th>
                        <th rowspan="2">Ссылка на фото (текущее)</th>
                    </tr>
                    <tr>
                        <th>Дата предыдущих показаний</th>
                        <th>Дата текущих показаний</th>
                        <th>Затрачено</th>
                    </tr>
                </thead>
                <tbody>"""
        for row in result_rows:
            page += "<tr>"
            page += f"<td><strong>{row['qr']}</strong></td>"
            page += f"<td>{row['previous_meter']}</td>"
            page += f"<td>{row['current_meter']}</td>"
            page += f"<td>{row['previous_created_at']}</td>"
            page += f"<td>{row['current_created_at']}</td>"
            page += f"<td style='text-align: center; font-weight: 500; color: #1e5a3a;'>{row['diff']}</td>"
            if row['photo_url']:
                page += f"<td><a href='{row['photo_url']}' target='_blank' class='photo-link'>Фото</a></td>"
            else:
                page += "<td>—</td>"
            page += "</tr>"
        page += f"""
                </tbody>
            </table>
        </div>
        <div class="footer">Всего счётчиков: {len(result_rows)} (по два последних показания)</div>
    </div>
</body>
</html>"""
        return page
    except Exception as e:
        return f"Ошибка: {e}", 500

if __name__ == '__main__':
    app.run(debug=True)
