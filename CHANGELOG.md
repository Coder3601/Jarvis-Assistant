# Changelog

## 1.0.0 — first public release

### Agents
- **JARVIS** – general assistant with an animated amber holographic core (OpenGL, bloom, five gyroscope rings, reactor, particle sphere).
- **A.T.O.M** – blue work & revision agent: calendar, to-do list, explanations at five levels, flashcards, quizzes, notes and a focus timer.
- **R.U.F.U.S** – green internet research agent: multi-search research reports with citations, quick web lookups, watchlist with daily digests.
- Switch by voice ("Jarvis, switch to Atom"), menu buttons or Ctrl+J.

### Features
- Local AI through Ollama with tool calling; streamed, spoken replies.
- Offline speech recognition (faster-whisper) with push-to-talk and hands-free wake word.
- Windows voices or natural Piper voices, one per agent, with speed and depth settings.
- Long-term memory (SQLite): conversation history, recalled excerpts, learned facts.
- Desktop control: apps, websites, folders, volume, media, timers, screenshots, lock and power (with confirmation).
- Protocols: named step sequences ("Activate House Party protocol") with an editor.
- Morning briefing with calendar, tasks, watchlist and tech headlines.
- Floating, frameless mini hologram with a hover microphone.
- Settings › Performance: every speed-up switchable, plus timing measurements.

### Safety
- No agent can run code; app names, URLs and folders are validated.
- After web content is read, all non-search actions are blocked for that request.
