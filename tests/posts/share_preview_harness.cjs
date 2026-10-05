// Execute Django-rendered inline JS with deterministic browser-shaped objects.
// This checks script behavior; native app/intent resolution needs device QA.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const scenarios = [
    ["desktop Chrome", "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0", false, false],
    ["preview crawler", "facebookexternalhit/1.1", false, false],
    ["iPhone Safari", "Mozilla/5.0 (iPhone) Version/18.0 Mobile Safari/604.1", true, false],
    ["iPhone Chrome", "Mozilla/5.0 (iPhone) CriOS/130.0 Mobile Safari/604.1", true, false],
    ["Android Chrome", "Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile", false, true],
    ["Android Firefox", "Mozilla/5.0 (Android 14) Firefox/130.0", false, false],
    ["Android WebView", "Mozilla/5.0 (Linux; Android 14; wv) Chrome/130.0 Mobile", false, true],
];

function execute(userAgent, missing = []) {
    const links = Object.fromEntries(Object.entries(input.links)
        .filter(([id]) => !missing.includes(id))
        .map(([id, href]) => [id, {
            href,
            style: {},
            // Ordinary anchors must retain their native, user-initiated action.
            addEventListener() { assert.fail("Do not intercept link clicks"); },
        }]));
    const forbiddenNavigation = () => assert.fail("Unexpected scripted navigation");
    const location = { replace: forbiddenNavigation, assign: forbiddenNavigation };
    Object.defineProperty(location, "href", { set: forbiddenNavigation });
    const window = {
        setTimeout() { assert.fail("No timed store fallback, including on retries"); },
        clearTimeout() {},
    };
    Object.defineProperty(window, "location", { get: () => location, set: forbiddenNavigation });
    const document = {
        hidden: false,
        getElementById: id => links[id] || null,
        addEventListener() { assert.fail("No accumulating visibility listeners"); },
    };
    const context = {
        window, document, navigator: { userAgent },
        setTimeout: window.setTimeout,
    };
    Object.defineProperty(context, "location", { get: () => location, set: forbiddenNavigation });
    vm.runInNewContext(input.script, context, { timeout: 1000 });
    return links;
}

for (const [name, ua, ios, intent] of scenarios) {
    const links = execute(ua);
    const deepParts = input.deepLink.split("://");
    const expected = intent
        ? "intent://" + deepParts[1] + "#Intent;scheme=" + deepParts[0] +
          ";package=" + input.packageName + ";S.browser_fallback_url=" +
          encodeURIComponent(input.links["android-store"]) + ";end"
        : input.deepLink;
    assert.equal(links["open-app"].href, expected, name);
    assert.equal(links["ios-store"].href, input.links["ios-store"], name);
    assert.equal(links["android-store"].href, input.links["android-store"], name);
    assert.equal(links["ios-store"].style.display, /Android/.test(ua) ? "none" : undefined, name);
    assert.equal(links["android-store"].style.display, ios ? "none" : undefined, name);
    // Repeated user actions follow the same anchor; no timers/listeners can race
    // a slow launch or redirect the user after returning from the app.
    for (let click = 0; click < 3; click++) {
        assert.equal(links["open-app"].href, expected, name + " repeat tap");
    }
}

// Optional/missing controls must not turn the preview into a JS exception.
const android = scenarios.find(([name]) => name === "Android Chrome")[1];
for (const missing of [["ios-store"], ["android-store"], ["open-app"], Object.keys(input.links)]) {
    const links = execute(android, missing);
    if (links["open-app"] && !links["android-store"]) {
        assert.ok(!links["open-app"].href.includes("browser_fallback_url"));
    }
}
console.log("Seven browser scenarios and missing-control cases passed.");
