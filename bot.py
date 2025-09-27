import asyncio
import aiohttp
import os
from datetime import datetime
import logging

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# تنظیمات بات
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

class BitcoinMonitor:
    def __init__(self):
        self.session = None
        
    async def init_session(self):
        """شروع session"""
        self.session = aiohttp.ClientSession()
        logger.info("Session started")
    
    async def close_session(self):
        """بستن session"""
        if self.session:
            await self.session.close()
            logger.info("Session closed")
    
    async def get_bitcoin_price(self):
        """گرفتن قیمت و تغییر بیت کوین"""
        try:
            url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    price = float(data['lastPrice'])
                    change_percent = float(data['priceChangePercent'])
                    return price, change_percent
                    
        except Exception as e:
            logger.error(f"خطا در گرفتن قیمت: {e}")
        return None, None
    
    async def send_telegram(self, message):
        """ارسال پیام تلگرام"""
        try:
            if not BOT_TOKEN or not CHAT_ID:
                logger.info(f"TEST: {message}")
                return True
                
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            async with self.session.post(url, json=data) as response:
                return response.status == 200
                
        except Exception as e:
            logger.error(f"خطا در ارسال پیام: {e}")
            return False
    
    async def send_bitcoin_update(self):
        """ارسال آپدیت بیت کوین"""
        price, change = await self.get_bitcoin_price()
        
        if price is None or change is None:
            await self.send_telegram("❌ خطا در دریافت اطلاعات بیت کوین")
            return
        
        # انتخاب emoji بر اساس تغییر
        if change > 0:
            emoji = "🟢"
            sign = "+"
        else:
            emoji = "🔴"
            sign = ""
        
        message = f"""{emoji} <b>Bitcoin Update</b>

💰 Price: ${price:,.2f}
📊 24h Change: {sign}{change:.2f}%
🕒 Time: {datetime.now().strftime("%H:%M:%S")}"""
        
        await self.send_telegram(message)
        logger.info(f"Update sent: ${price:,.2f} ({sign}{change:.2f}%)")
    
    async def run(self):
        """اجرای اصلی"""
        await self.init_session()
        logger.info("🤖 Bitcoin Monitor started!")
        
        # ارسال پیام شروع
        await self.send_telegram("🤖 Bitcoin Monitor started!\n⏰ Updates every 5 minutes")
        
        try:
            while True:
                await self.send_bitcoin_update()
                logger.info("Waiting 5 minutes...")
                await asyncio.sleep(300)  # 5 دقیقه = 300 ثانیه
                
        except KeyboardInterrupt:
            logger.info("Stopping...")
        except Exception as e:
            logger.error(f"خطای کلی: {e}")
            await self.send_telegram(f"🚨 Bot Error: {e}")
        finally:
            await self.close_session()
            logger.info("Monitor stopped")

# برای Render.com
from aiohttp import web

async def health_handler(request):
    return web.json_response({"status": "ok"})

async def init_bot(app):
    monitor = BitcoinMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())

async def cleanup_bot(app):
    if 'monitor_task' in app:
        app['monitor_task'].cancel()

def create_app():
    app = web.Application()
    app.router.add_get('/health', health_handler)
    app.on_startup.append(init_bot)
    app.on_cleanup.append(cleanup_bot)
    return app

if __name__ == "__main__":
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set - running in test mode")
    if not CHAT_ID:
        logger.warning("CHAT_ID not set - running in test mode")
    
    app = create_app()
    port = int(os.getenv('PORT', 8080))
    
    logger.info(f"Starting on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
