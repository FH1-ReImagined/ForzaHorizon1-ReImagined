# Co-op stall and reported sunrise FPS drop (#444)

I inspected the full issue, its comment and both attachments on 2026-10-25.
The runtime cause remains unresolved. This investigation supports a diagnostic
change, not a performance fix or closing the issue.

## Attachment identity and session boundaries

| Attachment | Bytes | SHA-256 |
| --- | ---: | --- |
| [Odd stall, 33939467](https://github.com/user-attachments/files/33939467/forza.log) | 17,192,075 | `1e5507d3de1757977ec97094fc52c51440b8fe60380e81bc54fc58b6e8364210` |
| [Performance drop, 33939522](https://github.com/user-attachments/files/33939522/forza.log) | 17,198,843 | `650eff9aaec8fbd41714a9608ccc5077eb1dc8420d0eee14eeb17245e591ae8b` |

The second file begins with every byte of the first file, followed by 6,768 bytes.
These are two snapshots of one appended log, not independent reproductions.
Both contain sessions dating back to October 15. October 25 has a short start at
22:08:26 and the gameplay start at 22:11:50. The stall attachment ends at
22:32:58.501; the performance attachment continues to 22:38:20.440.
Times here are the log's own timestamps; its timezone is not recorded.

## What the current session establishes

The reporter lists Windows 11, i7-10700, 16 GB RAM, RTX 2060 Super, TU4/DLC,
launcher 0.1.35 and public co-op. The log confirms the RTX 2060 SUPER Vulkan
backend, NVIDIA driver 596.49, a 2560x1440 swapchain with FIFO latest ready,
TU4, 16 DLC packages and public Forza Live activity. It reports draw scaling
enabled, but the initial swap source is 1280x720. That warning does not establish
the later scene render size or the effective scale/MSAA/upscaler configuration.
The track settings retain console shadow distance and texture LOD bias.

At 22:12:26 the command processor's cycle clock passes its comparison with
GetThreadTimes (106%). Its minute CPU costs are usable measurements, though
they do not measure GPU execution time. GPU fence wait time measures host
blocking, not exclusively shader cost.

| Log interval/end | Evidence |
| --- | --- |
| 22:21:58, minute 10 | 44.1 FPS; 10.6 ms command-processor CPU, 7.6 ms GPU waits, 3,523 draws; 3.00 us CPU/draw |
| 22:22:17.930 | 1,014 ms GPU submission wait; a separate earlier stall |
| 22:24:58, minute 13 | 39.9 FPS; 10.3 ms CPU, 9.2 ms GPU waits, 3,371 draws |
| 22:25:58 and 22:26:58, minutes 14-15 | 32.0/32.2 FPS; 14.3/14.9 ms CPU, 11.9/11.1 ms GPU waits, 4,704/4,923 draws; CPU/draw 3.04/3.02 us; worst frame 43 ms |
| 22:29:58 and 22:30:58, minutes 18-19 | 31.8/32.7 FPS; 12.2/10.6 ms CPU, 14.5/15.6 ms GPU waits; worst frame 43/42 ms |
| 22:31:50.891 | Watchdog sees no frame for 201 ms; main loop has advanced 8 frames |
| 22:31:52.764 | Vulkan waits 2,073 ms for submission 184114; outstanding submissions contain 961, 178, 274, 1,792 draws |
| 22:31:52.778 | Render frame took 2,088 ms while main loop advanced 29 frames |
| 22:31:56.088 | Vulkan waits 3,087 ms for submission 184131; outstanding submissions contain 277, 969, 173, 317, 173, 1,808 draws |
| 22:31:56.101 | Render frame took 3,108 ms while main loop advanced 33 frames |
| 22:32:02.780 | Slow-frame window attributes the long frames to GPU waits: 1,072 ms average GPU waits, 13 ms CPU, zero resolve readbacks |
| 22:32:58, minute 21 | 34.0 FPS; worst frame 50 ms |
| 22:33:58 through 22:37:58, minutes 22-26, second attachment only | Recovery to 41.1, 42.8, 44.6, 42.8 and 41.0 FPS |

The sustained low FPS predates the two multi-second waits. The low intervals
have different scene work and GPU waits, with roughly stable CPU cost per draw.
No logged time-of-day marker identifies sunrise. The later recovery rules out
claiming an irreversible collapse from elapsed time alone.

During the long waits, device-local use is 3,976 of 7,449 MB and system GPU use
223 of 7,482 MB. There is no current-session device-loss or render-target
allocation failure. Minute 14-19 summaries round resolve readbacks to 0.0 per
frame and report no pipeline churn. Render-target storage remains 2,604 MB;
texture storage stays around 454-456 MB while texture counts change. Loads and
evictions alone do not show a streaming failure. There are no ZIPREAD failures.
Small readback-related slow frames elsewhere do not explain these long waits.

The gameplay session has info/warning/error output, no DEBUG/trace flood.
A 536,242,732-byte driver-cache save finishes at 22:31:20.210, about 30 seconds
before the watchdog warning; this timing does not identify it as the stall's
cause. Matchmaking earlier in the session coincides with several 260-380 ms
game waits, distinct from the later fence waits. Public co-op by itself does not
establish a networking cause. The missing ambient sound file appears after
FPS has recovered and does not explain the earlier GPU fence stalls.

## Limits and next diagnostic

The separately supplied MSAA report concerned a large scale-dependent collapse
on different hardware. It is not established as this reporter or this cause.
Old launch settings from this reporter's #221 do not establish current settings.
That alt-tab report was separately confirmed fixed; #444 has no device loss.
There is no evidence here for a shared cause with #443.

Current main adds the render-target reuse and watch-walk work (0340/0341) since
v0.1.35. Their documented CPU optimization is not validation of a #444 fix.
I have not run the game or reproduced these symptoms for this diagnostic change.

The smallest missing input is the resolved runtime configuration. Four
`Performance settings at guest launch` lines now record selected cvars after
plugin loading, game defaults, benchmark restore and ultrawide preparation, just
before guest launch. Missing backend-specific flags say `<unavailable>` rather
than implying false. Values are bounded and control characters replaced; paths,
accounts, server details and arbitrary config entries are excluded.

These are cvar values at launch, not a promise that every requested mode is
active. Zero/negative automatic queue settings still require the vendor's
documented policy; the existing device/swapchain logs identify selected hardware
and presentation. Live changes after launch are not covered by this snapshot.
`log_level`/`log_verbose` do not describe every category/sink override.

Once settings are known, a controlled local/offline run at the same location,
camera and time of day, with frame-cost CSV and GPU timing, can separate changed
draw work from a GPU execution/presentation delay. Change one setting per trial;
do not reduce quality or submission size by default on this evidence.
