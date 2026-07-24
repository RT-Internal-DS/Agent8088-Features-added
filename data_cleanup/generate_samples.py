#!/usr/bin/env python3
"""
Generate high-quality training samples for tool-calling LoRA.
Each sample has a clear instruction and proper tool-call output.
"""

import json
import random

NEW_SAMPLES = []

# ============================================================================
# SHELL COMMANDS (execute_shell) - Simple commands that run immediately
# ============================================================================

SHELL_SAMPLES = [
    # File listing and info
    ("List all files in the current directory with details", "ls -la"),
    ("Show the 10 largest files in the current directory", "ls -lhS | head -10"),
    ("Find all PDF files in the current directory", "find . -name '*.pdf' -type f"),
    ("Count the number of files in this directory", "ls -1 | wc -l"),
    ("Show hidden files only", "ls -la | grep '^\\.'"),
    ("List files modified in the last 24 hours", "find . -mtime -1 -type f"),
    ("Show file sizes in human readable format", "du -sh *"),
    ("Find all empty files", "find . -type f -empty"),
    ("List directories only", "ls -d */"),
    ("Show the newest file in the directory", "ls -t | head -1"),
    
    # Disk and system info
    ("Check disk usage", "df -h"),
    ("Show free memory", "free -h"),
    ("Display system uptime", "uptime"),
    ("Show CPU information", "lscpu"),
    ("Check current user", "whoami"),
    ("Show current working directory", "pwd"),
    ("Display environment variables", "env"),
    ("Show kernel version", "uname -r"),
    ("List running processes", "ps aux"),
    ("Show top 5 CPU-consuming processes", "ps aux --sort=-%cpu | head -6"),
    ("Check network interfaces", "ip addr"),
    ("Show listening ports", "ss -tulnp"),
    ("Display routing table", "ip route"),
    ("Check DNS servers", "cat /etc/resolv.conf"),
    ("Show system date and time", "date"),
    
    # File operations
    ("Create a directory called 'backup'", "mkdir -p backup"),
    ("Remove empty directory 'temp'", "rmdir temp"),
    ("Copy file.txt to backup folder", "cp file.txt backup/"),
    ("Move config.json to /tmp", "mv config.json /tmp/"),
    ("Create an empty file called notes.txt", "touch notes.txt"),
    ("Delete all .log files", "rm -f *.log"),
    ("Change permissions to executable", "chmod +x script.sh"),
    ("Show first 20 lines of a file", "head -20 file.txt"),
    ("Show last 50 lines of a log", "tail -50 /var/log/syslog"),
    ("Search for 'error' in log files", "grep -r 'error' /var/log/"),
    ("Count lines in a file", "wc -l data.txt"),
    ("Sort file contents alphabetically", "sort names.txt"),
    ("Remove duplicate lines", "sort -u data.txt"),
    ("Compare two files", "diff file1.txt file2.txt"),
    ("Find and replace text in file", "sed -i 's/old/new/g' file.txt"),
    
    # Process management
    ("Kill process by name", "pkill -f process_name"),
    ("Find process ID of nginx", "pgrep nginx"),
    ("Show process tree", "pstree"),
    ("Run command in background", "nohup ./long_task.sh &"),
    ("Check if a service is running", "systemctl is-active nginx"),
    ("Restart a service", "sudo systemctl restart nginx"),
    ("View service logs", "journalctl -u nginx --since '1 hour ago'"),
    
    # Network commands
    ("Ping google.com", "ping -c 4 google.com"),
    ("Check if port 80 is open", "nc -zv localhost 80"),
    ("Download a file from URL", "wget https://example.com/file.zip"),
    ("Fetch URL content", "curl -s https://api.example.com/data"),
    ("Show active network connections", "netstat -an | grep ESTABLISHED"),
    ("Trace route to host", "traceroute google.com"),
    ("Lookup DNS record", "dig example.com"),
    ("Show public IP address", "curl -s ifconfig.me"),
    
    # Git commands
    ("Check git status", "git status"),
    ("Show recent commits", "git log --oneline -10"),
    ("Create a new branch", "git checkout -b feature-branch"),
    ("Pull latest changes", "git pull origin main"),
    ("Stage all changes", "git add ."),
    ("Commit with message", "git commit -m 'Update config'"),
    ("Push to remote", "git push origin main"),
    ("Show git diff", "git diff"),
    ("List branches", "git branch -a"),
    ("Stash changes", "git stash"),
    
    # Package management
    ("Update package list", "sudo apt update"),
    ("Install a package", "sudo apt install -y htop"),
    ("List installed packages", "dpkg -l"),
    ("Search for a package", "apt search nodejs"),
    ("Check Python version", "python3 --version"),
    ("List pip packages", "pip3 list"),
    ("Install Python package", "pip3 install requests"),
    
    # Docker commands
    ("List running containers", "docker ps"),
    ("List all images", "docker images"),
    ("Stop a container", "docker stop container_name"),
    ("Remove stopped containers", "docker container prune -f"),
    ("Show container logs", "docker logs -f container_name"),
    ("Pull an image", "docker pull nginx:latest"),
    ("Build Docker image", "docker build -t myapp ."),
    
    # Archives and compression
    ("Create tar archive", "tar -cvf archive.tar directory/"),
    ("Extract tar.gz file", "tar -xzf archive.tar.gz"),
    ("Create zip file", "zip -r archive.zip directory/"),
    ("Unzip file", "unzip archive.zip"),
    ("Compress file with gzip", "gzip large_file.txt"),
    
    # Text processing
    ("Extract column from CSV", "cut -d',' -f2 data.csv"),
    ("Count word frequency", "cat text.txt | tr ' ' '\\n' | sort | uniq -c | sort -rn"),
    ("Convert to lowercase", "tr '[:upper:]' '[:lower:]' < input.txt"),
    ("Remove blank lines", "grep -v '^$' file.txt"),
    ("Print lines matching pattern", "awk '/error/ {print}' log.txt"),
]

for instruction, command in SHELL_SAMPLES:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{command}</parameter>
</invoke>
</function_calls>''',
        "category": "shell"
    })

# ============================================================================
# PYTHON SCRIPTS (file_write) - Write actual Python code
# ============================================================================

PYTHON_SCRIPTS = [
    # Basic scripts
    ("Write a Python script that prints Hello World", '''print("Hello, World!")'''),
    
    ("Write a Python function to calculate factorial", '''def factorial(n):
    if n < 0:
        raise ValueError("Factorial not defined for negative numbers")
    if n <= 1:
        return 1
    return n * factorial(n - 1)

# Test
print(factorial(5))  # 120'''),

    ("Write a Python script to read a JSON file", '''import json

with open('data.json', 'r') as f:
    data = json.load(f)

print(data)'''),

    ("Write a Python script to write data to a JSON file", '''import json

data = {
    "name": "John",
    "age": 30,
    "city": "New York"
}

with open('output.json', 'w') as f:
    json.dump(data, f, indent=2)

print("Data saved to output.json")'''),

    ("Write a Python script that reads a CSV file and prints its contents", '''import csv

with open('data.csv', 'r') as f:
    reader = csv.reader(f)
    for row in reader:
        print(row)'''),

    ("Write a Python script to make an HTTP GET request", '''import requests

response = requests.get('https://api.example.com/data')

if response.status_code == 200:
    print(response.json())
else:
    print(f"Error: {response.status_code}")'''),

    ("Write a Python script to make an HTTP POST request with JSON data", '''import requests

data = {
    "name": "Test",
    "value": 123
}

response = requests.post(
    'https://api.example.com/submit',
    json=data,
    headers={'Content-Type': 'application/json'}
)

print(f"Status: {response.status_code}")
print(response.json())'''),

    ("Write a Python function to check if a number is prime", '''def is_prime(n):
    if n < 2:
        return False
    if n == 2:
        return True
    if n % 2 == 0:
        return False
    for i in range(3, int(n**0.5) + 1, 2):
        if n % i == 0:
            return False
    return True

# Test
for i in range(20):
    if is_prime(i):
        print(f"{i} is prime")'''),

    ("Write a Python script to reverse a string", '''def reverse_string(s):
    return s[::-1]

text = "Hello, World!"
print(f"Original: {text}")
print(f"Reversed: {reverse_string(text)}")'''),

    ("Write a Python function to find the maximum value in a list", '''def find_max(numbers):
    if not numbers:
        return None
    max_val = numbers[0]
    for num in numbers[1:]:
        if num > max_val:
            max_val = num
    return max_val

numbers = [3, 7, 2, 9, 1, 5]
print(f"Max value: {find_max(numbers)}")'''),

    ("Write a Python script to count word frequency in a text file", '''from collections import Counter

with open('text.txt', 'r') as f:
    text = f.read().lower()

# Remove punctuation and split into words
words = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in text).split()

word_counts = Counter(words)

print("Top 10 most common words:")
for word, count in word_counts.most_common(10):
    print(f"  {word}: {count}")'''),

    ("Write a Python script to sort a list of dictionaries by a key", '''data = [
    {"name": "Alice", "age": 30},
    {"name": "Bob", "age": 25},
    {"name": "Charlie", "age": 35}
]

# Sort by age
sorted_by_age = sorted(data, key=lambda x: x["age"])
print("Sorted by age:", sorted_by_age)

# Sort by name
sorted_by_name = sorted(data, key=lambda x: x["name"])
print("Sorted by name:", sorted_by_name)'''),

    ("Write a Python script to merge two dictionaries", '''dict1 = {"a": 1, "b": 2}
dict2 = {"c": 3, "d": 4}

# Python 3.9+ way
merged = dict1 | dict2

# Alternative way (works in all Python 3 versions)
merged_alt = {**dict1, **dict2}

print(merged)'''),

    ("Write a Python script to flatten a nested list", '''def flatten(nested_list):
    flat = []
    for item in nested_list:
        if isinstance(item, list):
            flat.extend(flatten(item))
        else:
            flat.append(item)
    return flat

nested = [1, [2, 3], [4, [5, 6]], 7]
print(flatten(nested))  # [1, 2, 3, 4, 5, 6, 7]'''),

    ("Write a Python script to validate an email address using regex", '''import re

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

emails = [
    "test@example.com",
    "invalid-email",
    "user.name@domain.co.uk",
    "@nodomain.com"
]

for email in emails:
    status = "valid" if is_valid_email(email) else "invalid"
    print(f"{email}: {status}")'''),

    ("Write a Python script to generate a random password", '''import random
import string

def generate_password(length=12):
    chars = string.ascii_letters + string.digits + string.punctuation
    password = ''.join(random.choice(chars) for _ in range(length))
    return password

print(generate_password(16))'''),

    ("Write a Python script to find all files with a specific extension in a directory", '''import os

def find_files(directory, extension):
    matches = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(extension):
                matches.append(os.path.join(root, file))
    return matches

# Find all .py files
python_files = find_files('.', '.py')
for f in python_files:
    print(f)'''),

    ("Write a Python script to calculate the Fibonacci sequence", '''def fibonacci(n):
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    
    fib = [0, 1]
    while len(fib) < n:
        fib.append(fib[-1] + fib[-2])
    return fib

print(fibonacci(10))'''),

    ("Write a Python script to remove duplicates from a list while preserving order", '''def remove_duplicates(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

data = [1, 2, 3, 2, 1, 4, 5, 4, 3]
print(remove_duplicates(data))  # [1, 2, 3, 4, 5]'''),

    ("Write a Python script to parse command line arguments", '''import argparse

parser = argparse.ArgumentParser(description='Process some data')
parser.add_argument('input', help='Input file path')
parser.add_argument('-o', '--output', default='output.txt', help='Output file path')
parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')

args = parser.parse_args()

print(f"Input: {args.input}")
print(f"Output: {args.output}")
print(f"Verbose: {args.verbose}")'''),

    ("Write a Python script to convert CSV to JSON", '''import csv
import json

def csv_to_json(csv_file, json_file):
    data = []
    with open(csv_file, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            data.append(row)
    
    with open(json_file, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Converted {len(data)} rows to JSON")

csv_to_json('input.csv', 'output.json')'''),

    ("Write a Python script to download a file from URL", '''import requests

def download_file(url, filename):
    response = requests.get(url, stream=True)
    response.raise_for_status()
    
    with open(filename, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    print(f"Downloaded {filename}")

download_file('https://example.com/file.zip', 'downloaded.zip')'''),

    ("Write a Python script to send an email", '''import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(to_email, subject, body):
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    from_email = "your-email@gmail.com"
    password = "your-app-password"
    
    msg = MIMEMultipart()
    msg['From'] = from_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(from_email, password)
        server.send_message(msg)
    
    print(f"Email sent to {to_email}")

send_email("recipient@example.com", "Test Subject", "This is the email body.")'''),

    ("Write a Python script to create a simple HTTP server", '''from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000

with HTTPServer(("", PORT), SimpleHTTPRequestHandler) as httpd:
    print(f"Serving at http://localhost:{PORT}")
    httpd.serve_forever()'''),

    ("Write a Python script to connect to a SQLite database", '''import sqlite3

conn = sqlite3.connect('database.db')
cursor = conn.cursor()

# Create table
cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT UNIQUE
    )
""")

# Insert data
cursor.execute("INSERT INTO users (name, email) VALUES (?, ?)", ("Alice", "alice@example.com"))
conn.commit()

# Query data
cursor.execute("SELECT * FROM users")
rows = cursor.fetchall()
for row in rows:
    print(row)

conn.close()'''),

    ("Write a Python script to read environment variables", '''import os

# Get single variable with default
db_host = os.environ.get('DB_HOST', 'localhost')
db_port = os.environ.get('DB_PORT', '5432')

print(f"Database: {db_host}:{db_port}")

# List all environment variables
for key, value in os.environ.items():
    print(f"{key}={value}")'''),

    ("Write a Python script to work with dates and times", '''from datetime import datetime, timedelta

# Current time
now = datetime.now()
print(f"Current: {now}")

# Format date
formatted = now.strftime("%Y-%m-%d %H:%M:%S")
print(f"Formatted: {formatted}")

# Parse date string
date_str = "2024-03-15"
parsed = datetime.strptime(date_str, "%Y-%m-%d")
print(f"Parsed: {parsed}")

# Date arithmetic
tomorrow = now + timedelta(days=1)
last_week = now - timedelta(weeks=1)
print(f"Tomorrow: {tomorrow}")
print(f"Last week: {last_week}")'''),

    ("Write a Python script to compress a file using gzip", '''import gzip
import shutil

def compress_file(input_file, output_file=None):
    if output_file is None:
        output_file = input_file + '.gz'
    
    with open(input_file, 'rb') as f_in:
        with gzip.open(output_file, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    
    print(f"Compressed {input_file} to {output_file}")

compress_file('large_file.txt')'''),

    ("Write a Python script to hash a password securely", '''import hashlib
import secrets

def hash_password(password):
    salt = secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode(),
        salt.encode(),
        100000
    )
    return f"{salt}:{hashed.hex()}"

def verify_password(password, stored_hash):
    salt, hash_value = stored_hash.split(':')
    new_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode(),
        salt.encode(),
        100000
    )
    return new_hash.hex() == hash_value

# Usage
stored = hash_password("mysecretpassword")
print(f"Stored hash: {stored}")
print(f"Verify: {verify_password('mysecretpassword', stored)}")'''),

    ("Write a Python script to monitor a directory for file changes", '''import time
import os

def watch_directory(path, interval=1):
    known_files = {}
    
    while True:
        current_files = {}
        for filename in os.listdir(path):
            filepath = os.path.join(path, filename)
            if os.path.isfile(filepath):
                mtime = os.path.getmtime(filepath)
                current_files[filename] = mtime
                
                if filename not in known_files:
                    print(f"New file: {filename}")
                elif known_files[filename] != mtime:
                    print(f"Modified: {filename}")
        
        for filename in known_files:
            if filename not in current_files:
                print(f"Deleted: {filename}")
        
        known_files = current_files
        time.sleep(interval)

watch_directory('.')'''),

    ("Write a Python script to parse and validate a URL", '''from urllib.parse import urlparse

def validate_url(url):
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def parse_url(url):
    parsed = urlparse(url)
    return {
        'scheme': parsed.scheme,
        'netloc': parsed.netloc,
        'path': parsed.path,
        'params': parsed.params,
        'query': parsed.query,
        'fragment': parsed.fragment
    }

urls = [
    "https://example.com/path?query=value#section",
    "not-a-url",
    "http://localhost:8080/api"
]

for url in urls:
    if validate_url(url):
        print(f"Valid: {url}")
        print(f"  Parsed: {parse_url(url)}")
    else:
        print(f"Invalid: {url}")'''),

    ("Write a Python class for a simple key-value store", '''import json
import os

class KeyValueStore:
    def __init__(self, filename='store.json'):
        self.filename = filename
        self.data = {}
        self._load()
    
    def _load(self):
        if os.path.exists(self.filename):
            with open(self.filename, 'r') as f:
                self.data = json.load(f)
    
    def _save(self):
        with open(self.filename, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def set(self, key, value):
        self.data[key] = value
        self._save()
    
    def get(self, key, default=None):
        return self.data.get(key, default)
    
    def delete(self, key):
        if key in self.data:
            del self.data[key]
            self._save()
    
    def keys(self):
        return list(self.data.keys())

# Usage
store = KeyValueStore()
store.set('name', 'Alice')
store.set('age', 30)
print(store.get('name'))
print(store.keys())'''),
]

for instruction, code in PYTHON_SCRIPTS:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.py</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>''',
        "category": "code"
    })

# ============================================================================
# BASH SCRIPTS (file_write) - Write actual bash scripts
# ============================================================================

BASH_SCRIPTS = [
    ("Write a bash script that backs up a directory", '''#!/bin/bash
SOURCE_DIR="$1"
BACKUP_DIR="$2"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -z "$SOURCE_DIR" ] || [ -z "$BACKUP_DIR" ]; then
    echo "Usage: $0 <source_dir> <backup_dir>"
    exit 1
fi

BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.tar.gz"
tar -czf "$BACKUP_FILE" "$SOURCE_DIR"
echo "Backup created: $BACKUP_FILE"'''),

    ("Write a bash script that monitors disk usage and sends alert if above threshold", '''#!/bin/bash
THRESHOLD=80
EMAIL="admin@example.com"

USAGE=$(df / | tail -1 | awk '{print $5}' | sed 's/%//')

if [ "$USAGE" -gt "$THRESHOLD" ]; then
    echo "Disk usage is ${USAGE}%, above ${THRESHOLD}% threshold" | mail -s "Disk Alert" $EMAIL
    echo "Alert sent!"
else
    echo "Disk usage is ${USAGE}%, OK"
fi'''),

    ("Write a bash script that rotates log files", '''#!/bin/bash
LOG_DIR="/var/log/myapp"
MAX_FILES=7

cd "$LOG_DIR" || exit 1

# Rotate existing logs
for i in $(seq $((MAX_FILES-1)) -1 1); do
    if [ -f "app.log.$i" ]; then
        mv "app.log.$i" "app.log.$((i+1))"
    fi
done

# Rotate current log
if [ -f "app.log" ]; then
    mv "app.log" "app.log.1"
fi

# Remove old logs
find . -name "app.log.*" -mtime +$MAX_FILES -delete

echo "Log rotation complete"'''),

    ("Write a bash script that monitors a process and restarts it if it dies", '''#!/bin/bash
PROCESS_NAME="myapp"
CHECK_INTERVAL=30

while true; do
    if ! pgrep -x "$PROCESS_NAME" > /dev/null; then
        echo "$(date): $PROCESS_NAME is not running, restarting..."
        /usr/bin/$PROCESS_NAME &
        sleep 5
    fi
    sleep $CHECK_INTERVAL
done'''),

    ("Write a bash script that cleans up old files", '''#!/bin/bash
TARGET_DIR="${1:-.}"
DAYS="${2:-30}"

echo "Cleaning files older than $DAYS days in $TARGET_DIR"

find "$TARGET_DIR" -type f -mtime +$DAYS -print -delete

echo "Cleanup complete"'''),

    ("Write a bash script that checks if a website is up", '''#!/bin/bash
URL="$1"
TIMEOUT=10

if [ -z "$URL" ]; then
    echo "Usage: $0 <url>"
    exit 1
fi

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout $TIMEOUT "$URL")

if [ "$HTTP_CODE" -eq 200 ]; then
    echo "OK: $URL is up (HTTP $HTTP_CODE)"
    exit 0
else
    echo "FAIL: $URL returned HTTP $HTTP_CODE"
    exit 1
fi'''),

    ("Write a bash script that creates a new user with SSH key", '''#!/bin/bash
USERNAME="$1"
SSH_KEY="$2"

if [ -z "$USERNAME" ]; then
    echo "Usage: $0 <username> [ssh_public_key]"
    exit 1
fi

# Create user
useradd -m -s /bin/bash "$USERNAME"

# Set up SSH directory
SSH_DIR="/home/$USERNAME/.ssh"
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"

# Add SSH key if provided
if [ -n "$SSH_KEY" ]; then
    echo "$SSH_KEY" >> "$SSH_DIR/authorized_keys"
    chmod 600 "$SSH_DIR/authorized_keys"
fi

chown -R "$USERNAME:$USERNAME" "$SSH_DIR"
echo "User $USERNAME created successfully"'''),

    ("Write a bash script that syncs two directories", '''#!/bin/bash
SOURCE="$1"
DEST="$2"

if [ -z "$SOURCE" ] || [ -z "$DEST" ]; then
    echo "Usage: $0 <source> <destination>"
    exit 1
fi

rsync -avz --delete --progress "$SOURCE/" "$DEST/"
echo "Sync complete"'''),

    ("Write a bash script that finds and kills zombie processes", '''#!/bin/bash
ZOMBIES=$(ps aux | awk '$8=="Z" {print $2}')

if [ -z "$ZOMBIES" ]; then
    echo "No zombie processes found"
    exit 0
fi

echo "Found zombie processes: $ZOMBIES"

for PID in $ZOMBIES; do
    PPID=$(ps -o ppid= -p $PID)
    echo "Killing parent process $PPID of zombie $PID"
    kill -9 $PPID 2>/dev/null
done

echo "Cleanup complete"'''),

    ("Write a bash script that generates a system report", '''#!/bin/bash
REPORT_FILE="/tmp/system_report_$(date +%Y%m%d).txt"

{
    echo "=== System Report ==="
    echo "Date: $(date)"
    echo ""
    echo "=== Hostname ==="
    hostname
    echo ""
    echo "=== Uptime ==="
    uptime
    echo ""
    echo "=== Memory ==="
    free -h
    echo ""
    echo "=== Disk Usage ==="
    df -h
    echo ""
    echo "=== Top Processes ==="
    ps aux --sort=-%mem | head -10
} > "$REPORT_FILE"

echo "Report saved to $REPORT_FILE"'''),

    ("Write a bash script that sets up a Python virtual environment", '''#!/bin/bash
VENV_NAME="${1:-venv}"

python3 -m venv "$VENV_NAME"
source "$VENV_NAME/bin/activate"
pip install --upgrade pip

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

echo "Virtual environment '$VENV_NAME' is ready"
echo "Activate with: source $VENV_NAME/bin/activate"'''),

    ("Write a bash script that deploys an application", '''#!/bin/bash
APP_DIR="/var/www/myapp"
GIT_REPO="https://github.com/user/repo.git"
BRANCH="${1:-main}"

echo "Deploying $BRANCH..."

cd "$APP_DIR" || exit 1

git fetch origin
git checkout "$BRANCH"
git pull origin "$BRANCH"

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

if [ -f "package.json" ]; then
    npm install
    npm run build
fi

sudo systemctl restart myapp
echo "Deployment complete"'''),

    ("Write a bash script that checks SSL certificate expiration", '''#!/bin/bash
DOMAIN="$1"
WARN_DAYS=30

if [ -z "$DOMAIN" ]; then
    echo "Usage: $0 <domain>"
    exit 1
fi

EXPIRY=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN":443 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

echo "Certificate for $DOMAIN expires in $DAYS_LEFT days"

if [ "$DAYS_LEFT" -lt "$WARN_DAYS" ]; then
    echo "WARNING: Certificate expires soon!"
    exit 1
fi'''),

    ("Write a bash script that creates a database backup", '''#!/bin/bash
DB_NAME="$1"
BACKUP_DIR="/backups/mysql"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

if [ -z "$DB_NAME" ]; then
    echo "Usage: $0 <database_name>"
    exit 1
fi

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

mysqldump -u root "$DB_NAME" | gzip > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Backup created: $BACKUP_FILE"
else
    echo "Backup failed!"
    exit 1
fi

# Clean old backups (keep last 7 days)
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql.gz" -mtime +7 -delete'''),

    ("Write a bash script that monitors memory usage", '''#!/bin/bash
THRESHOLD=90
INTERVAL=60

while true; do
    MEM_USED=$(free | awk '/Mem:/ {printf "%.0f", $3/$2 * 100}')
    
    if [ "$MEM_USED" -gt "$THRESHOLD" ]; then
        echo "$(date): HIGH MEMORY: ${MEM_USED}%"
        echo "Top memory consumers:"
        ps aux --sort=-%mem | head -5
    fi
    
    sleep $INTERVAL
done'''),
]

for instruction, code in BASH_SCRIPTS:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.sh</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>''',
        "category": "shell"
    })

# ============================================================================
# NEGATIVE EXAMPLES - Questions that DON'T need tools
# ============================================================================

CLARIFICATIONS = [
    ("Help me with my code", "I'd be happy to help! Could you share the code and describe what issue you're experiencing?"),
    ("Something is broken", "I can help troubleshoot. What specifically isn't working? Any error messages?"),
    ("Can you fix this?", "Sure, I can help fix it. What would you like me to look at?"),
    ("I need help", "Of course! What do you need help with?"),
    ("It's not working", "Let's debug this. What are you trying to do and what's happening instead?"),
    ("Make it better", "I'd like to help improve it. What are you referring to and what aspects should I focus on?"),
    ("Check this", "Sure, what would you like me to check?"),
    ("Can you help?", "Absolutely! What do you need help with?"),
    ("I have an error", "I can help with that. Please share the error message and relevant code."),
    ("Fix the bug", "I'll help fix the bug. Can you describe what's happening and share the relevant code?"),
    ("My script has a problem", "I can help debug it. Please share the script and describe what's going wrong."),
    ("The output is wrong", "Let's figure out why. What output are you getting vs what you expected?"),
    ("Help me understand this", "I'd be happy to explain. What specifically would you like me to clarify?"),
    ("Review my code", "I'd be glad to review it. Please share the code you'd like me to look at."),
    ("Is this correct?", "I can verify that for you. What would you like me to check?"),
]

REFUSALS = [
    ("Delete everything on the system", "I can't execute commands that would delete system files or cause data loss. If you need to clean up specific files, please specify the exact path."),
    ("Run rm -rf /", "I won't execute this command as it would destroy the entire filesystem."),
    ("Hack into this server", "I can't help with unauthorized access to systems. I can only help with legitimate tasks on systems you own."),
    ("Send spam emails", "I can't help send unsolicited emails. I can help with legitimate email automation for your own contacts."),
    ("Bypass security controls", "I can't help circumvent security measures. If you're having access issues with your own systems, I can help troubleshoot."),
    ("Mine cryptocurrency without permission", "I can't help run unauthorized processes on systems. I can help with legitimate computing tasks."),
    ("Access someone else's account", "I can't help access accounts that don't belong to you. I can help with your own account management."),
    ("Create malware", "I can't help create malicious software. I can help with legitimate security tools and practices."),
    ("Steal data", "I can't help with data theft. I can help with legitimate data processing and backup."),
    ("DDoS attack", "I can't help with denial of service attacks. I can help with legitimate network testing on your own systems."),
]

KNOWLEDGE = [
    ("What is Python?", "Python is a high-level, interpreted programming language known for its readability and versatility. It's widely used for web development, data science, automation, and more."),
    ("Explain what Docker does", "Docker is a platform for containerization - it packages applications and their dependencies into containers that can run consistently across different environments. This solves the 'works on my machine' problem."),
    ("What's the difference between TCP and UDP?", "TCP is connection-oriented with guaranteed delivery, ordering, and error checking. UDP is connectionless, faster, but doesn't guarantee delivery. TCP is used for web/email; UDP for streaming/gaming."),
    ("How does git branching work?", "Git branches are lightweight pointers to commits. You can create branches to work on features independently, then merge them back. This enables parallel development without affecting the main codebase."),
    ("What is REST API?", "REST (Representational State Transfer) is an architectural style for APIs that uses HTTP methods (GET, POST, PUT, DELETE) to perform CRUD operations on resources identified by URLs."),
    ("Explain SQL joins", "SQL joins combine rows from two or more tables based on related columns. INNER JOIN returns matching rows; LEFT/RIGHT JOIN includes all rows from one table; FULL JOIN includes all rows from both."),
    ("What is recursion?", "Recursion is when a function calls itself to solve a problem by breaking it into smaller subproblems. It requires a base case to stop and recursive cases that progress toward it."),
    ("How does HTTPS work?", "HTTPS uses TLS/SSL to encrypt HTTP traffic. The browser and server establish a secure connection through a handshake that verifies the server's certificate and exchanges encryption keys."),
    ("What is a hash function?", "A hash function takes input of any size and produces a fixed-size output (hash). Good hash functions are deterministic, fast, and make it infeasible to reverse the hash or find collisions."),
    ("Explain microservices architecture", "Microservices architecture breaks an application into small, independent services that communicate via APIs. Each service handles one function and can be developed, deployed, and scaled independently."),
]

for inst, out in CLARIFICATIONS:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "clarification"})

for inst, out in REFUSALS:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "refusal"})

for inst, out in KNOWLEDGE:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "knowledge"})

# ============================================================================
# DATA PROCESSING SAMPLES
# ============================================================================

DATA_SAMPLES = [
    ("Parse a JSON string in Python", '''import json

json_string = '{"name": "Alice", "age": 30}'
data = json.loads(json_string)
print(data['name'])  # Alice'''),

    ("Read a CSV file with pandas", '''import pandas as pd

df = pd.read_csv('data.csv')
print(df.head())
print(df.describe())'''),

    ("Filter rows in a pandas DataFrame", '''import pandas as pd

df = pd.read_csv('data.csv')

# Filter rows where age > 25
filtered = df[df['age'] > 25]

# Multiple conditions
filtered = df[(df['age'] > 25) & (df['status'] == 'active')]
print(filtered)'''),

    ("Group data and calculate statistics with pandas", '''import pandas as pd

df = pd.read_csv('sales.csv')

# Group by category and calculate sum
by_category = df.groupby('category')['revenue'].sum()

# Multiple aggregations
summary = df.groupby('category').agg({
    'revenue': ['sum', 'mean'],
    'quantity': 'sum'
})
print(summary)'''),

    ("Merge two DataFrames", '''import pandas as pd

df1 = pd.DataFrame({'id': [1, 2, 3], 'name': ['A', 'B', 'C']})
df2 = pd.DataFrame({'id': [1, 2, 4], 'value': [10, 20, 40]})

# Inner join
merged = pd.merge(df1, df2, on='id', how='inner')

# Left join
merged_left = pd.merge(df1, df2, on='id', how='left')
print(merged)'''),

    ("Clean missing data in pandas", '''import pandas as pd

df = pd.read_csv('data.csv')

# Check for missing values
print(df.isnull().sum())

# Drop rows with any missing values
df_clean = df.dropna()

# Fill missing values
df_filled = df.fillna({'age': df['age'].mean(), 'name': 'Unknown'})
print(df_filled)'''),
]

for instruction, code in DATA_SAMPLES:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.py</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>''',
        "category": "data"
    })

# ============================================================================
# SYSTEM ADMINISTRATION SAMPLES
# ============================================================================

SYSTEM_SAMPLES = [
    ("Check which process is using port 8080", "lsof -i :8080"),
    ("Show memory usage by process", "ps aux --sort=-%mem | head -20"),
    ("Find files larger than 100MB", "find / -type f -size +100M 2>/dev/null"),
    ("Check CPU temperature", "cat /sys/class/thermal/thermal_zone0/temp"),
    ("List all cron jobs", "crontab -l"),
    ("Show system boot time", "who -b"),
    ("Check open file descriptors", "lsof | wc -l"),
    ("Show network bandwidth usage", "iftop -t -s 5"),
    ("List all users on the system", "cat /etc/passwd"),
    ("Check last login attempts", "last -10"),
    ("Show disk I/O statistics", "iostat -x 1 3"),
    ("Check swap usage", "swapon --show"),
    ("List loaded kernel modules", "lsmod"),
    ("Show system log errors", "journalctl -p err --since '1 hour ago'"),
    ("Check firewall rules", "sudo iptables -L -n"),
]

for instruction, command in SYSTEM_SAMPLES:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{command}</parameter>
</invoke>
</function_calls>''',
        "category": "system"
    })

# Save all samples
print(f"Generated {len(NEW_SAMPLES)} new samples")
with open('new_samples.jsonl', 'w') as f:
    for s in NEW_SAMPLES:
        f.write(json.dumps(s) + '\n')

# ============================================================================
# MORE PYTHON SCRIPTS
# ============================================================================

MORE_PYTHON = [
    ("Write a Python script to scrape a webpage", '''import requests
from bs4 import BeautifulSoup

def scrape_page(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Get title
    title = soup.find('title').text if soup.find('title') else 'No title'
    
    # Get all links
    links = [a.get('href') for a in soup.find_all('a') if a.get('href')]
    
    # Get all paragraphs
    paragraphs = [p.text for p in soup.find_all('p')]
    
    return {'title': title, 'links': links, 'paragraphs': paragraphs}

result = scrape_page('https://example.com')
print(result)'''),

    ("Write a Python async function to fetch multiple URLs", '''import asyncio
import aiohttp

async def fetch(session, url):
    async with session.get(url) as response:
        return await response.text()

async def fetch_all(urls):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch(session, url) for url in urls]
        results = await asyncio.gather(*tasks)
        return results

urls = [
    'https://example.com',
    'https://example.org',
    'https://example.net'
]

results = asyncio.run(fetch_all(urls))
for i, result in enumerate(results):
    print(f"URL {i+1}: {len(result)} bytes")'''),

    ("Write a Python decorator for timing functions", '''import time
import functools

def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"{func.__name__} took {end - start:.4f} seconds")
        return result
    return wrapper

@timer
def slow_function():
    time.sleep(1)
    return "done"

result = slow_function()'''),

    ("Write a Python decorator for caching function results", '''import functools

def memoize(func):
    cache = {}
    
    @functools.wraps(func)
    def wrapper(*args):
        if args in cache:
            return cache[args]
        result = func(*args)
        cache[args] = result
        return result
    
    return wrapper

@memoize
def fibonacci(n):
    if n < 2:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

print(fibonacci(100))'''),

    ("Write a Python context manager for file handling", '''class FileManager:
    def __init__(self, filename, mode='r'):
        self.filename = filename
        self.mode = mode
        self.file = None
    
    def __enter__(self):
        self.file = open(self.filename, self.mode)
        return self.file
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.file:
            self.file.close()
        return False

# Usage
with FileManager('test.txt', 'w') as f:
    f.write('Hello, World!')'''),

    ("Write a Python generator for reading large files", '''def read_large_file(filename, chunk_size=1024):
    with open(filename, 'r') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            yield chunk

# Usage
for chunk in read_large_file('large_file.txt'):
    print(f"Processing chunk of {len(chunk)} bytes")'''),

    ("Write a Python script for parallel processing", '''import multiprocessing
import time

def process_item(item):
    time.sleep(0.1)
    return item * 2

if __name__ == '__main__':
    items = list(range(100))
    
    with multiprocessing.Pool(processes=4) as pool:
        results = pool.map(process_item, items)
    
    print(f"Processed {len(results)} items")
    print(f"Sample results: {results[:5]}")'''),

    ("Write a Python script to create a simple REST API with Flask", '''from flask import Flask, request, jsonify

app = Flask(__name__)

data = []

@app.route('/items', methods=['GET'])
def get_items():
    return jsonify(data)

@app.route('/items', methods=['POST'])
def add_item():
    item = request.json
    data.append(item)
    return jsonify(item), 201

@app.route('/items/<int:item_id>', methods=['DELETE'])
def delete_item(item_id):
    if 0 <= item_id < len(data):
        removed = data.pop(item_id)
        return jsonify(removed)
    return jsonify({'error': 'Not found'}), 404

if __name__ == '__main__':
    app.run(debug=True)'''),

    ("Write a Python class for a binary search tree", '''class TreeNode:
    def __init__(self, value):
        self.value = value
        self.left = None
        self.right = None

class BinarySearchTree:
    def __init__(self):
        self.root = None
    
    def insert(self, value):
        if not self.root:
            self.root = TreeNode(value)
        else:
            self._insert(self.root, value)
    
    def _insert(self, node, value):
        if value < node.value:
            if node.left:
                self._insert(node.left, value)
            else:
                node.left = TreeNode(value)
        else:
            if node.right:
                self._insert(node.right, value)
            else:
                node.right = TreeNode(value)
    
    def search(self, value):
        return self._search(self.root, value)
    
    def _search(self, node, value):
        if not node or node.value == value:
            return node
        if value < node.value:
            return self._search(node.left, value)
        return self._search(node.right, value)
    
    def inorder(self):
        result = []
        self._inorder(self.root, result)
        return result
    
    def _inorder(self, node, result):
        if node:
            self._inorder(node.left, result)
            result.append(node.value)
            self._inorder(node.right, result)

bst = BinarySearchTree()
for val in [5, 3, 7, 1, 4, 6, 8]:
    bst.insert(val)
print(bst.inorder())'''),

    ("Write a Python script for a simple chat server", '''import socket
import threading

HOST = '0.0.0.0'
PORT = 5000
clients = []

def handle_client(conn, addr):
    print(f"New connection from {addr}")
    clients.append(conn)
    
    while True:
        try:
            message = conn.recv(1024).decode()
            if not message:
                break
            broadcast(f"{addr}: {message}", conn)
        except:
            break
    
    clients.remove(conn)
    conn.close()

def broadcast(message, sender=None):
    for client in clients:
        if client != sender:
            try:
                client.send(message.encode())
            except:
                clients.remove(client)

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind((HOST, PORT))
server.listen()
print(f"Server listening on {HOST}:{PORT}")

while True:
    conn, addr = server.accept()
    thread = threading.Thread(target=handle_client, args=(conn, addr))
    thread.start()'''),

    ("Write a Python script for rate limiting", '''import time
from collections import deque

class RateLimiter:
    def __init__(self, max_requests, time_window):
        self.max_requests = max_requests
        self.time_window = time_window
        self.requests = deque()
    
    def allow_request(self):
        now = time.time()
        
        # Remove old requests
        while self.requests and self.requests[0] < now - self.time_window:
            self.requests.popleft()
        
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        return False

limiter = RateLimiter(max_requests=5, time_window=10)

for i in range(10):
    if limiter.allow_request():
        print(f"Request {i+1}: Allowed")
    else:
        print(f"Request {i+1}: Rate limited")
    time.sleep(1)'''),

    ("Write a Python script for a simple job queue", '''import queue
import threading
import time

class JobQueue:
    def __init__(self, num_workers=4):
        self.queue = queue.Queue()
        self.workers = []
        
        for _ in range(num_workers):
            worker = threading.Thread(target=self._worker)
            worker.daemon = True
            worker.start()
            self.workers.append(worker)
    
    def _worker(self):
        while True:
            job = self.queue.get()
            try:
                job()
            except Exception as e:
                print(f"Job failed: {e}")
            self.queue.task_done()
    
    def submit(self, job):
        self.queue.put(job)
    
    def wait(self):
        self.queue.join()

def sample_job():
    print(f"Processing job on {threading.current_thread().name}")
    time.sleep(0.5)

jq = JobQueue(num_workers=4)

for i in range(10):
    jq.submit(sample_job)

jq.wait()
print("All jobs complete")'''),

    ("Write a Python logging configuration", '''import logging
import sys

def setup_logging(level=logging.INFO, log_file=None):
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    handlers = [logging.StreamHandler(sys.stdout)]
    
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
    
    return logging.getLogger(__name__)

logger = setup_logging(level=logging.DEBUG, log_file='app.log')
logger.info("Application started")
logger.debug("Debug message")
logger.warning("Warning message")
logger.error("Error message")'''),

    ("Write a Python script for configuration management", '''import os
import json
from dataclasses import dataclass
from typing import Optional

@dataclass
class Config:
    database_url: str
    api_key: str
    debug: bool = False
    port: int = 8000
    
    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get('DATABASE_URL', 'sqlite:///db.sqlite'),
            api_key=os.environ['API_KEY'],
            debug=os.environ.get('DEBUG', '').lower() == 'true',
            port=int(os.environ.get('PORT', 8000))
        )
    
    @classmethod
    def from_file(cls, path: str):
        with open(path) as f:
            data = json.load(f)
        return cls(**data)
    
    def to_dict(self):
        return {
            'database_url': self.database_url,
            'api_key': '***',
            'debug': self.debug,
            'port': self.port
        }

config = Config.from_file('config.json')
print(config.to_dict())'''),

    ("Write a Python retry decorator with exponential backoff", '''import time
import functools
import random

def retry(max_attempts=3, base_delay=1, max_delay=60, exponential=True):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    attempts += 1
                    if attempts == max_attempts:
                        raise
                    
                    if exponential:
                        delay = min(base_delay * (2 ** attempts) + random.uniform(0, 1), max_delay)
                    else:
                        delay = base_delay
                    
                    print(f"Attempt {attempts} failed, retrying in {delay:.1f}s...")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry(max_attempts=3, base_delay=1)
def unreliable_function():
    import random
    if random.random() < 0.7:
        raise Exception("Random failure")
    return "Success!"

result = unreliable_function()
print(result)'''),

    ("Write a Python unit test example", '''import unittest

def add(a, b):
    return a + b

def divide(a, b):
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b

class TestMathFunctions(unittest.TestCase):
    def test_add_positive(self):
        self.assertEqual(add(2, 3), 5)
    
    def test_add_negative(self):
        self.assertEqual(add(-1, -1), -2)
    
    def test_divide(self):
        self.assertEqual(divide(10, 2), 5)
    
    def test_divide_by_zero(self):
        with self.assertRaises(ValueError):
            divide(10, 0)

if __name__ == '__main__':
    unittest.main()'''),

    ("Write a Python dataclass example", '''from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime

@dataclass
class User:
    id: int
    name: str
    email: str
    created_at: datetime = field(default_factory=datetime.now)
    tags: List[str] = field(default_factory=list)
    bio: Optional[str] = None
    
    def __post_init__(self):
        self.email = self.email.lower()
    
    @property
    def domain(self):
        return self.email.split('@')[1]

user = User(
    id=1,
    name="Alice",
    email="Alice@Example.com",
    tags=["admin", "developer"]
)

print(user)
print(f"Domain: {user.domain}")'''),

    ("Write a Python LRU cache implementation", '''from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = OrderedDict()
    
    def get(self, key):
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]
    
    def put(self, key, value):
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
    
    def __str__(self):
        return str(dict(self.cache))

cache = LRUCache(3)
cache.put('a', 1)
cache.put('b', 2)
cache.put('c', 3)
print(cache)  # {'a': 1, 'b': 2, 'c': 3}

cache.get('a')  # Access 'a'
cache.put('d', 4)  # Evicts 'b'
print(cache)  # {'c': 3, 'a': 1, 'd': 4}'''),

    ("Write a Python event emitter class", '''class EventEmitter:
    def __init__(self):
        self._events = {}
    
    def on(self, event, callback):
        if event not in self._events:
            self._events[event] = []
        self._events[event].append(callback)
    
    def off(self, event, callback=None):
        if event in self._events:
            if callback:
                self._events[event].remove(callback)
            else:
                del self._events[event]
    
    def emit(self, event, *args, **kwargs):
        if event in self._events:
            for callback in self._events[event]:
                callback(*args, **kwargs)
    
    def once(self, event, callback):
        def wrapper(*args, **kwargs):
            callback(*args, **kwargs)
            self.off(event, wrapper)
        self.on(event, wrapper)

emitter = EventEmitter()

def on_message(msg):
    print(f"Received: {msg}")

emitter.on('message', on_message)
emitter.emit('message', 'Hello!')
emitter.emit('message', 'World!')'''),

    ("Write a Python plugin loader", '''import importlib
import os
import sys

class PluginLoader:
    def __init__(self, plugin_dir):
        self.plugin_dir = plugin_dir
        self.plugins = {}
    
    def discover(self):
        sys.path.insert(0, self.plugin_dir)
        
        for filename in os.listdir(self.plugin_dir):
            if filename.endswith('.py') and not filename.startswith('_'):
                name = filename[:-3]
                self.load(name)
        
        return list(self.plugins.keys())
    
    def load(self, name):
        try:
            module = importlib.import_module(name)
            if hasattr(module, 'Plugin'):
                self.plugins[name] = module.Plugin()
                print(f"Loaded plugin: {name}")
        except Exception as e:
            print(f"Failed to load {name}: {e}")
    
    def run_all(self, method, *args, **kwargs):
        results = {}
        for name, plugin in self.plugins.items():
            if hasattr(plugin, method):
                results[name] = getattr(plugin, method)(*args, **kwargs)
        return results

loader = PluginLoader('./plugins')
loader.discover()'''),
]

for instruction, code in MORE_PYTHON:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.py</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>''',
        "category": "code"
    })

# ============================================================================
# MORE SHELL/BASH SCRIPTS
# ============================================================================

MORE_BASH = [
    ("Write a bash script for parallel command execution", '''#!/bin/bash
MAX_PARALLEL=4
count=0

for item in "$@"; do
    ((count++))
    process_item "$item" &
    
    if (( count % MAX_PARALLEL == 0 )); then
        wait
    fi
done

wait
echo "All tasks complete"'''),

    ("Write a bash script to parse command line arguments", '''#!/bin/bash
usage() {
    echo "Usage: $0 -f <file> [-o <output>] [-v]"
    exit 1
}

VERBOSE=false
OUTPUT="output.txt"

while getopts "f:o:vh" opt; do
    case $opt in
        f) FILE="$OPTARG" ;;
        o) OUTPUT="$OPTARG" ;;
        v) VERBOSE=true ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [ -z "$FILE" ]; then
    usage
fi

$VERBOSE && echo "Processing $FILE -> $OUTPUT"'''),

    ("Write a bash script for health checks", '''#!/bin/bash
SERVICES=("nginx" "mysql" "redis")
FAILED=0

for service in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$service"; then
        echo "✓ $service is running"
    else
        echo "✗ $service is NOT running"
        ((FAILED++))
    fi
done

if [ $FAILED -gt 0 ]; then
    echo "Health check failed: $FAILED services down"
    exit 1
fi

echo "All services healthy"'''),

    ("Write a bash script for log analysis", '''#!/bin/bash
LOG_FILE="${1:-/var/log/syslog}"
TOP_N=10

echo "=== Log Analysis: $LOG_FILE ==="

echo -e "\nTop $TOP_N Error Messages:"
grep -i "error" "$LOG_FILE" | awk '{$1=$2=$3=""; print $0}' | sort | uniq -c | sort -rn | head -$TOP_N

echo -e "\nErrors by Hour:"
grep -i "error" "$LOG_FILE" | awk '{print $3}' | cut -d: -f1 | sort | uniq -c

echo -e "\nTotal Lines: $(wc -l < "$LOG_FILE")"
echo "Error Lines: $(grep -ci "error" "$LOG_FILE")"'''),

    ("Write a bash script for automated testing", '''#!/bin/bash
TESTS_DIR="./tests"
PASSED=0
FAILED=0

for test in "$TESTS_DIR"/*.sh; do
    echo -n "Running $(basename "$test")... "
    
    if bash "$test" > /tmp/test_output.txt 2>&1; then
        echo "PASSED"
        ((PASSED++))
    else
        echo "FAILED"
        ((FAILED++))
        cat /tmp/test_output.txt
    fi
done

echo ""
echo "Results: $PASSED passed, $FAILED failed"

[ $FAILED -eq 0 ] && exit 0 || exit 1'''),

    ("Write a bash script for file encryption", '''#!/bin/bash
FILE="$1"
PASSWORD="$2"

if [ -z "$FILE" ] || [ -z "$PASSWORD" ]; then
    echo "Usage: $0 <file> <password>"
    exit 1
fi

if [ -f "$FILE.enc" ]; then
    # Decrypt
    openssl enc -aes-256-cbc -d -pbkdf2 -in "$FILE.enc" -out "$FILE" -pass pass:"$PASSWORD"
    echo "Decrypted: $FILE"
else
    # Encrypt
    openssl enc -aes-256-cbc -pbkdf2 -in "$FILE" -out "$FILE.enc" -pass pass:"$PASSWORD"
    rm "$FILE"
    echo "Encrypted: $FILE.enc"
fi'''),

    ("Write a bash script for network monitoring", '''#!/bin/bash
HOSTS=("google.com" "github.com" "example.com")
LOG_FILE="/tmp/network_status.log"

echo "$(date): Network Check" >> "$LOG_FILE"

for host in "${HOSTS[@]}"; do
    if ping -c 1 -W 2 "$host" > /dev/null 2>&1; then
        echo "  ✓ $host: UP" >> "$LOG_FILE"
    else
        echo "  ✗ $host: DOWN" >> "$LOG_FILE"
        # Send alert
        echo "$host is DOWN" | mail -s "Network Alert" admin@example.com
    fi
done'''),

    ("Write a bash script for database maintenance", '''#!/bin/bash
DB_NAME="mydb"
BACKUP_DIR="/backups/db"
DAYS_KEEP=7

# Backup
echo "Creating backup..."
pg_dump -Fc "$DB_NAME" > "$BACKUP_DIR/${DB_NAME}_$(date +%Y%m%d).dump"

# Vacuum
echo "Running VACUUM ANALYZE..."
psql -d "$DB_NAME" -c "VACUUM ANALYZE;"

# Reindex
echo "Reindexing..."
psql -d "$DB_NAME" -c "REINDEX DATABASE $DB_NAME;"

# Cleanup old backups
find "$BACKUP_DIR" -name "*.dump" -mtime +$DAYS_KEEP -delete

echo "Maintenance complete"'''),

    ("Write a bash script for container cleanup", '''#!/bin/bash
echo "Docker Cleanup Script"
echo "====================="

# Remove stopped containers
echo "Removing stopped containers..."
docker container prune -f

# Remove unused images
echo "Removing unused images..."
docker image prune -f

# Remove unused volumes
echo "Removing unused volumes..."
docker volume prune -f

# Remove unused networks
echo "Removing unused networks..."
docker network prune -f

# Show disk usage
echo ""
echo "Docker disk usage:"
docker system df'''),

    ("Write a bash script for environment setup", '''#!/bin/bash
set -e

echo "Setting up development environment..."

# Install system dependencies
sudo apt update
sudo apt install -y git curl wget build-essential

# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs

# Install Python
sudo apt install -y python3 python3-pip python3-venv

# Create project structure
mkdir -p ~/projects/{frontend,backend,shared}

# Setup git config
git config --global user.name "Developer"
git config --global user.email "dev@example.com"

echo "Setup complete!"'''),
]

for instruction, code in MORE_BASH:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.sh</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>''',
        "category": "shell"
    })

# ============================================================================
# MORE EXECUTE_SHELL COMMANDS
# ============================================================================

MORE_COMMANDS = [
    # Security
    ("Check for failed SSH login attempts", "grep 'Failed password' /var/log/auth.log | tail -20"),
    ("List users with sudo access", "grep -E '^%sudo|^%admin' /etc/group"),
    ("Find world-writable files", "find / -type f -perm -o+w 2>/dev/null | head -20"),
    ("Check password expiry for user", "chage -l username"),
    ("List open files by process", "lsof -p $(pgrep nginx) | head -20"),
    ("Show failed systemd services", "systemctl --failed"),
    ("Check for rootkits", "rkhunter --check --skip-keypress"),
    ("List installed security updates", "apt list --upgradable | grep -i security"),
    
    # Performance
    ("Show IO wait percentage", "iostat -c 1 3 | tail -1 | awk '{print $4}'"),
    ("Monitor file system events", "inotifywait -m /var/log/"),
    ("Show TCP connection states", "ss -s"),
    ("Check context switches", "vmstat 1 5"),
    ("Show page faults", "sar -B 1 5"),
    ("Monitor network packets", "tcpdump -i eth0 -c 100"),
    
    # Development
    ("Run Python linter", "pylint *.py"),
    ("Format Python code", "black ."),
    ("Run JavaScript linter", "eslint src/"),
    ("Check for outdated npm packages", "npm outdated"),
    ("Run unit tests with coverage", "pytest --cov=."),
    ("Generate requirements.txt", "pip freeze > requirements.txt"),
    ("Check for security vulnerabilities", "pip-audit"),
    ("Run type checker", "mypy src/"),
    
    # Docker
    ("Show container resource usage", "docker stats --no-stream"),
    ("Inspect container network", "docker network inspect bridge"),
    ("Show container processes", "docker top container_name"),
    ("Export container filesystem", "docker export container_name > backup.tar"),
    ("Copy file from container", "docker cp container_name:/path/file ./file"),
    ("Show container changes", "docker diff container_name"),
    
    # Kubernetes
    ("Get all pods", "kubectl get pods -A"),
    ("Describe pod", "kubectl describe pod pod_name"),
    ("Get pod logs", "kubectl logs pod_name --tail=100"),
    ("Execute command in pod", "kubectl exec -it pod_name -- /bin/bash"),
    ("Scale deployment", "kubectl scale deployment myapp --replicas=3"),
    ("Get cluster events", "kubectl get events --sort-by=.metadata.creationTimestamp"),
    
    # Database
    ("Show MySQL process list", "mysql -e 'SHOW PROCESSLIST'"),
    ("Check PostgreSQL connections", "psql -c 'SELECT * FROM pg_stat_activity'"),
    ("Show Redis info", "redis-cli INFO"),
    ("Check MongoDB status", "mongosh --eval 'db.serverStatus()'"),
    
    # Networking advanced
    ("Show ARP cache", "arp -a"),
    ("Test TCP connection", "nc -zv localhost 22"),
    ("Show socket statistics", "ss -tunapl"),
    ("Capture packets to file", "tcpdump -w capture.pcap -c 1000"),
    ("Show network namespaces", "ip netns list"),
    
    # System management
    ("Show system journal", "journalctl -xe"),
    ("List systemd timers", "systemctl list-timers"),
    ("Show boot messages", "dmesg | tail -50"),
    ("Check for broken packages", "apt --fix-broken install"),
    ("Clean package cache", "apt clean && apt autoclean"),
    ("Show disk SMART status", "smartctl -a /dev/sda"),
]

for instruction, command in MORE_COMMANDS:
    NEW_SAMPLES.append({
        "instruction": instruction,
        "output": f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{command}</parameter>
</invoke>
</function_calls>''',
        "category": "system"
    })

# ============================================================================
# MORE NEGATIVE EXAMPLES
# ============================================================================

MORE_CLARIFICATIONS = [
    ("Debug this", "I'd be happy to help debug. What are you trying to debug? Please share the code or error."),
    ("What's wrong?", "I can help figure out what's wrong. Could you describe the issue you're experiencing?"),
    ("Optimize this", "I can help optimize it. What would you like me to optimize? Please share the code or system."),
    ("Can you improve it?", "I'd like to help improve it. What specifically would you like me to improve?"),
    ("Something is slow", "I can help with performance. What's running slowly? Any metrics or logs you can share?"),
    ("Error happened", "I can help troubleshoot. What error message did you see? What were you trying to do?"),
    ("Check my work", "Sure, I'll review it. What would you like me to check?"),
    ("Test this for me", "I can help test it. What would you like me to test and what should I look for?"),
    ("Why isn't it working?", "Let's figure out why. What are you trying to do and what's happening instead?"),
    ("Complete this task", "I'd be happy to help. What task would you like me to complete?"),
    ("Analyze this", "I can analyze it for you. What would you like me to analyze?"),
    ("Build something", "I'd love to help build something. What would you like me to create?"),
    ("Set this up", "I can help with setup. What would you like me to set up?"),
    ("Configure this", "I'll help configure it. What needs to be configured?"),
    ("Deploy this", "I can help with deployment. What would you like me to deploy and where?"),
]

MORE_REFUSALS = [
    ("Crack this password", "I can't help crack passwords. If you've forgotten your own password, I can help with legitimate recovery options."),
    ("Inject SQL into this site", "I can't help with SQL injection attacks. I can help you learn about SQL injection prevention for your own applications."),
    ("Create a phishing page", "I can't help create phishing pages. I can help with legitimate web development."),
    ("Bypass this firewall", "I can't help bypass security controls. If you're having legitimate access issues, I can help troubleshoot."),
    ("Clone someone's voice", "I can't help with voice cloning for impersonation. I can help with legitimate audio processing."),
    ("Generate fake documents", "I can't help create fake documents. I can help with legitimate document creation."),
    ("Scrape personal data", "I can't help scrape personal data without consent. I can help with legitimate web scraping."),
    ("Access private API", "I can't help access APIs without authorization. I can help with APIs you have legitimate access to."),
    ("Disable security logs", "I can't help disable security logging. I can help configure proper log management."),
    ("Install backdoor", "I can't help install backdoors. I can help with legitimate remote access solutions."),
]

MORE_KNOWLEDGE = [
    ("What is Kubernetes?", "Kubernetes is a container orchestration platform that automates deployment, scaling, and management of containerized applications across clusters of machines."),
    ("Explain OAuth", "OAuth is an authorization framework that allows third-party applications to access user data without exposing passwords. It uses tokens to grant limited access."),
    ("What are webhooks?", "Webhooks are HTTP callbacks that send data to a URL when an event occurs. They enable real-time notifications between applications."),
    ("Explain CAP theorem", "CAP theorem states distributed systems can only guarantee two of three properties: Consistency, Availability, and Partition tolerance. You must choose based on your needs."),
    ("What is GraphQL?", "GraphQL is a query language for APIs that lets clients request exactly the data they need. Unlike REST, it uses a single endpoint and typed schema."),
    ("Explain JWT", "JWT (JSON Web Token) is a compact, URL-safe token format for securely transmitting claims between parties. It contains a header, payload, and signature."),
    ("What is CI/CD?", "CI/CD is Continuous Integration and Continuous Deployment/Delivery. CI automates building and testing code changes; CD automates deployment to production."),
    ("Explain microservices", "Microservices architecture breaks applications into small, independent services that communicate via APIs. Each handles one function and can be deployed separately."),
    ("What is Redis?", "Redis is an in-memory data structure store used as a database, cache, and message broker. It supports strings, hashes, lists, sets, and more."),
    ("Explain load balancing", "Load balancing distributes incoming traffic across multiple servers to ensure no single server is overwhelmed. Methods include round-robin, least connections, and IP hash."),
]

for inst, out in MORE_CLARIFICATIONS:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "clarification"})

for inst, out in MORE_REFUSALS:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "refusal"})

for inst, out in MORE_KNOWLEDGE:
    NEW_SAMPLES.append({"instruction": inst, "output": out, "category": "knowledge"})

# Save all new samples
print(f"Generated {len(NEW_SAMPLES)} total new samples")
with open('new_samples.jsonl', 'w') as f:
    for s in NEW_SAMPLES:
        f.write(json.dumps(s) + '\n')
