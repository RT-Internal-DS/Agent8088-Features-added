#!/usr/bin/env python3
"""
Complete training data cleanup pipeline.
1. Clean spam from outputs
2. Convert to tool-call format
3. Generalize all paths
4. Add negative examples
5. Validate everything
6. Produce review table
"""
import json
import re
import random
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from html import escape

# ============ SPAM PATTERNS ============
SPAM_PATTERNS = [
    r'Loading qwen\d*:?\d*b?\.\.\.(\n|\s)*',
    r'Running with qwen\d*:?\d*b?\.\.\.(\n|\s)*',
    r'\[FORCE\] Stage[^\n]+\n?',
    r'\[FORCE\] Injected[^\n]+\n?',
    r'─+\n?',
    r'⏱[^\n]+\n?',
    r'│[^\n]+\n?',
    r'🧠[^\n]+\n?',
    r'📊[^\n]+\n?',
    r'⚡[^\n]+\n?',
    r'🚀[^\n]+\n?',
    r'\s{50,}',  # Long whitespace
]

# ============ PATH PATTERNS ============
PATH_PATTERNS = [
    (r'/home/amir/', './'),
    (r'/home/user/', './'),
    (r'/home/\w+/', './'),
    (r'~/(?=[a-zA-Z])', './'),
    (r'\$HOME/', './'),
    (r'/data/processed/', './data/'),
    (r'/path/to/\w+', '.'),
    (r'/Users/\w+/', './'),
]

# ============ NEGATIVE EXAMPLES ============
NEGATIVE_EXAMPLES = [
    # Ambiguous requests → Clarifying questions
    {
        "instruction": "Help me with my code",
        "output": "I'd be happy to help! Could you share the code and describe what error or issue you're experiencing?",
        "category": "clarification"
    },
    {
        "instruction": "I need help with my Python code that's throwing an error",
        "output": "I can help debug that. Please share:\n1. The relevant code\n2. The full error message\n3. What you expected to happen",
        "category": "clarification"
    },
    {
        "instruction": "Can you fix my script?",
        "output": "Sure, I can help fix your script. Please paste the script content and describe what's not working correctly.",
        "category": "clarification"
    },
    {
        "instruction": "Something is wrong with my server",
        "output": "I can help troubleshoot. What symptoms are you seeing? (e.g., error messages, connection issues, slow performance)",
        "category": "clarification"
    },
    {
        "instruction": "My program isn't working",
        "output": "Let's debug this. What language is your program in, and what error or unexpected behavior are you seeing?",
        "category": "clarification"
    },
    {
        "instruction": "Help me set up the thing",
        "output": "I'd like to help! Could you specify what you're trying to set up? (e.g., a development environment, a server, a tool)",
        "category": "clarification"
    },
    {
        "instruction": "Can you check something for me?",
        "output": "Of course! What would you like me to check?",
        "category": "clarification"
    },
    {
        "instruction": "I have a question about databases",
        "output": "Happy to help with databases. What's your specific question? (e.g., SQL query, connection issues, schema design)",
        "category": "clarification"
    },
    {
        "instruction": "Make it better",
        "output": "I'd like to help improve it. What are you referring to, and what aspects would you like to improve?",
        "category": "clarification"
    },
    {
        "instruction": "The file isn't right",
        "output": "I can help fix the file. Which file are you referring to, and what's wrong with it?",
        "category": "clarification"
    },
    # Dangerous/impossible → Refusals
    {
        "instruction": "Delete all files on the system",
        "output": "I can't execute commands that would delete system files or cause data loss. If you need to clean up specific files, please specify the exact path and what you want to remove.",
        "category": "refusal"
    },
    {
        "instruction": "Run rm -rf /",
        "output": "I won't execute this command as it would destroy the entire filesystem. If you need to delete specific files, please provide a safe, targeted command.",
        "category": "refusal"
    },
    {
        "instruction": "Format my hard drive",
        "output": "I can't execute disk formatting commands as this would cause permanent data loss. Please use your system's disk management tools directly if this is truly needed.",
        "category": "refusal"
    },
    {
        "instruction": "Access the private server at <IP_ADDRESS>",
        "output": "I can only execute commands on the local system. I don't have network access to external or private servers.",
        "category": "refusal"
    },
    {
        "instruction": "Hack into my neighbor's WiFi",
        "output": "I can't help with unauthorized access to networks or systems. If you're having connectivity issues with your own network, I can help troubleshoot that.",
        "category": "refusal"
    },
    {
        "instruction": "Send my password to this email",
        "output": "I can't send sensitive information like passwords over email or any external service. If you need to manage credentials, I recommend using a password manager.",
        "category": "refusal"
    },
    {
        "instruction": "Execute this obfuscated code: eval(base64.decode(...))",
        "output": "I won't execute obfuscated or encoded commands as they could contain malicious code. Please provide clear, readable commands instead.",
        "category": "refusal"
    },
    {
        "instruction": "Download and run this script from pastebin",
        "output": "I won't download and execute scripts from external URLs without knowing their contents. Please share the script directly so I can review it first.",
        "category": "refusal"
    },
    # Knowledge questions → Direct answers (no tools needed)
    {
        "instruction": "What is the capital of France?",
        "output": "The capital of France is Paris.",
        "category": "knowledge"
    },
    {
        "instruction": "Explain what a Dockerfile is",
        "output": "A Dockerfile is a text file containing instructions to build a Docker container image. It specifies the base image, dependencies, files to copy, and commands to run when building the image.",
        "category": "knowledge"
    },
    {
        "instruction": "What does the grep command do?",
        "output": "grep searches for patterns in text. It reads input line by line and outputs lines matching the specified pattern. Common usage: `grep 'pattern' filename` or `command | grep 'pattern'`.",
        "category": "knowledge"
    },
    {
        "instruction": "What's the difference between git merge and git rebase?",
        "output": "git merge creates a new commit combining two branches, preserving history. git rebase replays your commits on top of another branch, creating a linear history. Merge is safer for shared branches; rebase is cleaner for local work.",
        "category": "knowledge"
    },
    {
        "instruction": "How does TCP differ from UDP?",
        "output": "TCP is connection-oriented with guaranteed delivery, ordering, and error checking. UDP is connectionless, faster, but doesn't guarantee delivery or ordering. TCP is used for web/email; UDP for streaming/gaming.",
        "category": "knowledge"
    },
]

def clean_output(output: str) -> str:
    """Remove all spam patterns from output."""
    cleaned = output
    for pattern in SPAM_PATTERNS:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    # Collapse multiple newlines
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()

def generalize_paths(text: str) -> str:
    """Replace all specific paths with generic ones."""
    result = text
    for pattern, replacement in PATH_PATTERNS:
        result = re.sub(pattern, replacement, result)
    return result

def extract_code_block(text: str, lang: str) -> Optional[str]:
    """Extract code block of given language."""
    pattern = rf'```{lang}\n?(.*?)```'
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None

def convert_to_tool_call(output: str) -> Optional[str]:
    """Convert output to tool-calling format."""
    # Already has function_calls?
    if '<function_calls>' in output and '<invoke' in output:
        match = re.search(r'<function_calls>.*?</function_calls>', output, re.DOTALL)
        if match:
            return match.group(0)
    
    # Extract bash script
    bash = extract_code_block(output, 'bash')
    if bash:
        # Escape for XML
        bash_escaped = bash.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{bash_escaped}</parameter>
</invoke>
</function_calls>'''
    
    # Extract shell/sh script
    shell = extract_code_block(output, 'sh')
    if shell:
        shell_escaped = shell.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{shell_escaped}</parameter>
</invoke>
</function_calls>'''
    
    # Extract Python code
    python = extract_code_block(output, 'python')
    if python:
        python_escaped = python.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.py</parameter>
<parameter name="content">{python_escaped}</parameter>
</invoke>
</function_calls>'''
    
    # Look for inline shell command
    cmd_match = re.search(r'^(ls|cat|grep|find|echo|mkdir|touch|rm|cp|mv|cd|pwd|df|du|wc|head|tail|sort|uniq|awk|sed|chmod|chown|curl|wget|tar|zip|unzip|git|docker|npm|pip|python3?)\s+.+$', output, re.MULTILINE)
    if cmd_match:
        cmd = cmd_match.group(0).strip()
        cmd_escaped = cmd.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        return f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{cmd_escaped}</parameter>
</invoke>
</function_calls>'''
    
    return None

def validate_sample(sample: Dict) -> List[str]:
    """Validate a sample, return list of issues."""
    issues = []
    output = sample.get('output', '')
    
    # Check for remaining spam
    if 'Loading qwen' in output or 'Running with qwen' in output:
        issues.append("Contains loading spam")
    if '─────' in output:
        issues.append("Contains unicode decorations")
    
    # Check for specific paths
    if '/home/amir' in output:
        issues.append("Contains /home/amir path")
    if '/home/user' in output:
        issues.append("Contains /home/user path")
    if re.search(r'/home/\w+/', output):
        issues.append("Contains /home/<user>/ path")
    
    # Check format for tool-call samples
    if sample.get('category') not in ['clarification', 'refusal', 'knowledge']:
        if '<function_calls>' not in output:
            issues.append("Missing <function_calls> tag")
        if '<invoke' not in output:
            issues.append("Missing <invoke> tag")
        if '<parameter' not in output:
            issues.append("Missing <parameter> tag")
    
    return issues

def process_sample(sample: Dict) -> Tuple[Optional[Dict], str]:
    """Process a single sample. Returns (cleaned_sample, status)."""
    instruction = sample.get('instruction', '')
    output = sample.get('output', '')
    category = sample.get('category', 'unknown')
    
    # Step 1: Clean spam
    cleaned = clean_output(output)
    
    # Step 2: Generalize paths
    cleaned = generalize_paths(cleaned)
    
    # Step 3: Convert to tool-call format
    tool_call = convert_to_tool_call(cleaned)
    
    if tool_call:
        tool_call = generalize_paths(tool_call)
        return {
            'instruction': instruction,
            'output': tool_call,
            'category': category,
        }, 'converted'
    
    # If we can't convert, check if it's short enough to keep as-is
    if len(cleaned) < 500 and len(cleaned) > 10:
        return {
            'instruction': instruction,
            'output': cleaned,
            'category': category,
            'needs_review': True,
        }, 'kept_for_review'
    
    return None, 'discarded'

def main():
    input_file = Path('excellent_samples.jsonl')
    output_file = Path('final_training_data.jsonl')
    review_file = Path('review_table.html')
    stats_file = Path('cleanup_stats.json')
    
    # Load original samples
    samples = []
    with open(input_file) as f:
        for line in f:
            if line.strip():
                samples.append(json.loads(line))
    
    print(f"Loaded {len(samples)} original samples")
    
    # Process all samples
    cleaned_samples = []
    stats = {'converted': 0, 'kept_for_review': 0, 'discarded': 0}
    
    for sample in samples:
        result, status = process_sample(sample)
        stats[status] += 1
        if result:
            cleaned_samples.append(result)
    
    print(f"Processed: {stats}")
    
    # Add negative examples
    for neg in NEGATIVE_EXAMPLES:
        cleaned_samples.append(neg)
    
    print(f"Added {len(NEGATIVE_EXAMPLES)} negative examples")
    
    # Validate all samples
    validation_issues = []
    for i, sample in enumerate(cleaned_samples):
        issues = validate_sample(sample)
        if issues:
            validation_issues.append((i, sample['instruction'][:50], issues))
    
    print(f"Validation issues found: {len(validation_issues)}")
    
    # Fix validation issues
    fixed_samples = []
    for sample in cleaned_samples:
        output = sample['output']
        # Re-clean paths
        output = generalize_paths(output)
        # Remove any remaining spam
        output = clean_output(output)
        
        fixed_sample = {**sample, 'output': output}
        
        # Skip if still has issues after cleaning
        remaining_issues = validate_sample(fixed_sample)
        critical_issues = [i for i in remaining_issues if 'Contains' in i]
        
        if not critical_issues:
            fixed_samples.append(fixed_sample)
    
    print(f"Samples after validation: {len(fixed_samples)}")
    
    # Shuffle
    random.seed(42)
    random.shuffle(fixed_samples)
    
    # Write final dataset
    with open(output_file, 'w') as f:
        for sample in fixed_samples:
            # Remove needs_review flag for final output
            clean_sample = {k: v for k, v in sample.items() if k != 'needs_review'}
            f.write(json.dumps(clean_sample) + '\n')
    
    print(f"Wrote {len(fixed_samples)} samples to {output_file}")
    
    # Generate HTML review table
    html = '''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Training Data Review</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 20px; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 12px; text-align: left; vertical-align: top; }
th { background: #4a90d9; color: white; position: sticky; top: 0; }
tr:nth-child(even) { background: #f9f9f9; }
tr:hover { background: #e8f4ff; }
.instruction { max-width: 400px; }
.output { max-width: 600px; font-family: monospace; font-size: 12px; white-space: pre-wrap; word-wrap: break-word; background: #f5f5f5; padding: 8px; border-radius: 4px; max-height: 300px; overflow-y: auto; }
.category { font-weight: bold; }
.category-shell { color: #2e7d32; }
.category-code { color: #1565c0; }
.category-clarification { color: #f57c00; }
.category-refusal { color: #c62828; }
.category-knowledge { color: #6a1b9a; }
.stats { background: #e3f2fd; padding: 15px; border-radius: 8px; margin-bottom: 20px; }
h1 { color: #333; }
.count { font-size: 24px; font-weight: bold; color: #1565c0; }
</style>
</head>
<body>
<h1>LoRA Training Data Review</h1>
<div class="stats">
<p><span class="count">''' + str(len(fixed_samples)) + '''</span> total samples</p>
<p>Categories: '''
    
    # Count categories
    cat_counts = {}
    for s in fixed_samples:
        cat = s.get('category', 'unknown')
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    
    html += ', '.join(f'{k}: {v}' for k, v in sorted(cat_counts.items()))
    html += '''</p>
</div>
<table>
<tr><th>#</th><th>Category</th><th class="instruction">Instruction</th><th class="output">Output</th></tr>
'''
    
    for i, sample in enumerate(fixed_samples):
        cat = sample.get('category', 'unknown')
        cat_class = f'category-{cat}' if cat in ['shell', 'code', 'clarification', 'refusal', 'knowledge'] else ''
        instruction = escape(sample['instruction'])
        output = escape(sample['output'])
        
        html += f'''<tr>
<td>{i+1}</td>
<td class="category {cat_class}">{cat}</td>
<td class="instruction">{instruction}</td>
<td><div class="output">{output}</div></td>
</tr>
'''
    
    html += '''</table>
</body>
</html>'''
    
    with open(review_file, 'w') as f:
        f.write(html)
    
    print(f"Wrote review table to {review_file}")
    
    # Write stats
    final_stats = {
        'original_samples': len(samples),
        'converted': stats['converted'],
        'kept_for_review': stats['kept_for_review'],
        'discarded': stats['discarded'],
        'negative_examples_added': len(NEGATIVE_EXAMPLES),
        'final_samples': len(fixed_samples),
        'categories': cat_counts,
    }
    
    with open(stats_file, 'w') as f:
        json.dump(final_stats, f, indent=2)
    
    print(f"\nFinal stats: {json.dumps(final_stats, indent=2)}")

if __name__ == '__main__':
    main()
