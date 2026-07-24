#!/usr/bin/env python3
"""
Clean the corrupted training data from excellent_samples.jsonl.

Issues to fix:
1. Remove debug spam (Loading/Running lines)
2. Remove Unicode decorations
3. Remove performance metrics
4. Extract actual content
5. Convert to proper tool-calling format
6. Generalize paths
"""
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Patterns to remove from output
SPAM_PATTERNS = [
    r'Loading qwen3?:?\d*b?\.\.\.\n?',
    r'Running with qwen3?:?\d*b?\.\.\.\n?',
    r'\[FORCE\] Stage .+\n?',
    r'\[FORCE\] Injected \d+ mandatory tool results\n?',
    r'─+\n?',
    r'⏱.+tok/s.+\n?',
    r'🧠 qwen3?:?\d*b?.+\n?',
    r'📊.+tokens.+\n?',
    r'⚡.+tok/s.+\n?',
    r'🚀 fast\n?',
    r'\s+\n',  # Lines with only whitespace
]

# Path patterns to generalize
PATH_REPLACEMENTS = [
    (r'/home/amir/', '$HOME/'),
    (r'/home/user/', '$HOME/'),
    (r'/home/[a-zA-Z0-9_-]+/', '$HOME/'),
    (r'~/(\w)', '$HOME/\\1'),  # ~/something -> $HOME/something
]

def clean_output(output: str) -> Tuple[str, List[str]]:
    """
    Clean the output field, removing spam and extracting actual content.
    Returns (cleaned_output, list_of_issues_found)
    """
    issues = []
    original_len = len(output)
    
    # Remove spam patterns
    cleaned = output
    for pattern in SPAM_PATTERNS:
        matches = len(re.findall(pattern, cleaned))
        if matches > 0:
            issues.append(f"Removed {matches} matches of: {pattern[:30]}")
            cleaned = re.sub(pattern, '', cleaned)
    
    # Remove excessive whitespace
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = cleaned.strip()
    
    # Track compression
    if len(cleaned) < original_len * 0.5:
        issues.append(f"Compressed from {original_len} to {len(cleaned)} chars ({len(cleaned)/original_len*100:.1f}%)")
    
    return cleaned, issues

def generalize_paths(text: str) -> Tuple[str, List[str]]:
    """Replace hardcoded paths with generic versions."""
    issues = []
    result = text
    
    for pattern, replacement in PATH_REPLACEMENTS:
        matches = re.findall(pattern, result)
        if matches:
            issues.append(f"Replaced {len(matches)} paths: {pattern}")
            result = re.sub(pattern, replacement, result)
    
    return result, issues

def extract_tool_call(output: str) -> Optional[str]:
    """
    Try to extract or convert output to tool-calling format.
    Returns None if can't be converted.
    """
    # Already has function_calls?
    if '<function_calls>' in output and '<invoke' in output:
        # Extract just the function_calls block
        match = re.search(r'<function_calls>.*?</function_calls>', output, re.DOTALL)
        if match:
            return match.group(0)
    
    # Look for bash/shell script blocks
    bash_match = re.search(r'```bash\n(.*?)```', output, re.DOTALL)
    if bash_match:
        script = bash_match.group(1).strip()
        # Convert to tool call format
        return f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{script}</parameter>
</invoke>
</function_calls>'''
    
    # Look for simple shell commands
    shell_match = re.search(r'^(ls|cat|grep|find|echo|mkdir|touch|rm|cp|mv|cd|pwd|df|du|wc|head|tail|sort|uniq|awk|sed)\s+.+$', output, re.MULTILINE)
    if shell_match:
        cmd = shell_match.group(0).strip()
        return f'''<function_calls>
<invoke name="execute_shell">
<parameter name="command">{cmd}</parameter>
</invoke>
</function_calls>'''
    
    # Look for Python code blocks
    python_match = re.search(r'```python\n(.*?)```', output, re.DOTALL)
    if python_match:
        code = python_match.group(1).strip()
        # Save to file and run
        return f'''<function_calls>
<invoke name="file_write">
<parameter name="path">./script.py</parameter>
<parameter name="content">{code}</parameter>
</invoke>
</function_calls>

<function_calls>
<invoke name="execute_shell">
<parameter name="command">python3 ./script.py</parameter>
</invoke>
</function_calls>'''
    
    return None

def process_sample(sample: Dict) -> Tuple[Optional[Dict], Dict]:
    """
    Process a single sample, returning (cleaned_sample, report).
    Returns None for first element if sample should be discarded.
    """
    report = {
        'instruction': sample.get('instruction', '')[:50],
        'category': sample.get('category', 'unknown'),
        'original_score': sample.get('score', 0),
        'issues': [],
        'action': 'kept',
    }
    
    output = sample.get('output', '')
    
    # Step 1: Clean spam
    cleaned, issues = clean_output(output)
    report['issues'].extend(issues)
    
    # Step 2: Generalize paths
    cleaned, path_issues = generalize_paths(cleaned)
    report['issues'].extend(path_issues)
    
    # Step 3: Try to extract/convert to tool call format
    tool_call = extract_tool_call(cleaned)
    
    if tool_call:
        # Generalize paths in tool call too
        tool_call, _ = generalize_paths(tool_call)
        report['action'] = 'converted_to_tool_call'
        
        return {
            'instruction': sample['instruction'],
            'input': sample.get('input', ''),
            'output': tool_call,
            'category': sample.get('category', 'unknown'),
            'original_score': sample.get('score', 0),
        }, report
    else:
        # Keep cleaned version but flag for manual review
        report['action'] = 'needs_manual_review'
        report['issues'].append('Could not extract tool call format')
        
        return {
            'instruction': sample['instruction'],
            'input': sample.get('input', ''),
            'output': cleaned,
            'category': sample.get('category', 'unknown'),
            'original_score': sample.get('score', 0),
            'needs_review': True,
        }, report

def main():
    if len(sys.argv) < 2:
        print("Usage: python clean_training_data.py <input.jsonl> [output.jsonl]")
        sys.exit(1)
    
    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path.with_suffix('.cleaned.jsonl')
    report_path = output_path.with_suffix('.report.json')
    
    samples = []
    with open(input_path) as f:
        for line in f:
            if line.strip():
                samples.append(json.loads(line))
    
    print(f"Loaded {len(samples)} samples from {input_path}")
    
    cleaned_samples = []
    reports = []
    
    for i, sample in enumerate(samples):
        cleaned, report = process_sample(sample)
        reports.append(report)
        if cleaned:
            cleaned_samples.append(cleaned)
        
        if (i + 1) % 50 == 0:
            print(f"Processed {i+1}/{len(samples)}")
    
    # Write cleaned samples
    with open(output_path, 'w') as f:
        for sample in cleaned_samples:
            f.write(json.dumps(sample) + '\n')
    
    print(f"Wrote {len(cleaned_samples)} cleaned samples to {output_path}")
    
    # Write report
    summary = {
        'total_input': len(samples),
        'total_output': len(cleaned_samples),
        'converted_to_tool_call': sum(1 for r in reports if r['action'] == 'converted_to_tool_call'),
        'needs_manual_review': sum(1 for r in reports if r['action'] == 'needs_manual_review'),
        'discarded': sum(1 for r in reports if r['action'] == 'discarded'),
        'samples': reports,
    }
    
    with open(report_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"Wrote report to {report_path}")
    print(f"\nSummary:")
    print(f"  Converted to tool calls: {summary['converted_to_tool_call']}")
    print(f"  Needs manual review: {summary['needs_manual_review']}")
    print(f"  Discarded: {summary['discarded']}")

if __name__ == '__main__':
    main()
