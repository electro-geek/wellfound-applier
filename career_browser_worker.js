#!/usr/bin/env node
"use strict";

// One long-lived Playwright process. JSON commands are read from stdin and one
// JSON response is written per line, keeping browser state alive for the scan.
const readline = require("readline");
const path = require("path");
const os = require("os");

function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_CORE_PATH,
    "playwright",
    "playwright-core",
    path.join(process.env.ROTE_HOME || path.join(os.homedir(), ".rote"), "lib/playwright-runtime/node_modules/playwright-core"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate); } catch (error) {
      if (error.code !== "MODULE_NOT_FOUND") throw error;
    }
  }
  throw new Error("Playwright is not installed. Run `npm install` or set PLAYWRIGHT_CORE_PATH.");
}

const { chromium } = loadPlaywright();

let browser;
let page;

async function ensureBrowser() {
  if (page) return;
  const launchOptions = {
    headless: true,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }
  browser = await chromium.launch(launchOptions);
  const context = await browser.newContext({
    userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
    locale: "en-US",
  });
  page = await context.newPage();
}

async function discover(query) {
  await ensureBrowser();
  const url = "https://html.duckduckgo.com/html/?q=" + encodeURIComponent(query);
  let lastError;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
      lastError = undefined;
      break;
    } catch (error) {
      lastError = error;
      await page.waitForTimeout(750);
    }
  }
  if (lastError) throw lastError;
  const results = await page.locator("a.result__a").evaluateAll((as) =>
    as.slice(0, 10).map((a) => ({ title: (a.innerText || a.textContent || "").trim(), url: a.href }))
  );
  return { pageTitle: await page.title(), results };
}

async function scrape(url) {
  await ensureBrowser();
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 40000 });
    await page.waitForTimeout(1500);
    const links = await page.locator("a[href]").evaluateAll((as) =>
      as.map((a) => ({
        title: (a.innerText || a.textContent || "").replace(/\s+/g, " ").trim(),
        url: a.href,
      })).filter((x) => x.title && x.url).slice(0, 800)
    );
    const body = (await page.locator("body").innerText({ timeout: 5000 })).slice(0, 120000);
    return { ok: true, requestedUrl: url, finalUrl: page.url(), title: await page.title(), links, body };
  } catch (error) {
    return { ok: false, requestedUrl: url, finalUrl: page.url(), error: String(error) };
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", async (line) => {
  try {
    const command = JSON.parse(line);
    let result;
    if (command.action === "discover") result = await discover(command.query);
    else if (command.action === "scrape") result = await scrape(command.url);
    else if (command.action === "close") {
      if (browser) await browser.close();
      result = { ok: true };
      process.stdout.write(JSON.stringify(result) + "\n");
      process.exit(0);
    } else throw new Error("Unknown action: " + command.action);
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch (error) {
    process.stdout.write(JSON.stringify({ ok: false, error: String(error) }) + "\n");
  }
});
