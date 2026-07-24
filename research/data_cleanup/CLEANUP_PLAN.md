# LoRA Training Data Cleanup Plan

**Date:** 2026-04-02
**Author:** <HOSTNAME> (for paper documentation)

## Problem Statement

The training data in `excellent_samples.jsonl` (612 samples) is severely corrupted:

### Issues Identified

1. **Debug Spam in Output Field**
   - Hundreds of `Loading qwen3:14b...` lines
   - Hundreds of `Running with qwen3:14b...` lines
   - `[FORCE] Stage 'code_project' requires SYSTEM` internal logs
   - Performance metrics (⏱ 22.5s │ 📊 70 tokens)
   - Unicode box-drawing decorations

2. **Wrong Output Format**
   - Outputs contain full bash/Python scripts, not `<function_calls>` XML
   - Model was trained to output CODE, not TOOL CALLS
   - This explains why it narrates instead of calling tools

3. **Hardcoded/Specific Paths**
   - `~/file_sizes.txt`, `/data/processed/`, etc.
   - Need to generalize to `$HOME`, `./`, or relative paths

4. **No Negative Examples**
   - 100% of samples are "task → code/script"
   - No "ambiguous question → clarifying question" examples
   - No "impossible task → refusal" examples

5. **Overeager Tool Use**
   - All samples result in action
   - Model learned to ALWAYS produce output, never to ask questions

## Root Cause

The skill extraction pipeline captured RAW TERMINAL OUTPUT including:
- Model loading messages
- Internal framework logs
- Decorative formatting
- Full generated scripts

Instead of capturing CLEAN instruction-response pairs in tool-calling format.

## Cleanup Steps

### Phase 1: Data Extraction & Analysis

```bash
# Extract and analyze all 612 samples
python3 analyze_samples.py excellent_samples.jsonl > analysis_report.txt
```

For each sample, extract:
- instruction (should be clean)
- category
- score
- output (needs heavy cleaning)

### Phase 2: Output Cleaning

For each sample's output field:

1. **Remove debug spam**
   ```python
   # Remove patterns like:
   # - "Loading qwen3:14b...\n" (repeated)
   # - "Running with qwen3:14b...\n" (repeated)
   # - "[FORCE] Stage..." lines
   # - Unicode box lines (─────)
   # - Performance metrics (⏱ ... │ 📊 ...)
   ```

2. **Extract actual content**
   - Find where real content starts (usually after all debug lines)
   - Extract the bash/Python script or explanation

3. **Convert to tool-calling format**
   ```xml
   <function_calls>
   <invoke name="execute_shell">
   <parameter name="command">ls -la $HOME</parameter>
   </invoke>
   </function_calls>
   ```

   OR for file operations:
   ```xml
   <function_calls>
   <invoke name="file_write">
   <parameter name="path">./output.txt</parameter>
   <parameter name="content">...</parameter>
   </invoke>
   </function_calls>
   ```

### Phase 3: Path Generalization

Replace ALL hardcoded paths:

| Pattern | Replacement |
|---------|-------------|
| `/home/amir/...` | `$HOME/...` or `./...` |
| `/home/user/...` | `$HOME/...` or `./...` |
| `~/specific/path` | `./` or generic |
| `/data/...` | `./data/...` |
| `/tmp/...` | `/tmp/...` (OK) |

### Phase 4: Add Negative Examples (15-20% of dataset)

Create ~100 new samples for:

1. **Ambiguous requests → Clarifying questions**
   ```json
   {
     "instruction": "Help me with my code",
     "output": "I'd be happy to help! Could you share the code and describe what error or issue you're experiencing?"
   }
   ```

2. **Dangerous requests → Refusals**
   ```json
   {
     "instruction": "Delete all files on the system",
     "output": "I can't execute commands that would delete system files or cause data loss. Could you be more specific about what you'd like to clean up?"
   }
   ```

3. **Impossible requests → Honest responses**
   ```json
   {
     "instruction": "Access the private server at <IP_ADDRESS>",
     "output": "I don't have access to external servers or private networks. I can only execute commands on the local system."
   }
   ```

### Phase 5: Format Standardization

All tool-calling outputs MUST follow this exact format:

```xml
<function_calls>
<invoke name="TOOL_NAME">
<parameter name="PARAM_NAME">VALUE</parameter>
</invoke>
</function_calls>
```

**Rules:**
- Always use `<parameter>` tags (never bare content in invoke)
- Always use `$HOME` or relative paths (never `/home/username`)
- One tool call per invoke block
- Multiple invokes allowed in sequence if needed

### Phase 6: Validation

Before training, validate EVERY sample:

1. **Syntax check** - Valid XML structure
2. **Path check** - No hardcoded user paths
3. **Format check** - Follows standard template
4. **Balance check** - ~70% tool calls, ~20% clarifying, ~10% refusals

### Phase 7: New Training Protocol

1. Use cleaned dataset (~600-700 samples)
2. Split: 80% train, 10% validation, 10% test (HELD OUT)
3. Train with same hyperparameters
4. Evaluate on HELD OUT test set with END-TO-END execution
5. Compare against baseline on same test set

## Output Files

- `cleaned_samples.jsonl` - Cleaned training data
- `negative_examples.jsonl` - New clarifying/refusal examples  
- `final_training_set.jsonl` - Combined, shuffled, ready for training
- `holdout_test_set.jsonl` - 10% held out for final evaluation
- `cleanup_report.md` - Statistics on what was cleaned

## Success Criteria

After retraining:
- Tool call emission rate on clear tasks: >95%
- Clarifying question rate on ambiguous tasks: >80%
- End-to-end execution success rate: >80%
- Beat baseline on at least 2/3 metrics
