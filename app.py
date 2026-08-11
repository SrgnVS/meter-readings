from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
import requests






app = Flask(__name__)
CORS(app)

# ---------- НАСТРОЙКА ХРАНИЛИЩА ----------
UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

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

# ---------- ФУНКЦИЯ ОБНОВЛЕНИЯ CSV В ХРАНИЛИЩЕ ----------
def update_storage_csv():
    """Обновляет readings.csv и сохраняет предыдущую версию как readings_old.csv"""
    try:
        # 1. Генерируем новый CSV из БД
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("Нет данных для CSV, пропускаем обновление.")
            return

        si = StringIO()
        cw = csv.writer(si, delimiter=';', quoting=csv.QUOTE_ALL)
        cw.writerow([
            'ID записи',
            'Счётчик (QR)',
            'Показания',
            'Ссылка на фото',
            'Время отправки (UTC)',
            'Время сохранения (МСК)'
        ])
        for row in rows:
            cw.writerow([
                row['id'],
                row['qr'] or '',
                row['meter_reading'] or '',
                row['photo_filename'] or '',
                row['timestamp'] or '',
                row['created_at'] or ''
            ])

        new_csv_data = si.getvalue().encode('utf-8-sig')
        headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}

        # 2. Получаем список файлов в папке data
        list_url = "https://relaxdev.ru/api/v1/storage/files?path=data"
        list_resp = requests.get(list_url, headers=headers)
        if list_resp.status_code != 200:
            print("Не удалось получить список файлов")
            return

        files_list = list_resp.json().get('files', [])
        # Ищем пути к readings.csv и readings_old.csv
        old_path = None
        current_path = None
        for f in files_list:
            if f['path'].endswith('readings_old.csv'):
                old_path = f['path']
            elif f['path'].endswith('readings.csv'):
                current_path = f['path']

        # 3. Если есть readings_old.csv – удаляем его
        if old_path:
            delete_url = f"https://relaxdev.ru/api/v1/storage/files?path={old_path}"
            del_resp = requests.delete(delete_url, headers=headers)
            if del_resp.status_code == 200:
                print("Удалён старый readings_old.csv")
            else:
                print("Не удалось удалить readings_old.csv")

        # 4. Если есть readings.csv – скачиваем его и загружаем как readings_old.csv
        if current_path:
            # Скачиваем текущий файл
            download_url = f"https://cdn.relaxdev.ru/{current_path}"  # предполагаем, что URL формируется так
            # Но лучше использовать прямой URL из списка файлов
            old_file_url = f"https://cdn.relaxdev.ru/{current_path}"
            old_resp = requests.get(old_file_url)
            if old_resp.status_code == 200:
                # Загружаем как readings_old.csv
                files_upload = {'file': ('readings_old.csv', old_resp.content, 'text/csv')}
                upload_data = {'path': 'data', 'webp': 'false'}
                upload_resp = requests.post(
                    STORAGE_UPLOAD_URL,
                    headers=headers,
                    files=files_upload,
                    data=upload_data
                )
                if upload_resp.status_code == 200:
                    print("Сохранена копия как readings_old.csv")
                else:
                    print("Не удалось загрузить readings_old.csv")
            else:
                print("Не удалось скачать текущий readings.csv")

        # 5. Загружаем новый CSV как readings.csv
        files_upload = {'file': ('readings.csv', new_csv_data, 'text/csv')}
        upload_data = {'path': 'data', 'webp': 'false'}
        upload_resp = requests.post(
            STORAGE_UPLOAD_URL,
            headers=headers,
            files=files_upload,
            data=upload_data
        )

        if upload_resp.status_code == 200:
            print("✅ Новый readings.csv загружен")
        else:
            print(f"❌ Ошибка загрузки readings.csv: {upload_resp.status_code} - {upload_resp.text}")

    except Exception as e:
        print(f"❌ Исключение при обновлении CSV: {e}")
        
    
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
                print("STORAGE_API_KEY не найден в переменных окружения!")
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
                        print(f"Фото загружено в хранилище: {photo_url}")
                    else:
                        print(f"Ошибка API: {data}")
                else:
                    print(f"Ошибка загрузки фото: {response.status_code} - {response.text}")
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    photo_file.save(filepath)
                    photo_url = f"/local_photo/{filename}"

        # Сохраняем запись в БД
        moscow_now_db = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, photo_url, timestamp, moscow_now_db))
        conn.commit()
        conn.close()

        # ---- ОБНОВЛЯЕМ CSV В ХРАНИЛИЩЕ (асинхронно, чтобы не задерживать ответ) ----
        try:
            update_storage_csv()
        except Exception as e:
            print(f"Ошибка при обновлении CSV: {e}")

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
    cw.writerow([
        'ID записи',
        'Счётчик (QR)',
        'Показания',
        'Ссылка на фото',
        'Время отправки (UTC)',
        'Время сохранения (МСК)'
    ])
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

# Опционально: ручное обновление CSV (можно вызвать в браузере)
@app.route('/refresh_csv')
def refresh_csv():
    update_storage_csv()
    return "CSV обновлён"

if __name__ == '__main__':
    app.run(debug=True)




















import csv
from io import StringIO
import requests
from datetime import datetime
import os

# ... (ваш существующий код) ...

@app.route('/readings_view')
def readings_view():
    """
    Отображает последний бэкап из Storage в виде HTML-таблицы.
    """
    try:
        # 1. Получаем список файлов в папке 'backups'
        list_url = "https://relaxdev.ru/api/v1/storage/files?path=backups"
        headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
        response = requests.get(list_url, headers=headers)
        
        if response.status_code != 200:
            return "Не удалось получить список файлов", 500
        
        files = response.json().get('files', [])
        if not files:
            return "Нет бэкапов в хранилище", 404
        
        # 2. Находим самый свежий файл (по lastModified)
        latest_file = max(files, key=lambda f: f.get('lastModified', ''))
        file_path = latest_file['path']
        
        # 3. Загружаем содержимое файла
        file_url = f"https://cdn.relaxdev.ru/{file_path}"
        csv_response = requests.get(file_url)
        if csv_response.status_code != 200:
            return "Не удалось загрузить бэкап", 500
        
        csv_content = csv_response.text
        
        # 4. Парсим CSV
        reader = csv.reader(StringIO(csv_content), delimiter=';')
        rows = list(reader)
        if not rows:
            return "Бэкап пуст", 404
        
        headers_row = rows[0]
        data_rows = rows[1:]
        
        # 5. Формируем HTML-страницу с таблицей
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Показания счётчиков</title>
            <style>
                body {{
                    font-family: system-ui, sans-serif;
                    background: #f0f4f8;
                    padding: 20px;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background: white;
                    padding: 20px;
                    border-radius: 16px;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.1);
                }}
                h1 {{
                    font-size: 24px;
                    margin-bottom: 16px;
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                }}
                .badge {{
                    font-size: 14px;
                    font-weight: normal;
                    color: #64748b;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    font-size: 14px;
                }}
                th {{
                    background: #2563eb;
                    color: white;
                    padding: 10px 12px;
                    text-align: left;
                    position: sticky;
                    top: 0;
                }}
                td {{
                    padding: 8px 12px;
                    border-bottom: 1px solid #e5e7eb;
                }}
                tr:hover {{
                    background: #f8fafc;
                }}
                .photo-link {{
                    color: #2563eb;
                    text-decoration: none;
                }}
                .photo-link:hover {{
                    text-decoration: underline;
                }}
                .empty {{
                    text-align: center;
                    color: #64748b;
                    padding: 40px;
                }}
                .refresh-btn {{
                    background: #2563eb;
                    color: white;
                    border: none;
                    padding: 8px 16px;
                    border-radius: 8px;
                    cursor: pointer;
                    font-size: 14px;
                }}
                .refresh-btn:hover {{
                    background: #1d4ed8;
                }}
                .timestamp {{
                    color: #64748b;
                    font-size: 12px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>
                    📊 Показания счётчиков
                    <span class="badge">Последний бэкап: {datetime.fromisoformat(latest_file['lastModified']).strftime('%d.%m.%Y %H:%M:%S')}</span>
                    <button class="refresh-btn" onclick="location.reload()">🔄 Обновить</button>
                </h1>
                <div style="overflow-x: auto;">
                    <table>
                        <thead>
                            <tr>
                                {''.join(f'<th>{col}</th>' for col in headers_row)}
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(
                                f'<tr>{''.join(f'<td>{col}</td>' for col in row)}</tr>'
                                for row in data_rows
                            )}
                        </tbody>
                    </table>
                </div>
                <div style="margin-top: 16px; font-size: 12px; color: #64748b;">
                    Всего записей: {len(data_rows)}
                </div>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        return f"Ошибка: {e}", 500
