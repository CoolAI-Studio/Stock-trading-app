---
name: memory-discipline
description: Keep this repo's test and build runs inside the machine's memory budget. Use before running the full backend or frontend suite, before launching background agents or workflows, when a run dies with "Windows fatal exception - stack overflow", "[vitest-pool] Failed to start/terminate threads worker", an OOM, or when the machine starts thrashing. Also use when cleaning up after an interrupted run.
---

# Memory discipline

This machine has ~24 GB with typically **only 6-8 GB actually free** (VS Code, browsers,
Defender and the agent itself hold the rest). The suites here are big enough that the last
few GB decide whether a run finishes or dies. Everything below was measured on this
machine, not assumed.

## The failures this prevents, and how to recognise them

Memory pressure here does **not** announce itself as "out of memory". It shows up as:

| Symptom | What it really is |
| --- | --- |
| `Windows fatal exception: stack overflow` partway through pytest | memory pressure, not recursion |
| `[vitest-pool]: Failed to start threads worker` / `Failed to terminate threads worker` | the pool losing workers |
| A vitest run reporting only 3 of 17 files, with "Errors 14" | same thing; **not** a result |
| One test "timed out in 5000ms" that passes alone | same thing |

**Re-run before diagnosing.** Treating one of these as a code bug wastes far more time than
the re-run costs. A *real* failure is an assertion with an expected-vs-got diff, and it
reproduces when that file is run on its own.

## Before a heavy run

```bash
# free RAM, and who is holding it
powershell -Command "$os=Get-CimInstance Win32_OperatingSystem; 'free: {0:N1} GB' -f ($os.FreePhysicalMemory/1MB)"
```

Under ~6 GB free, clean up before starting rather than watching it die at 60%.

## Run suites the cheap way

- **Frontend: always `npx vitest run --maxWorkers=1`.** The default pool spawns a worker
  per core and tears them down under pressure. Single-threaded takes ~110s instead of ~35s
  and actually completes; the fast path frequently does not.
- **Never run the two suites at once**, and never run either while a background agent or
  workflow is also running tests. They compound.
- **While iterating, run one file**, not the suite: `npx vitest run src/pages/X.test.tsx --maxWorkers=1`
  or `python -m pytest tests/test_x.py -q`. Save the full suite for the gate before commit.

## Release what is finished

Interrupted runs leave orphans that hold memory and SQLite locks for hours. After any
crashed or cancelled run, check:

```bash
powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | ForEach-Object { '{0} {1}' -f $_.ProcessId, $_.CommandLine.Substring(0,[Math]::Min(100,$_.CommandLine.Length)) }"
```

**Only ever kill processes whose command line points at THIS repo.** This machine runs
other projects whose pytest processes look identical in a bare process
list. Killing one of those destroys someone else's in-flight work. Filter on the path:

```bash
powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*Stock trading app*' -and $_.CommandLine -like '*pytest*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
```

Also release, as a habit rather than a rescue measure — the point is to stop something
hogging memory *before* the next thing needs it, not to scramble once a run has already
died:

- **Docker Desktop** — holds ~700 MB across its own processes plus a WSL VM even while
  completely idle, and nothing in this project needs it running except building the deploy
  image. Stop it when it is not in use.

  **Stop, never uninstall.** Closing Docker Desktop and running `wsl --shutdown` frees the
  memory and changes nothing else: the install, the images, the volumes and the settings
  all survive, and the user gets it back by opening Docker Desktop from the Start menu.
  Say "停用" when reporting this, not "刪除" — they are very different things to hear about
  your own machine.

  ```bash
  powershell -Command "Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }"
  wsl.exe --shutdown
  ```

- **Local dev servers** — stop the uvicorn/vite processes you started once the check that
  needed them is done. Do not leave one running across a whole session on the chance it
  might be wanted again; starting it back up costs seconds.
- **Playwright browsers and profiles** — a persistent-context profile keeps a full browser
  alive; close the context in the script's `finally`.

## When starting background work

Agents and workflows each carry their own memory. Before launching several, account for
what is already running, and prefer sequential phases over parallel ones on this machine —
the parallel version is what turns a slow run into a crashed one. If a workflow is running,
do not also run a suite yourself.
