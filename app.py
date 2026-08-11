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
    """Генерирует CSV из всех записей и загружает в хранилище RelaxDev по пути data/readings.csv"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            print("Нет данных для CSV, пропускаем обновление.")
            return

        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(['id', 'qr', 'meter_reading', 'photo_url', 'timestamp', 'created_at'])
        for row in rows:
            cw.writerow([row['id'], row['qr'], row['meter_reading'], row['photo_filename'], row['timestamp'], row['created_at']])

        csv_data = si.getvalue().encode('utf-8')

        files = {'file': ('readings.csv', csv_data, 'text/csv')}
        data = {'path': 'data', 'webp': 'false'}  # сохраняем в папку data, не конвертируем в webp
        headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}

        response = requests.post(STORAGE_UPLOAD_URL, headers=headers, files=files, data=data)

        if response.status_code == 200:
            print("✅ CSV успешно обновлён в хранилище")
            print(f"Ссылка: https://cdn.relaxdev.ru/users/.../data/readings.csv")
        else:
            print(f"❌ Ошибка обновления CSV: {response.status_code} - {response.text}")
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
    cw = csv.writer(si)
    cw.writerow(['id', 'qr', 'meter_reading', 'photo_url', 'timestamp', 'created_at'])
    for row in rows:
        cw.writerow([row['id'], row['qr'], row['meter_reading'], row['photo_filename'], row['timestamp'], row['created_at']])

    response = app.response_class(
        si.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment;filename=readings.csv'}
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
