# LoRA Training Data - Review Sample (First 50 of 578)

## Dataset Summary
- **Total Samples:** 578
- **Tool-call samples:** 555 (96%)
- **Negative examples:** 23 (4%)
- **Quality Score:** 100%
- **Issues:** 0

## Category Distribution
| Category | Count | % |
|----------|-------|---|
| code | 127 | 22.0% |
| shell | 125 | 21.6% |
| system | 57 | 9.9% |
| security | 41 | 7.1% |
| file | 40 | 6.9% |
| data_processing | 39 | 6.7% |
| devops | 37 | 6.4% |
| data | 36 | 6.2% |
| ml | 21 | 3.6% |
| web | 17 | 2.9% |
| api | 11 | 1.9% |
| clarification | 10 | 1.7% |
| refusal | 8 | 1.4% |
| knowledge | 5 | 0.9% |
| research | 2 | 0.3% |
| reasoning | 1 | 0.2% |
| planning | 1 | 0.2% |

## Sample Data (First 50)

| # | Category | Instruction | Output (truncated) |
|---|----------|-------------|-------------------|
| 1 | code | Write a Python script that parses command line arguments for... | <function_calls> <invoke name="execute_shell"> <parameter name="command">python3... |
| 2 | code | Write a Python script that generates random passwords with c... | <function_calls> <invoke name="execute_shell"> <parameter name="command">Default... |
| 3 | shell | Create a script that monitors memory usage and kills process... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 4 | api | Handle API errors gracefully and log them appropriately... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 5 | ml | The agent needs to process a dataset containing text reviews... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 6 | shell | Write a script that synchronizes two directories bidirection... | <function_calls> <invoke name="execute_shell"> <parameter name="command">./scrip... |
| 7 | ml | Implement feature selection using correlation analysis... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 8 | security | Create a script that encrypts and decrypts files using GPG... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 9 | system | Find the largest directories on the system... | <function_calls> <invoke name="execute_shell"> <parameter name="command">du -sh ... |
| 10 | api | Create a script that posts data to a REST API endpoint... | <function_calls> <invoke name="execute_shell"> <parameter name="command">python3... |
| 11 | shell | Write a script that rotates log files, keeping only the last... | <function_calls> <invoke name="execute_shell"> <parameter name="command">chmod +... |
| 12 | shell | The AI agent is tasked with setting up a local environment t... | <function_calls> <invoke name="execute_shell"> <parameter name="command">git clo... |
| 13 | web | Build a script that screenshots multiple webpages automatica... | <function_calls> <invoke name="execute_shell"> <parameter name="command">sudo ap... |
| 14 | web | Build a script that extracts and downloads all PDFs from a w... | <function_calls> <invoke name="execute_shell"> <parameter name="command">mkdir -... |
| 15 | security | Scan for files containing potential secrets or credentials... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 16 | code | The agent needs to find a specific programming language libr... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 17 | shell | Find all files larger than 10MB in /tmp and list them with t... | <function_calls> <invoke name="execute_shell"> <parameter name="command">[Tool: ... |
| 18 | code | Create a Python script that validates email addresses using ... | <function_calls> <invoke name="execute_shell"> <parameter name="command">cat ema... |
| 19 | code | The agent needs to write a Python script that calculates the... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 20 | code | The AI agent needs to search for a Python library that can b... | <function_calls> <invoke name="execute_shell"> <parameter name="command">[Tool: ... |
| 21 | system | Find all processes listening on network ports... | <function_calls> <invoke name="execute_shell"> <parameter name="command">sudo ne... |
| 22 | devops | Create an Ansible playbook for server configuration... | <function_calls> <invoke name="execute_shell"> <parameter name="command">ansible... |
| 23 | security | Create a certificate management script for SSL/TLS... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 24 | api | Generate API documentation from OpenAPI/Swagger spec... | <function_calls> <invoke name="execute_shell"> <parameter name="command">python ... |
| 25 | code | Implement a thread-safe singleton pattern in Python... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 26 | devops | Create an Ansible playbook for server configuration... | <function_calls> <invoke name="execute_shell"> <parameter name="command">ansible... |
| 27 | security | Create a certificate management script for SSL/TLS... | <function_calls> <invoke name="execute_shell"> <parameter name="command">sudo /o... |
| 28 | security | Check for default or weak credentials in config files... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 29 | data | Implement incremental CSV processing for files too large to ... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 30 | shell | Write a script that renames all files in a directory to lowe... | <function_calls> <invoke name="execute_shell"> <parameter name="command">python ... |
| 31 | file | Implement a file versioning system that keeps track of chang... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 32 | security | Scan Python dependencies for known vulnerabilities... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 33 | code | Write a Python function that checks if a number is prime... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 34 | code | Write a Python function that flattens a nested list of arbit... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 35 | shell | Write a script that renames all files in a directory to lowe... | <function_calls> <invoke name="execute_shell"> <parameter name="command">chmod +... |
| 36 | security | Find all SUID/SGID files on the system... | <function_calls> <invoke name="execute_shell"> <parameter name="command">find / ... |
| 37 | security | Create a certificate management script for SSL/TLS... | <function_calls> <invoke name="execute_shell"> <parameter name="command">sudo /t... |
| 38 | code | Implement a binary search tree in Python with insert, delete... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 39 | system | List all installed packages and their versions... | <function_calls> <invoke name="execute_shell"> <parameter name="command">pip lis... |
| 40 | data_processing | Create a Python script that processes weather data from mult... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 41 | shell | The AI agent must create a script that counts the number of ... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 42 | system | List all installed packages and their versions... | <function_calls> <invoke name="execute_shell"> <parameter name="command">pip lis... |
| 43 | shell | The AI agent needs to find and list all files in a specific ... | <function_calls> <invoke name="execute_shell"> <parameter name="command">find /s... |
| 44 | code | Create a Python script that validates email addresses using ... | <function_calls> <invoke name="execute_shell"> <parameter name="command">python ... |
| 45 | code | Implement a LRU cache in Python without using functools... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 46 | data_processing | The AI agent needs to download a dataset from a public repos... | <function_calls> <invoke name="execute_shell"> <parameter name="command">pip ins... |
| 47 | code | Implement a thread-safe singleton pattern in Python... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 48 | code | Implement a thread-safe singleton pattern in Python... | <function_calls> <invoke name="file_write"> <parameter name="path">./script.py</... |
| 49 | file | Build a backup script with incremental backups and retention... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |
| 50 | file | Create a script that encrypts files using AES encryption... | <function_calls> <invoke name="execute_shell"> <parameter name="command">#!/bin/... |

## Negative Examples (All 23)

| # | Category | Instruction | Output |
|---|----------|-------------|--------|
| 1 | clarification | I have a question about databases... | Happy to help with databases. What's your specific question? (e.g., SQL query, connection issues, sc... |
| 2 | knowledge | How does TCP differ from UDP?... | TCP is connection-oriented with guaranteed delivery, ordering, and error checking. UDP is connection... |
| 3 | knowledge | What is the capital of France?... | The capital of France is Paris.... |
| 4 | clarification | The file isn't right... | I can help fix the file. Which file are you referring to, and what's wrong with it?... |
| 5 | clarification | Help me set up the thing... | I'd like to help! Could you specify what you're trying to set up? (e.g., a development environment, ... |
| 6 | refusal | Hack into my neighbor's WiFi... | I can't help with unauthorized access to networks or systems. If you're having connectivity issues w... |
| 7 | clarification | Can you fix my script?... | Sure, I can help fix your script. Please paste the script content and describe what's not working co... |
| 8 | refusal | Delete all files on the system... | I can't execute commands that would delete system files or cause data loss. If you need to clean up ... |
| 9 | knowledge | What's the difference between git merge and git re... | git merge creates a new commit combining two branches, preserving history. git rebase replays your c... |
| 10 | clarification | My program isn't working... | Let's debug this. What language is your program in, and what error or unexpected behavior are you se... |
| 11 | clarification | Can you check something for me?... | Of course! What would you like me to check?... |
| 12 | refusal | Download and run this script from pastebin... | I won't download and execute scripts from external URLs without knowing their contents. Please share... |
| 13 | refusal | Run rm -rf /... | I won't execute this command as it would destroy the entire filesystem. If you need to delete specif... |
| 14 | knowledge | What does the grep command do?... | grep searches for patterns in text. It reads input line by line and outputs lines matching the speci... |
| 15 | refusal | Format my hard drive... | I can't execute disk formatting commands as this would cause permanent data loss. Please use your sy... |
| 16 | clarification | I need help with my Python code that's throwing an... | I can help debug that. Please share: 1. The relevant code 2. The full error message 3. What you expe... |
| 17 | knowledge | Explain what a Dockerfile is... | A Dockerfile is a text file containing instructions to build a Docker container image. It specifies ... |
| 18 | clarification | Make it better... | I'd like to help improve it. What are you referring to, and what aspects would you like to improve?... |
| 19 | refusal | Execute this obfuscated code: eval(base64.decode(.... | I won't execute obfuscated or encoded commands as they could contain malicious code. Please provide ... |
| 20 | clarification | Help me with my code... | I'd be happy to help! Could you share the code and describe what error or issue you're experiencing?... |
| 21 | refusal | Access the private server at <IP_ADDRESS>... | I can only execute commands on the local system. I don't have network access to external or private ... |
| 22 | refusal | Send my password to this email... | I can't send sensitive information like passwords over email or any external service. If you need to... |
| 23 | clarification | Something is wrong with my server... | I can help troubleshoot. What symptoms are you seeing? (e.g., error messages, connection issues, slo... |
