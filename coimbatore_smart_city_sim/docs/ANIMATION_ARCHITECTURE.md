# Animation architecture

## One clock

Python owns the clock. When Play is active the app sleeps for `1 / playback_speed`
seconds, advances the engine one 30-minute step, and calls `st.rerun()`. The map is then
re-rendered at the new step index.

Earlier revisions ran a `setInterval` inside the map iframe **and** the Python loop, each
advancing one step independently. Two timers drift apart as soon as a rerun takes longer
than the interval, so the map clock silently disagreed with the metric cards. That
in-iframe timer has been removed; the map is now a pure function of the step index.

## Why the map can be re-rendered each step

Two changes made this affordable:

- route geometry is cached on each bus, cutting the trajectory build from roughly 506 ms
  to 106 ms;
- the payload is columnar and quantised, cutting the document from 4.10 MB to 0.91 MB and
  the render call from about 22 ms to 10 ms.

The map HTML is rebuilt only when the step index actually changes, tracked in session
state, so dashboard interactions that do not advance time do not re-render it.

## Performance

| Operation | Before | After |
|---|---:|---:|
| Full 48-step day simulation | 210 ms | 40 ms |
| 48-frame trajectory build | 506 ms | 106 ms |
| Map document render | 22 ms | 10 ms |
| Map payload | 4.10 MB | 0.91 MB |

The day simulation is over five times faster because route segment lengths and cumulative
distances are computed once per bus instead of on every property access; previously the
engine and the trajectory builder together performed roughly two million redundant
haversine evaluations per simulated day.
