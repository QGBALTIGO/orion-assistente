"""Render the real panel HTML and local assets without authentication or writes."""
import asyncio, json, os
from pathlib import Path
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2]
async def main():
    report=[];output=Path(os.getenv('ORION_VISUAL_OUTPUT','/mnt/data/orion-auth-previews'));output.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser=await p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),args=['--no-sandbox'])
        for width,height in [(320,740),(390,844),(820,1180),(1366,900)]:
            for theme in ['light','dark']:
                page=await browser.new_page(viewport={'width':width,'height':height},color_scheme=theme)
                # Offline rendering only. The environment blocks network navigation to localhost.
                import base64,re
                html=(ROOT/'app/static/panel.html').read_text()
                def css(match):
                    value=(ROOT/'app/static'/match.group(1)).read_text()
                    value=re.sub(r"url\('/brand/([^']+)'\)",lambda m:"url('data:font/woff2;base64,"+base64.b64encode((ROOT/'app/static'/m.group(1)).read_bytes()).decode()+"')",value)
                    return '<style>'+value+'</style>'
                html=re.sub(r'<link rel="stylesheet" href="/panel-assets/([^"]+)">',css,html)
                html=re.sub(r'<script src="/panel-assets/([^"]+)"></script>',lambda m:'<script>'+(ROOT/'app/static'/m.group(1)).read_text()+'</script>',html)
                for name in ['orion-brand-dark.svg','orion-brand-light.svg']:
                    html=html.replace('/brand/'+name,'data:image/svg+xml;base64,'+base64.b64encode((ROOT/'app/static'/name).read_bytes()).decode())
                await page.evaluate("window.fetch=async (url)=>new Response(String(url).includes('/auth/features')?'{\"registration\":true,\"passwordRecovery\":true}':'{}',{status:String(url).includes('/auth/features')?200:401,headers:{'Content-Type':'application/json'}})")
                await page.set_content(html);await page.evaluate('document.fonts.ready')
                await page.locator('#loginView').wait_for(state='visible')
                data=await page.evaluate('''() => {const q=s=>getComputedStyle(document.querySelector(s));return {width:innerWidth,scroll:document.documentElement.scrollWidth,background:q('#loginView').backgroundColor,card:q('.auth-card').backgroundColor,text:q('.auth-card-top h2').color,button:q('.auth-primary').backgroundColor,font:q('.auth-card-top h2').fontFamily,bodyFont:q('.auth-shell').fontFamily}}''')
                assert data['scroll']<=width,(width,theme,data)
                assert data['button']=='rgb(46, 123, 246)',data
                assert 'Outfit' in data['font'] and 'Figtree' in data['bodyFont'],data
                await page.screenshot(path=str(output/f'login-{theme}-{width}.png'),full_page=True,animations="disabled")
                await page.locator('#authTabRegister').click();await page.screenshot(path=str(output/f'register-{theme}-{width}.png'),full_page=True,animations="disabled")
                assert await page.evaluate('document.documentElement.scrollWidth')<=width
                await page.locator('#authTabLogin').click();await page.locator('#authForgotLink').click()
                assert await page.locator('#forgotForm').is_visible()
                report.append(dict(data,theme=theme));await page.close()
        await browser.close()
    (output/'report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
if __name__=='__main__':asyncio.run(main())
