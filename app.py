from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import sqlite3
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

# Настройка папки для загрузки
UPLOAD_FOLDER = '/tmp/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 МБ

# ---------- База данных ----------
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

# ---------- Эндпоинты ----------
@app.route('/upload', methods=['POST'])
def upload():
    try:
        meter_value = request.form.get('meter_reading', 'не_указано')
        qr_data = request.form.get('qr', 'нет_qr')
        timestamp = request.form.get('timestamp', datetime.now().isoformat())
        photo_file = request.files.get('photo')

        time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_meter = ''.join(c for c in meter_value if c.isdigit()) or "no_value"
        base_filename = f"{time_str}_{safe_meter}"

        photo_filename = None
        if photo_file and photo_file.filename != '':
            filename = secure_filename(f"{base_filename}.jpg")
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            photo_file.save(filepath)
            photo_filename = filename

        # Московское время (UTC+3)
        moscow_tz = timezone(timedelta(hours=3))
        moscow_now = datetime.now(moscow_tz).strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, photo_filename, timestamp, moscow_now))
        conn.commit()
        conn.close()

        return jsonify({
            "status": "ok",
            "message": f"OK. Meter reading: {meter_value}",
            "id": cursor.lastrowid
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

if __name__ == '__main__':
    app.run(debug=True)
