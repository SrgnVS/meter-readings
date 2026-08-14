from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    return "Hello, World!"

@app.route('/ping')
def ping():
    return "pong"

@app.route('/recognize', methods=['GET', 'POST'])
def recognize():
    return "OK", 200

if __name__ == '__main__':
    app.run()
