# Security policy

## Supported versions

| version | supported |
|---|---|
| the latest minor release | yes, security fixes are published here |
| any earlier release | no, upgrade to the latest minor |
| pre-release builds from `main` | no |

No release yet; `main` is pre-release.

## Reporting a vulnerability

Use GitHub private vulnerability reporting on this repository: the **Security**
tab, then the **Report a vulnerability** button. That opens a private advisory
only the maintainer can see.

Do not open a public issue, a discussion, or a pull request for a security
problem. A public report is a disclosure.

If private reporting is unavailable to you, send a direct message to
[@aarontimko](https://github.com/aarontimko) on GitHub asking for a private
channel, and say nothing about the issue itself in that message.

## What to include

- What the problem is, in one or two sentences.
- The commit you are on: `git rev-parse --short HEAD`. rondo has no version
  command.
- Your macOS version, your Reaper version, and your Python version.
- Steps to reproduce, or a proof of concept.
- What an attacker gets out of it, and what they need first.
- Anything you already know about a fix.

## Response

- Acknowledgement within 7 days.
- For a confirmed report, a fix or a decision not to fix within 30 days of the
  acknowledgement.
- You are credited in the advisory unless you ask not to be.

## Threat model

What rondo touches, so you can judge whether something is in scope. There is no
daemon and no installed binary: every script is a short-lived process you start
yourself.

- **Reads** the project that is open in your running Reaper, through Reaper
  itself, plus the files you point a script at: grid text, JSON note files, the
  drum manifest `samples/kit.json`, and the wav you hand `hum_to_grid.py`. It
  reads Reaper's own resource directory for plugin and preset information.
- **Writes** MIDI items, tracks, instruments, FX and automation into the open
  Reaper project, track and region names, and Reaper project files when you ask
  for a save. It writes generated ReaScript and its output to a scratch
  directory under the system temp directory, converted Surge presets into that
  same scratch directory, and wav renders only to the `--out` path you give. It never
  writes audio into the repository.
- **Talks to** the locally running Reaper, and nothing else. There is no network
  socket and no port. The transports are a file mailbox in
  `~/Library/Application Support/REAPER/Scripts/mcp_bridge_data`, where a
  request JSON file is written and a response file is read back, and
  `open -a REAPER <file>.lua`, which runs a generated ReaScript inside the
  running instance. Both are local file paths on your own machine, so anyone who
  can write to that directory can make Reaper run code; that is the same trust
  boundary as your user account.
- **Reaches the network** in exactly one script: `scripts/install_samples.py`
  downloads the CC0 drum one-shots named in `samples/kit.json` over HTTPS from
  `raw.githubusercontent.com`, at a commit pinned in that manifest, and checks
  each file's size and SHA-256 against that manifest, reporting the mismatch and
  exiting nonzero if either disagrees. Nothing else in rondo makes a
  network call. Do not run that script and there is no network call at all.
  `hum_to_grid.py`'s optional dependencies are installed by you, not by rondo.

Out of scope: anything that needs an attacker to already have arbitrary code
execution as your user; Reaper itself, the bridge ReaScript, which rondo does
not ship, and the synth plugins rondo loads; and the content of the project
files and samples you choose to open.
