import { describe, expect, it } from "vitest"
import { describeDevice } from "@/lib/user-agent"

describe("describeDevice", () => {
  it.each([
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36", "Chrome on Windows"],
    ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36 Edg/126.0", "Edge on Windows"],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15", "Safari on macOS"],
    ["Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile Safari/604.1", "Safari on iOS"],
    ["Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36", "Chrome on Android"],
    ["Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0", "Firefox on Linux"],
  ])("%s -> %s", (ua, label) => expect(describeDevice(ua)).toBe(label))

  it("falls back for a missing or unrecognised agent instead of guessing", () => {
    expect(describeDevice(null)).toBe("Unknown device")
    expect(describeDevice("curl/8.0")).toBe("Unknown device")
    expect(describeDevice("python-requests/2.31 (Linux)")).toBe("Linux")
  })
})
