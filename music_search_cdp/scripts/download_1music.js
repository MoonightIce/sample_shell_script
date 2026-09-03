// Re-runs the same search as search_1music.js against the reused 1music.cc
// tab, then clicks the download icon on ONE specific result (by index, as
// printed by search_1music.js), picks a format in the modal, and confirms
// "下载". That navigates to 1music.cc's own /download page (in a new tab or
// the same one), which does its own fetch/transcode and eventually drives a
// real Chrome download. This script points that download at a dedicated
// staging folder (via CDP Page.setDownloadBehavior) instead of the browser's
// default Downloads folder, waits for it to actually finish, and prints the
// saved file's absolute path — it does not talk to metube.
//
// This script performs a real, user-facing download action. Only run it
// after the caller has shown the search results to the user and the user has
// picked which index to download — never chain it automatically after
// search_1music.js.
//
// Usage:
//   node download_1music.js "<query>" <index> [format] [downloadDir] [cdpPort]
//   format: "flac" (default) or "mp3"
//   downloadDir: default /Users/admin/Documents/Music/.music-workflow/incoming-downloads/<run-id>

const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const QUERY = process.argv[2];
const INDEX = parseInt(process.argv[3], 10);
const FORMAT = (process.argv[4] || 'flac').toLowerCase();
const DEFAULT_STAGING_ROOT = '/Users/admin/Documents/Music/.music-workflow/incoming-downloads';
const DOWNLOAD_DIR = process.argv[5] || path.join(DEFAULT_STAGING_ROOT, `${Date.now()}`);
const PORT = process.argv[6] || '9223';
const DOWNLOAD_TIMEOUT_MS = 10 * 60 * 1000; // the site's own transcode step can be slow

if (!QUERY || Number.isNaN(INDEX)) {
  console.error('Usage: node download_1music.js "<query>" <index> [format] [downloadDir] [cdpPort]');
  process.exit(1);
}
if (!['flac', 'mp3'].includes(FORMAT)) {
  console.error('format 必须是 flac 或 mp3');
  process.exit(1);
}

async function getExistingPage(browser) {
  const pages = await browser.pages();
  const existing = pages.find((p) => p.url().includes('1music.cc') && !p.url().includes('/download'));
  if (!existing) {
    throw new Error('没有找到已打开的 1music.cc 标签页，请先运行 search_1music.js。');
  }
  await existing.bringToFront();
  return existing;
}

async function waitForTurnstile(page) {
  await page.waitForFunction(() => {
    const input = document.querySelector(
      'input[placeholder*="验证"], input[type="search"], input[type="text"]'
    );
    return input && !/验证/.test(input.placeholder || '');
  }, { timeout: 60000 });
}

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
  await new Promise((r) => setTimeout(r, 700));
}

// Search occasionally fails transiently (Turnstile re-triggering, a stuck
// page state, a slow backend). On failure, wait 15s and reload the page once
// before trying again, rather than failing outright.
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

async function tagCards(page, upToIndex) {
  const matched = await page.evaluate((limit) => {
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
    const top = cards.slice(0, limit + 1);
    top.forEach(({ btn }, idx) => btn.setAttribute('data-1music-idx', String(idx)));
    return top.map(({ text }, idx) => ({ index: idx, text }));
  }, upToIndex);
  return matched;
}

// Points one page/target's downloads at downloadDir instead of the default
// Downloads folder. Best-effort: if the target is already gone (e.g. an ad
// popup that closed itself), swallow the error — completion is detected by
// polling the filesystem below, not by trusting CDP download events, because
// 1music.cc's own /download tab can close itself right after the file lands,
// tearing down the CDP session before a 'completed' event is guaranteed to
// arrive (observed: the file finished writing but the script hung forever
// waiting on that event).
async function armDownloadPath(page, downloadDir) {
  try {
    const client = await page.createCDPSession();
    await client.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: downloadDir });
  } catch (e) {
    // ignore — filesystem polling is the actual source of truth
  }
}

// Polls downloadDir for a file that isn't a Chrome in-progress temp file
// (.crdownload) and whose size has stopped changing across two checks.
function waitForStableFile(downloadDir, timeoutMs) {
  const start = Date.now();
  let lastSnapshot = null;
  return new Promise((resolve, reject) => {
    const tick = () => {
      let entries = [];
      try { entries = fs.readdirSync(downloadDir); } catch (e) { /* not created yet */ }
      const candidates = entries.filter(
        (f) => !f.endsWith('.crdownload') && !f.endsWith('.tmp') && !f.startsWith('.')
      );
      if (candidates.length > 0) {
        const file = candidates[0];
        let size = -1;
        try { size = fs.statSync(path.join(downloadDir, file)).size; } catch (e) { /* raced with rename */ }
        if (size > 0 && lastSnapshot && lastSnapshot.file === file && lastSnapshot.size === size) {
          resolve(file);
          return;
        }
        lastSnapshot = { file, size };
      }
      if (Date.now() - start > timeoutMs) {
        reject(new Error('下载未在超时时间内完成（本地目录中未出现稳定文件）'));
        return;
      }
      setTimeout(tick, 1500);
    };
    tick();
  });
}

async function main() {
  const browser = await puppeteer.connect({
    browserURL: `http://127.0.0.1:${PORT}`,
    defaultViewport: null,
  });

  const page = await getExistingPage(browser);
  await waitForTurnstile(page);

  try {
    await searchWithRetry(page, QUERY);
  } catch (e) {
    console.error(e.message);
    await browser.disconnect();
    process.exit(1);
  }

  const cards = await tagCards(page, INDEX);
  const target = cards.find((c) => c.index === INDEX);
  if (!target) {
    console.error(`索引 ${INDEX} 超出结果范围（共 ${cards.length} 条），已放弃点击下载。`);
    await browser.disconnect();
    process.exit(1);
  }
  console.error(`即将下载: [${INDEX}] ${target.text} (格式: ${FORMAT}) -> ${DOWNLOAD_DIR}`);

  fs.mkdirSync(DOWNLOAD_DIR, { recursive: true });

  // Arm the current tab in case 1music.cc reuses it instead of opening a
  // popup; also arm any popup the instant it appears. This only redirects
  // where Chrome saves the file — actual completion is detected by polling
  // the directory (see waitForStableFile), so it's fine if a popup never
  // shows up or closes itself before we'd otherwise notice.
  await armDownloadPath(page, DOWNLOAD_DIR);
  browser.on('targetcreated', async (t) => {
    const popupPage = await t.page().catch(() => null);
    if (popupPage) armDownloadPath(popupPage, DOWNLOAD_DIR);
  });

  const btn = await page.$(`button[data-1music-idx="${INDEX}"]`);
  await btn.click();
  try {
    await page.waitForSelector('div[role="dialog"]', { timeout: 8000 });
  } catch (e) {
    console.error('点击下载图标后弹窗未出现，可能点错了元素或页面结构已变化。');
    await browser.disconnect();
    process.exit(1);
  }
  // The dialog's radios can render a beat after the dialog container itself.
  const radio = await page.waitForSelector(
    `div[role="dialog"] input[type="radio"][value="${FORMAT}"]`,
    { timeout: 5000 }
  ).catch(() => null);
  if (!radio) {
    console.error('未找到格式选择框，弹窗结构可能已变化。');
    await browser.disconnect();
    process.exit(1);
  }
  await radio.click();
  await new Promise((r) => setTimeout(r, 200));

  const clickedDownload = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('div[role="dialog"] button'));
    const dl = btns.find((b) => b.textContent.trim() === '下载');
    if (!dl) return false;
    dl.click();
    return true;
  });
  if (!clickedDownload) {
    console.error('未找到弹窗里的"下载"按钮。');
    await browser.disconnect();
    process.exit(1);
  }

  console.error('已提交下载请求，等待 1music.cc 完成转码（可能需要几十秒到几分钟）...');

  let filename;
  try {
    filename = await waitForStableFile(DOWNLOAD_DIR, DOWNLOAD_TIMEOUT_MS);
  } catch (e) {
    console.error(`下载失败: ${e.message}`);
    await browser.disconnect();
    process.exit(1);
  }

  const savedTo = path.join(DOWNLOAD_DIR, filename);
  console.log(JSON.stringify({
    query: QUERY,
    index: INDEX,
    format: FORMAT,
    title: target.text,
    savedTo,
  }, null, 2));

  await browser.disconnect();
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
