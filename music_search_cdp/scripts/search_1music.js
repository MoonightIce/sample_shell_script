// Connects to a local Chrome over CDP, opens 1music.cc, waits out Cloudflare
// Turnstile, searches for a song, and prints the top N result cards as JSON.
// Does not click any download button and does not fetch/save any audio file.
//
// Usage:
//   node search_1music.js "<query>" [limit] [cdpPort]
//
// Prereqs:
//   - A Chrome instance already running with --remote-debugging-port=<cdpPort>
//     (default 9223), e.g.:
//       open -na "Google Chrome" --args --remote-debugging-port=9223 \
//         --user-data-dir=/tmp/chrome-cdp-profile
//   - npm install puppeteer-core (in the same directory as this script, or a
//     parent node_modules)

const puppeteer = require('puppeteer-core');

const QUERY = process.argv[2];
const LIMIT = parseInt(process.argv[3] || '10', 10);
const PORT = process.argv[4] || '9223';

if (!QUERY) {
  console.error('Usage: node search_1music.js "<query>" [limit] [cdpPort]');
  process.exit(1);
}

async function main() {
  const browser = await puppeteer.connect({
    browserURL: `http://127.0.0.1:${PORT}`,
    defaultViewport: null,
  });

  const page = await browser.newPage();
  await page.goto('https://1music.cc/zh-CN', { waitUntil: 'networkidle2' });

  // Wait for Cloudflare Turnstile to clear in this real browser window.
  try {
    await page.waitForFunction(() => {
      const input = document.querySelector(
        'input[placeholder*="验证"], input[type="search"], input[type="text"]'
      );
      return input && !/验证/.test(input.placeholder || '');
    }, { timeout: 60000 });
  } catch (e) {
    console.error(
      '验证未在 60 秒内通过。若窗口里出现可交互的验证挑战，请手动完成后重新运行。'
    );
    await browser.disconnect();
    process.exit(1);
  }

  const input = await page.$('input[type="search"], input[type="text"]');
  await input.click({ clickCount: 3 });
  await input.type(QUERY, { delay: 50 });
  await page.keyboard.press('Enter');

  // Wait for the results grid to actually reflect the query instead of a fixed
  // sleep — the homepage's default/recommended cards look just like real
  // results and a short sleep can read them before the search updates the DOM.
  try {
    await page.waitForFunction((q) => {
      const firstChar = q.trim()[0];
      return document.body.innerText.includes(firstChar);
    }, { timeout: 15000 }, QUERY);
  } catch (e) {
    console.error('搜索结果未在 15 秒内更新，可能是这首歌没有匹配结果。');
  }
  await new Promise((r) => setTimeout(r, 800));

  const results = await page.evaluate((limit) => {
    // Each result card: a download-icon button whose closest reasonably-sized
    // ancestor's text is "<title><artist> · <album/other>". We don't have a
    // stable class name to key off, so walk up from each icon button.
    const buttons = Array.from(document.querySelectorAll('button')).filter((b) =>
      b.querySelector('svg')
    );
    const cards = [];
    for (const btn of buttons) {
      let el = btn;
      for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
        const text = (el.textContent || '').trim();
        if (text.length > 0 && text.length < 200 && el.querySelector('img')) {
          cards.push(text);
          break;
        }
      }
    }
    return cards.slice(0, limit);
  }, LIMIT);

  console.log(JSON.stringify({ query: QUERY, results }, null, 2));

  await browser.disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
