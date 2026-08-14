from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import csv
from io import StringIO
import requests
import html

app = Flask(__name__)
CORS(app)  # разрешаем запросы с любых доменов

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
        # Пока просто возвращаем успех
        return jsonify({"status": "ok", "message": "upload working"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run()
