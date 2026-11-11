---
name: web-verify
description: Verify that web pages or UI changes work using project tests, HTTP checks, and headless Chrome screenshots, rendered DOM, and console errors, without touching the user's browser.
compatibility: Headless checks need Google Chrome or Chromium installed locally.
---

# Web Verification

Verify web behavior with repeatable commands, cheapest sufficient check first. Never verify in the user's running browser or profile; load `browser` against it only when the user asks you to operate their own browser.

## Choose checks

1. Run the project's existing tests that cover the change: unit, component, or end-to-end.
2. Request server routes and APIs directly, for example `curl -sS -i <url>`, and check status, headers, and body.
3. Check rendering, scripts, and layout with headless Chrome.
4. For multi-step flows such as logging in or submitting a form, reuse the project's Playwright or Puppeteer setup when one exists; otherwise use an isolated interactive instance. Do not install a test framework or download browser builds without approval.

Verify against local or development servers. Ask before submitting forms or changing data on shared or production services.

## Serve the app

- Reuse a dev server that already answers. Otherwise start it with `start_process`, `notify="output"`, and a `pattern` matching its ready line, such as `(?i)ready|listening|local:`, and continue when notified.
- Open static files directly as `file://` URLs.
- Stop servers you started once verification is done, unless the user wants them running.

## Headless Chrome

Use Chrome or Chromium: `google-chrome`, `chromium`, or `chromium-browser` on `PATH`; on macOS, the binary in `Google Chrome.app/Contents/MacOS/`; on Windows, `chrome.exe` under Program Files. The examples use bash syntax.

- Give every run its own profile under `.ene/scratch/web-verify/` so the user's profile is never touched.
- On Linux, the `DBUS_SESSION_BUS_ADDRESS=disabled:` prefix stops Chrome from stalling up to 25 seconds on an unresponsive desktop bus. The `dbus` errors it then logs are harmless.

Capture a screenshot and console messages in one run:

```bash
DBUS_SESSION_BUS_ADDRESS=disabled: google-chrome --headless=new --no-first-run \
  --no-default-browser-check --user-data-dir=.ene/scratch/web-verify/profile \
  --window-size=1280,800 --virtual-time-budget=5000 --enable-logging=stderr --v=0 \
  --screenshot=.ene/scratch/web-verify/page.png <url> 2> .ene/scratch/web-verify/chrome.log
grep CONSOLE .ene/scratch/web-verify/chrome.log
```

Dump the DOM after scripts run:

```bash
DBUS_SESSION_BUS_ADDRESS=disabled: google-chrome --headless=new --no-first-run \
  --no-default-browser-check --user-data-dir=.ene/scratch/web-verify/profile \
  --virtual-time-budget=5000 --dump-dom <url> \
  > .ene/scratch/web-verify/dom.html 2> .ene/scratch/web-verify/chrome.log
```

- `--virtual-time-budget` lets timers and async rendering settle before capture; raise it for slow pages.
- A screenshot covers only the window. Use a taller `--window-size` for long pages, or `390,844` for a phone layout.
- Inspect screenshots with `read_image` when it is available. Otherwise check the DOM and report that nothing was inspected visually.
- `CONSOLE` lines include console messages and uncaught exceptions, not failed requests. Check requests with `curl` or the server log.
- Search large DOM dumps with `grep_files` instead of reading them whole.

## Interactive checks

Use an isolated instance when a flow needs several steps and the project has no browser test setup:

1. Start Chrome with `start_process`, `notify="output"`, and `pattern="^DevTools listening on"`:

   ```bash
   DBUS_SESSION_BUS_ADDRESS=disabled: google-chrome --headless=new --no-first-run \
     --no-default-browser-check --remote-debugging-port=0 \
     --user-data-dir=.ene/scratch/web-verify/cdp-profile about:blank
   ```

2. Load `browser` and call `browser_status` with the notification's `ws://` URL as `cdp_url`. Then use `browser_open`, `browser_observe`, `browser_click`, and `browser_type` as that skill describes.
3. When done, call `browser_stop`, then `stop_process` for the instance.

## Report

State what was checked (URLs, viewport sizes, commands), the observed results, any console errors, and what remains unverified, such as visuals without image inspection or flows not exercised.
