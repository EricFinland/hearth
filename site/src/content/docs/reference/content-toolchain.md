---
title: Content toolchain
description: The local media tools agents can use to research, assemble, and voice content on the box.
---

hearth ships a fully-local content-creation toolchain so an agent can produce
media without any cloud service. Because agents run with full shell reach, the
tools just need to be on `PATH`.

Enabled by default with
[`hearth.creator.enable`](/hearth/reference/configuration/#hearthcreatorenable).

## What is included

| Tool | Use |
| --- | --- |
| `ffmpeg` | Assemble and encode video and audio, overlays, captions. |
| `yt-dlp` | Pull source clips from the web. |
| `imagemagick` | Image manipulation, thumbnails, caption frames. |
| `sox` | Audio shaping. |
| `piper-tts` | Fully-local neural text-to-speech for voiceover. |

## Why it is here

It lets an agent run an end-to-end media loop entirely on your hardware: research
a template, pull clips, assemble, caption, and voice a video, all on the box's
GPU with nothing leaving the machine. Combined with the
[marathon](/hearth/concepts/autonomy/#marathon) and
[permission](/hearth/concepts/permission-modes/) modes, you can hand a content
goal to an agent and supervise it from the cockpit or over Telegram.

If you do not want these tools installed, set `hearth.creator.enable = false`.
