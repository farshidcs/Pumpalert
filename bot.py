import asyncio
import aiohttp
import os
from datetime import datetime
import logging

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# تنظیمات بات
BOT_TOKEN = "8454411687:AAGLoczSqO_ptazxaCaBfHiiyL05yMMuCGw"
CHAT_ID = "1758259682"

class MultiCoinMonitor:
    def __init__(self):
        self.session = None
        self.symbols = [
            "PORT3_USDT",
            "KAITO_USDT", 
            "AEVO_USDT",
            "COAI_USDT"
        ]
        self.threshold = 1.0  # 1% threshold for alerts
        
    async def init_session(self):
        """شروع session"""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={'User-Agent': 'MultiCoinMonitor/1.0'}
        )
        logger.info("Session started")
    
    async def close_session(self):
        """بستن session"""
        if self.session:
            await self.session.close()
            logger.info("Session closed")
    
    async def get_1min_candle(self, symbol):
        """گرفتن کندل 1 دقیقه ای"""
        try:
            url = "https://openapi.bitunix.com/api/spot/v1/market/kline"
            params = {
                'coin_pair': symbol,
                'type': '1min',
                'limit': 2
            }
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if data.get('code') == 200 and data.get('data') and len(data['data']) >= 2:
                        klines = data['data']
                        current_candle = klines[-1]
                        prev_candle = klines[-2]
                        
                        open_price = float(current_candle['open'])
                        close_price = float(current_candle['close'])
                        prev_close = float(prev_candle['close'])
                        
                        # محاسبه تغییر کندل
                        if open_price > 0:
                            candle_change = ((close_price - open_price) / open_price) * 100
                        else:
                            candle_change = 0
                        
                        return {
                            'symbol': symbol,
                            'candle_change': candle_change
                        }
                        
        except Exception as e:
            logger.error(f"خطا در گرفتن کندل {symbol}: {e}")
        return None
    
    async def send_telegram(self, message):
        """ارسال پیام تلگرام"""
        try:
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
    
    async def send_alert(self, coin_name, change):
        """ارسال هشدار ساده"""
        if change >= self.threshold:
            alert_type = "PUMP"
            emoji = "🚀"
            sign = "+"
        elif change <= -self.threshold:
            alert_type = "DUMP"  
            emoji = "📉"
            sign = ""
        else:
            return
        
        message = f"""{emoji} <b>{alert_type}</b>

{coin_name}: {sign}{change:.2f}%
{datetime.now().strftime("%H:%M:%S")}"""
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"Alert sent: {coin_name} {sign}{change:.2f}%")
    
    async def send_status_report(self):
        """گزارش وضعیت ساده"""
        report_lines = ["📊 <b>Status Report</b>\n"]
        
        for symbol in self.symbols:
            candle_data = await self.get_1min_candle(symbol)
            if candle_data:
                coin_name = symbol.replace('_USDT', '')
                change = candle_data['candle_change']
                
                if change > 0:
                    emoji = "🟢"
                    sign = "+"
                else:
                    emoji = "🔴"
                    sign = ""
                
                report_lines.append(f"{emoji} {coin_name}: {sign}{change:.2f}%")
        
        report_lines.append(f"\n🕒 {datetime.now().strftime('%H:%M:%S')}")
        message = "\n".join(report_lines)
        
        await self.send_telegram(message)
        logger.info("Status report sent")
    
    async def check_all_coins(self):
        """چک کردن همه ارزها"""
        for symbol in self.symbols:
            candle_data = await self.get_1min_candle(symbol)
            if candle_data:
                coin_name = symbol.replace('_USDT', '')
                change = candle_data['candle_change']
                await self.send_alert(coin_name, change)
            
            # فاصله کوتاه بین درخواست‌ها
            await asyncio.sleep(0.5)
    
    async def run(self):
        """اجرای اصلی"""
        await self.init_session()
        logger.info("Multi-Coin Monitor started!")
        
        # پیام شروع
        coin_list = ", ".join([s.replace('_USDT', '') for s in self.symbols])
        await self.send_telegram(f"🤖 Multi-Coin Monitor started!\n\nCoins: {coin_list}\nThreshold: ±{self.threshold}%\nReports every 5min")
        
        retry_count = 0
        max_retries = 3
        minute_counter = 0
        
        try:
            while True:
                try:
                    await self.check_all_coins()
                    minute_counter += 1
                    
                    # هر 5 دقیقه گزارش
                    if minute_counter >= 5:
                        await self.send_status_report()
                        minute_counter = 0
                    
                    retry_count = 0
                    logger.info(f"Check completed. Next in 1min (Report in {5-minute_counter}min)")
                    await asyncio.sleep(60)
                    
                except Exception as e:
                    retry_count += 1
                    logger.error(f"Error {retry_count}/{max_retries}: {e}")
                    
                    if retry_count >= max_retries:
                        await self.send_telegram("🚨 Monitor having issues. Will retry in 5 minutes.")
                        await asyncio.sleep(300)
                        retry_count = 0
                        minute_counter = 0
                    else:
                        await asyncio.sleep(30)
                        
        except KeyboardInterrupt:
            logger.info("Stopping...")
        except Exception as e:
            logger.error(f"Critical error: {e}")
            await self.send_telegram(f"🚨 Critical Error: {str(e)[:100]}")
        finally:
            await self.close_session()
            logger.info("Monitor stopped")

# برای web hosting
from aiohttp import web

async def health_handler(request):
    return web.json_response({"status": "Multi-Coin Monitor OK"})

async def init_bot(app):
    monitor = MultiCoinMonitor()
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
    app = create_app()
    port = int(os.getenv('PORT', 8080))
    
    logger.info(f"Starting Multi-Coin Monitor on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
