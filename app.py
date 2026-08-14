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
@app.route('/ping')
def ping():
    return "pong"
# ---------- ЭНДПОИНТ ДЛЯ РАСПОЗНАВАНИЯ (OCR) ----------
@app.route('/recognize', methods=['GET', 'POST'])
def recognize():
    if request.method == 'GET':
        return "OCR endpoint is working. Use POST with photo.", 200

    try:
        print("=== /recognize POST вызван ===")
        if 'photo' not in request.files:
            print("Нет файла photo в запросе")
            return jsonify({"error": "No photo"}), 400

        photo_file = request.files['photo']
        print(f"Файл: {photo_file.filename}")
        image_data = photo_file.read()
        print(f"Размер фото: {len(image_data)} байт")

        if len(image_data) == 0:
            print("Пустое изображение")
            return jsonify({"digits": ""}), 200

        img = Image.open(io.BytesIO(image_data))
        print(f"Изображение открыто: {img.size}")

        result = reader.readtext(img, detail=1, paragraph=False)
        print(f"Результат распознавания: {result}")

        if not result:
            print("Ничего не распознано")
            return jsonify({"digits": ""}), 200

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
            
            if not STORAGE_API_KEY:
                print("STORAGE_API_KEY не найден!")
                filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                photo_file.save(filepath)
                photo_url = f"/local_photo/{filename}"
            else:
                # ИСПРАВЛЕНО: Сброс указателя потока для избежания проблем с чтением
                photo_file.stream.seek(0)
                files = {'file': (filename, photo_file.stream, 'image/jpeg')}
                headers = {'Authorization': f'Bearer {STORAGE_API_KEY}'}
                response = requests.post(
                    STORAGE_UPLOAD_URL,
                    headers=headers,
                    files=files,
                    data={'webp': 'false'}
                )

                if response.status_code == 200:
                    data = response.json()
                    # ИСПРАВЛЕНО: Дописана логика при успешной отправке во внешнее хранилище
                    if data.get('success'):
                        photo_url = data.get('url') or f"https://relaxdev.ru{filename}"
                
                # Если апи-ключ есть, но загрузка не удалась, пишем локально как фолбек
                if not photo_url:
                    photo_file.stream.seek(0)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    photo_file.save(filepath)
                    photo_url = f"/local_photo/{filename}"

        # ИСПРАВЛЕНО: Сохранение результатов в БД SQLite и ответ клиенту
        created_at_str = datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, photo_url, timestamp, created_at_str))
        conn.commit()
        conn.close()

        # Запуск фонового бэкапа (для продакшна лучше делать асинхронно через Celery или потоки)
        backup_readings_to_storage()

        return jsonify({"status": "success", "message": "Данные успешно сохранены", "photo_url": photo_url}), 200

    except Exception as e:
        print(f"Ошибка в /upload: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
