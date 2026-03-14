# SPEC-012 — Live agent stream: `agent_stream.jsonl`

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently `execute_job.py` uses `subprocess.run` (blocking), writes the full
agent output to `stdout.log` only after the process exits. During a long run
the orchestrator has no visibility into what the agent is doing — the run is
opaque until it finishes.

The project already has:
- `events.jsonl` / `event_snapshot.json` in run dirs (orchestrator-level events)
- `openclaw summary` and `dashboard` reading run artifacts
- `_system/engine/event_log.py` with append helpers

What is missing is a **per-line streaming record of agent output** written in
real-time alongside the existing `stdout.log`.

## Goal

Replace `subprocess.run` with `subprocess.Popen` + line-by-line reading so
that each meaningful agent output line is appended to `agent_stream.jsonl` as
it arrives. The existing `stdout.log` behavior must remain intact.

## Desired outcome

1. `runs/<id>/agent_stream.jsonl` is created at run start and grows line-by-line
2. Each record carries `ts`, `type`, `text` (and optionally `seq`)
3. `openclaw summary` prints the last N lines from `agent_stream.jsonl` when available
4. `stdout.log` is still written at the end (backwards-compatible)
5. Tests confirm stream file is created and contains valid JSONL

## Event types

| type | when written |
|------|-------------|
| `message` | regular stdout line from agent |
| `reasoning` | line that starts with `<thinking>` or similar reasoning prefix |
| `command` | line that matches a trusted-command pattern (e.g. `openclaw ...`) |
| `status` | synthetic: written by runner at run_start / run_end |

Classification can be heuristic and kept simple — the important thing is the
stream exists.

## Scope

### In scope
- Switch `execute_job.py` subprocess invocation from `.run` to `.Popen` + readline loop
- Drain stderr on a **background thread** (see Stderr strategy below) to avoid deadlock
- Write `agent_stream.jsonl` inside run dir with append-per-line
- Classify lines into the four types above (simple heuristics, no ML)
- Add `stream_path` to run artifacts dict
- Extend `openclaw summary` JSON response with a `stream_tail` field (last 10
  records as a list) when `agent_stream.jsonl` exists — do not change the text
  output format; `cmd_openclaw_summary` already returns structured JSON
- Tests: stream file created, valid JSONL, contains status events, existing artifacts unbroken

### Stderr strategy
`subprocess.Popen` with stdout/stderr as separate pipes + readline on stdout
risks deadlock if stderr pipe fills up while the main thread is blocked on
stdout. The safe approach:

```python
import threading

stderr_lines: list[str] = []

def _drain_stderr(pipe):
    for line in pipe:
        stderr_lines.append(line)

proc = subprocess.Popen(command, stdout=PIPE, stderr=PIPE, text=True, ...)
t = threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True)
t.start()
# readline loop on proc.stdout here
proc.stdout.close()
t.join()
stderr_text = "".join(stderr_lines)
```

Timeout handling: use `proc.wait(timeout=...)` after closing stdout; on
`TimeoutExpired` call `proc.kill()` and `t.join()` to collect stderr.

### Out of scope
- Web UI / live tail in a browser
- Changing the event_log.py orchestrator event format
- Multi-model routing or model-specific parsers
- stderr filtering / deduplication (stderr goes to stderr.log as before)

## Files to modify / create

### MODIFY: `scripts/execute_job.py`
Replace `subprocess.run` call that invokes the agent with `subprocess.Popen`.
Add `stream_agent_output(proc, stream_path) -> tuple[str, str]` helper that:
1. starts background stderr drain thread
2. reads stdout line by line, classifies, appends JSON record to stream file
3. accumulates stdout text, joins stderr thread
4. returns `(stdout_text, stderr_text)` for the existing artifact-writing code

### CREATE: `scripts/_stream.py`  (or inline in execute_job.py)
Pure functions: `classify_line(line: str) -> str`, `make_stream_record(...)`.
Keeping this separate makes it unit-testable.

### MODIFY: `scripts/claw.py`
`cmd_openclaw_summary`: add `stream_tail` key to the existing JSON payload.
Value: last 10 records from `agent_stream.jsonl` as a list of dicts, or `[]`
when file absent. Do not change any other part of the response shape.

### MODIFY / CREATE: `tests/`
- `test_agent_stream.py`: mock subprocess, verify stream file created, JSONL valid
- `test_stream_classify.py`: unit tests for `classify_line`

## Acceptance Criteria

- After a run, `runs/<id>/agent_stream.jsonl` exists and is valid JSONL
- Each line has at minimum `ts` (ISO-8601), `type` (one of four), `text` (str)
- `stdout.log` is still written and contains the same text as before
- `openclaw summary` prints last 10 stream events without crashing when file absent
- `bash tests/run_all.sh` passes

## Constraints

- Do not break file-backed artifact contract (stdout.log, result.json, meta.json)
- Keep streaming path as a thin wrapper — no buffering beyond one line
- Subprocess timeout handling must still work (carry over from current code)
- Python stdlib only — no external streaming libs
