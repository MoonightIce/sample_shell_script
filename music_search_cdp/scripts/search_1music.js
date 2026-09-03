// Connects to a local Chrome over CDP, opens (or reuses) a 1music.cc tab, waits
// out Cloudflare Turnstile, searches for a song, and prints the matching result
// cards as JSON. Does not click any download button and does not fetch/save any
// audio file.
//
// Usage:
//   node search_1music.js "<query>" [limit] [cdpPort]
//
// Prereqs:
//   - A Chrome instance already running with --remote-debugging-port=<cdpPort>
//     (default 9223), e.g.:
//       open -na "Google Chrome" --args --remote-debugging-port=9223 \
//         --user-data-dir=/tmp/chrome-cdp-profile
//   - npm install puppeteer-core (in this directory, or a parent node_modules)

const puppeteer = require('puppeteer-core');

const QUERY = process.argv[2];
const LIMIT = parseInt(process.argv[3] || '10', 10);
const PORT = process.argv[4] || '9223';

if (!QUERY) {
  console.error('Usage: node search_1music.js "<query>" [limit] [cdpPort]');
  process.exit(1);
}

// Reuse an existing 1music.cc tab if one is already open instead of always
// spawning a new one — searches run repeatedly against the same session.
async function getOrCreatePage(browser) {
  const pages = await browser.pages();
  const existing = pages.find((p) => p.url().includes('1music.cc') && !p.url().includes('/download'));
  if (existing) {
    await existing.bringToFront();
    return existing;
  }
  const page = await browser.newPage();
  await page.goto('https://1music.cc/zh-CN', { waitUntil: 'networkidle2' });
  return page;
}

async function waitForTurnstile(page) {
  try {
    await page.waitForFunction(() => {
      const input = document.querySelector(
        'input[placeholder*="验证"], input[type="search"], input[type="text"]'
      );
      return input && !/验证/.test(input.placeholder || '');
    }, { timeout: 60000 });
  } catch (e) {
    throw new Error(
      '验证未在 60 秒内通过。若窗口里出现可交互的验证挑战，请手动完成后重新运行。'
    );
  }
}

// The site fires GET https://api.1music.cc/search?songs=... once the query is
// submitted. Waiting on that response (rather than a fixed sleep or a guess at
// when innerText changes) is what makes this reliable — pressing Enter too
// soon after typing, or trusting a text-based heuristic, both let the page's
// still-showing homepage recommendations get read as if they were results.
async function search(page, query) {
  const input = await page.$('input[type="search"], input[type="text"]');
  await input.click();
  // Triple-click + Backspace is not reliable on this React-controlled field
  // (leftover text from a prior search can survive and get a new query
  // appended after it). Force the value empty via the native setter + a real
  // 'input' event so React's onChange actually sees the field as cleared.
  await input.evaluate((el) => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(el, '');
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await input.type(query, { delay: 60 });
  await new Promise((r) => setTimeout(r, 500));

  const responsePromise = page.waitForResponse(
    (res) => res.url().includes('api.1music.cc/search'),
    { timeout: 15000 }
  ).catch(() => null);

  await page.keyboard.press('Enter');
  const response = await responsePromise;
  if (!response) {
    throw new Error('搜索请求未在 15 秒内发出/响应，可能页面状态异常，请重试。');
  }
  // Let the DOM finish rendering the new cards after the API response lands.
  await new Promise((r) => setTimeout(r, 700));
}

// Search occasionally fails transiently (Turnstile re-triggering, a stuck
// page state, a slow backend). On failure, wait 15s and reload the page once
// before trying again, rather than failing the whole batch immediately.
async function searchWithRetry(page, query, retries = 1) {
  for (let attempt = 0; ; attempt++) {
    try {
      await search(page, query);
      return;
    } catch (e) {
      if (attempt >= retries) throw e;
      console.error(`${e.message} 等待 15 秒后刷新页面重试...`);
      await new Promise((r) => setTimeout(r, 15000));
      await page.reload({ waitUntil: 'networkidle2' });
      await waitForTurnstile(page);
    }
  }
}

// Marks each real result card's download button with a stable data attribute
// (data-1music-idx) so a later script invocation can click the same card by
// index without relying on fragile structural assumptions surviving twice.
async function extractCards(page, limit) {
  return page.evaluate((limit) => {
    const buttons = Array.from(document.querySelectorAll('button')).filter((b) =>
      b.querySelector('svg')
    );
    const cards = [];
    for (const btn of buttons) {
      let el = btn;
      for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
        const text = (el.textContent || '').trim();
        if (
          text.length > 0 &&
          text.length < 200 &&
          el.querySelector('img') &&
          !text.includes('Kolis Music') &&
          !text.includes('补给站')
        ) {
          cards.push({ text, btn });
          break;
        }
      }
    }
    const top = cards.slice(0, limit);
    top.forEach(({ btn }, idx) => btn.setAttribute('data-1music-idx', String(idx)));
    return top.map(({ text }, idx) => ({ index: idx, text }));
  }, limit);
}

async function main() {
  const browser = await puppeteer.connect({
    browserURL: `http://127.0.0.1:${PORT}`,
    defaultViewport: null,
  });

  const page = await getOrCreatePage(browser);
  await waitForTurnstile(page);

  try {
    await searchWithRetry(page, QUERY);
  } catch (e) {
    console.error(e.message);
    await browser.disconnect();
    process.exit(1);
  }

  const cards = await extractCards(page, LIMIT);
  if (cards.length === 0) {
    console.error('没有找到匹配结果。');
  }
  console.log(JSON.stringify({ query: QUERY, results: cards }, null, 2));

  await browser.disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
