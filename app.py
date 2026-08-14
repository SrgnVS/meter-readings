from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sqlite3
from datetime import datetime, timedelta, timezone

app = Flask(__name__)
CORS(app)

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

# ---------- ЭНДПОИНТЫ ----------
@app.route('/')
def index():
    return "Hello, World!"

@app.route('/ping')
def ping():
    return "pong"

@app.route('/recognize', methods=['GET', 'POST'])
def recognize():
    if request.method == 'GET':
        return "OCR endpoint is working (EasyOCR disabled).", 200
    return jsonify({"digits": "12345"}), 200

@app.route('/upload', methods=['POST'])
def upload():
    try:
        meter_value = request.form.get('meter_reading', 'не_указано')
        qr_data = request.form.get('qr', 'нет_qr')
        timestamp = request.form.get('timestamp', datetime.now().isoformat())

        # Создаём таблицу, если её нет
        init_db()

        moscow_tz = timezone(timedelta(hours=3))
        created_at_str = datetime.now(moscow_tz).strftime('%Y-%m-%d %H:%M:%S')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO readings (qr, meter_reading, photo_filename, timestamp, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (qr_data, meter_value, '', timestamp, created_at_str))
        conn.commit()
        conn.close()

        return jsonify({"status": "ok", "message": "Данные сохранены"}), 200
    except Exception as e:
        print(f"Ошибка в /upload: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/readings', methods=['GET'])
def get_readings():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM readings ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

if __name__ == '__main__':
    app.run()
