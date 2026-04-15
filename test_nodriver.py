"""nodriver の動作確認"""
import sys, asyncio
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# WindowsSelectorEventLoopPolicy は設定しない（ProactorEventLoop が必要）

async def main():
    try:
        import nodriver as uc
        print("nodriver import OK")
    except ImportError as e:
        print(f"nodriver import NG: {e}")
        return

    loop = asyncio.get_event_loop()
    print(f"event loop: {type(loop).__name__}")

    print("ブラウザ起動中...")
    try:
        browser = await uc.start(headless=True)
        print("ブラウザ起動 OK")
        tab = await browser.get("https://x.com")
        await asyncio.sleep(3)
        print(f"URL: {tab.url}")
        browser.stop()
        print("完了")
    except Exception as e:
        print(f"エラー: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
