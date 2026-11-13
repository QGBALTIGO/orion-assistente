/** Non-mutating public URL health, viewport and visual evidence.
 * Never submit booking forms, send messages, or authenticate to production.
 */
import { test, expect } from '@playwright/test';

const SITE = 'https://orionagenda.pro/s/barbearia-peixoto';
const VIEWPORTS = [
  { name: 'iphone-small', width: 375, height: 667 },
  { name: 'iphone-large', width: 430, height: 932 },
  { name: 'android-small', width: 360, height: 740 },
  { name: 'android-large', width: 412, height: 915 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'desktop', width: 1440, height: 900 },
];
for (const device of VIEWPORTS) {
  for (const theme of ['light', 'dark']) {
    test(device.name + ' ' + theme + ' read-only public layout', async ({ page }, testInfo) => {
      await page.setViewportSize({ width: device.width, height: device.height });
      await page.emulateMedia({ colorScheme: theme });
      const response = await page.goto(SITE, {
        waitUntil: 'domcontentloaded',
        timeout: 25000,
      });
      expect(response, 'HTTP response').not.toBeNull();
      expect(response.status()).toBeLessThan(500);
      await expect(page.locator('body')).toBeVisible();
      await page.waitForTimeout(600);
      const layout = await page.evaluate(() => ({
        viewport: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth,
        bodyText: document.body.innerText.length,
      }));
      expect(layout.bodyText, 'Visible site must have substantive content').toBeGreaterThan(100);
      expect(layout.scroll - layout.viewport, 'No global horizontal overflow').toBeLessThanOrEqual(4);
      await page.screenshot({
        path: testInfo.outputPath(device.name + '-' + theme + '.png'),
        fullPage: true, animations: 'disabled',
      });
    });
  }
}
test('public login route is reachable without writing data', async ({ page }) => {
  const response = await page.goto('https://orionagenda.pro/painel', {
    waitUntil: 'domcontentloaded', timeout: 25000,
  });
  expect(response).not.toBeNull();
  expect(response.status()).toBeLessThan(500);
  await expect(page.getByRole('heading', { name: /Bem-vindo ao Orion/i })).toBeVisible();
});