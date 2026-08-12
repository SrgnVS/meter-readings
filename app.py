from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
import requests
import html

app = Flask(__name__)
CORS(app)

# ---------- НАСТРОЙКА ХРАНИЛИЩА ----------
UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

STORAGE_API_KEY = os.environ.get('STORAGE_API_KEY')
STORAGE_UPLOAD_URL = "https://relaxdev.ru/api/v1/storage/upload"

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
        # ЗАГОЛОВКИ ТОЛЬКО НА РУССКОМ (без лишних символов)
        cw.writerow(['ID', 'QR', 'Показания', 'Ссылка на фото', 'Время отправки (МСК)', 'Время сохранения (МСК)'])

        moscow_tz = timezone(timedelta(hours=3))

        for row in rows:
            # Преобразуем timestamp
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

# ---------- ЭНДПОИНТЫ ----------
@app.route('/upload', methods=['POST'])
def upload():
    try:
        meter_value = request.form.get('meter_reading', 'не_указано')
        qr_data = request.form.get('qr', 'нет_qr')
        timestamp = request.form.get('timestamp', datetime.now().isoformat())
        photo_file = request.files.get('photo')

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
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                photo_file.save(filepath)
                photo_url = f"/local_photo/{filename}"
            else:
                headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
                response = requests.post(STORAGE_UPLOAD_URL, headers=headers, files=files, data={'webp': 'false'})
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

        # Сохраняем в БД
        moscow_now_db = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, photo_url, timestamp, moscow_now_db))
        conn.commit()
        conn.close()

        # ---- БЭКАП В STORAGE ----
        try:
            backup_readings_to_storage()
        except Exception as e:
            print(f"Ошибка при бэкапе: {e}")

        return jsonify({
            "status": "ok",
            "message": f"OK. Meter reading: {meter_value}",
            "id": cursor.lastrowid,
            "photo_url": photo_url
        }), 200
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

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
        latest_file = files_sorted[0]
        file_path = latest_file['path']

        if file_path.startswith('/'):
            file_url = f"https://cdn.relaxdev.ru{file_path}"
        else:
            file_url = f"https://cdn.relaxdev.ru/{file_path}"

        # СКАЧИВАЕМ СОДЕРЖИМОЕ И ЯВНО ДЕКОДИРУЕМ В UTF-8-SIG
        csv_response = requests.get(file_url)
        if csv_response.status_code != 200:
            return f"Не удалось загрузить бэкап: {csv_response.status_code}", 500

        # Декодируем содержимое с учётом BOM
        csv_content = csv_response.content.decode('utf-8-sig')

        # Читаем CSV
        import csv
        from io import StringIO
        reader = csv.reader(StringIO(csv_content), delimiter=';')
        rows = list(reader)
        if not rows:
            return "Бэкап пуст", 404

        # Экранируем HTML-символы
        import html
        escaped_rows = []
        for row in rows:
            escaped_rows.append([html.escape(cell) for cell in row])

        headers_row = escaped_rows[0]
        data_rows = escaped_rows[1:]

        last_modified = datetime.fromisoformat(latest_file['lastModified']).strftime('%d.%m.%Y %H:%M:%S')

        # Строим страницу с инлайн-стилями
        page = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Показания счётчиков</title>
</head>
<body style="font-family: system-ui, -apple-system, sans-serif; background: #f0f4f8; padding: 20px; margin: 0;">
    <div style="max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 16px;">
            <span style="font-size: 24px; font-weight: 600;">📊 Показания счётчиков</span>
            <span style="font-size: 14px; color: #64748b;">Последний бэкап: {last_modified}</span>
            <button onclick="location.reload()" style="background: #2563eb; color: white; border: none; padding: 8px 16px; border-radius: 8px; cursor: pointer; font-size: 14px;">🔄 Обновить</button>
        </div>
        <div style="overflow-x: auto;">
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <thead>
                    <tr>"""
        for col in headers_row:
            page += f"<th style='background: #2563eb; color: white; padding: 10px 12px; text-align: left; position: sticky; top: 0;'>{col}</th>"
        page += """</tr>
                </thead>
                <tbody>"""
        for row in data_rows:
            page += "<tr>"
            for cell in row:
                page += f"<td style='padding: 8px 12px; border-bottom: 1px solid #e5e7eb;'>{cell}</td>"
            page += "</tr>"
        page += f"""
                </tbody>
            </table>
        </div>
        <div style="margin-top: 16px; font-size: 12px; color: #64748b;">Всего записей: {len(data_rows)}</div>
    </div>
</body>
</html>"""
        return page
    except Exception as e:
        return f"Ошибка: {e}", 500

if __name__ == '__main__':
    app.run(debug=True)
