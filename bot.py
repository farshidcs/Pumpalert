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

class AlpineMonitor:
    def __init__(self):
        self.session = None
        self.symbol = "ALPINEUSDT"
        self.threshold = 1.0  # 1% threshold for alerts
        
    async def init_session(self):
        """شروع session"""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={'User-Agent': 'AlpineMonitor/1.0'}
        )
        logger.info("Session started")
    
    async def close_session(self):
        """بستن session"""
        if self.session:
            await self.session.close()
            logger.info("Session closed")
    
    async def get_1min_candle(self):
        """گرفتن کندل 1 دقیقه ای ALPINE"""
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': self.symbol,
                'interval': '1m',
                'limit': 2  # آخرین 2 کندل
            }
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if len(data) >= 2:
                        current_candle = data[-1]  # کندل جاری
                        prev_candle = data[-2]     # کندل قبلی
                        
                        # اطلاعات کندل جاری
                        open_price = float(current_candle[1])
                        close_price = float(current_candle[4])
                        high_price = float(current_candle[2])
                        low_price = float(current_candle[3])
                        volume = float(current_candle[5])
                        
                        # محاسبه تغییر کندل (از open تا close)
                        if open_price > 0:
                            candle_change = ((close_price - open_price) / open_price) * 100
                        else:
                            candle_change = 0
                        
                        # محاسبه تغییر کل (از close قبلی تا close جاری)
                        prev_close = float(prev_candle[4])
                        if prev_close > 0:
                            total_change = ((close_price - prev_close) / prev_close) * 100
                        else:
                            total_change = 0
                        
                        return {
                            'open': open_price,
                            'close': close_price,
                            'high': high_price,
                            'low': low_price,
                            'volume': volume,
                            'candle_change': candle_change,
                            'total_change': total_change
                        }
                        
        except Exception as e:
            logger.error(f"خطا در گرفتن کندل: {e}")
        return None
    
    async def get_24h_price(self):
        """گرفتن قیمت و تغییر 24 ساعته"""
        try:
            url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={self.symbol}"
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(url, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    price = float(data['lastPrice'])
                    change_24h = float(data['priceChangePercent'])
                    return price, change_24h
                        
        except Exception as e:
            logger.error(f"خطا در گرفتن قیمت 24h: {e}")
        return None, None
    
    def format_price(self, price):
        """فرمت قیمت"""
        if price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"
    
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
    
    async def send_pump_dump_alert(self, candle_data):
        """ارسال هشدار پامپ/دامپ"""
        candle_change = candle_data['candle_change']
        close_price = candle_data['close']
        volume = candle_data['volume']
        
        if candle_change >= self.threshold:
            # PUMP
            emoji = "🚀"
            alert_type = "PUMP"
            sign = "+"
        elif candle_change <= -self.threshold:
            # DUMP  
            emoji = "📉"
            alert_type = "DUMP"
            sign = ""
        else:
            return  # No significant change
        
        message = f"""{emoji} <b>{alert_type} ALERT!</b>

🪙 Coin: ALPINE
📊 1min Change: {sign}{candle_change:.2f}%
💰 Price: {self.format_price(close_price)}
📈 Volume: {volume:,.0f}
🕒 Time: {datetime.now().strftime("%H:%M:%S")}

#ALPINE #{alert_type.lower()}"""
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"🚨 {alert_type} Alert sent: {sign}{candle_change:.2f}%")
    
    async def send_status_report(self):
        """ارسال گزارش وضعیت (هر 5 دقیقه)"""
        price_24h, change_24h = await self.get_24h_price()
        candle_data = await self.get_1min_candle()
        
        if not price_24h or not candle_data:
            await self.send_telegram("❌ خطا در دریافت اطلاعات ALPINE")
            return
        
        # انتخاب emoji بر اساس تغییر 24h
        if change_24h > 0:
            emoji_24h = "🟢"
            sign_24h = "+"
        else:
            emoji_24h = "🔴"
            sign_24h = ""
        
        # انتخاب emoji برای کندل 1min
        candle_change = candle_data['candle_change']
        if candle_change > 0:
            emoji_1m = "🟢"
            sign_1m = "+"
        else:
            emoji_1m = "🔴" 
            sign_1m = ""
        
        message = f"""📊 <b>ALPINE Status Report</b>

🪙 <b>ALPINE/USDT</b>
💰 Price: {self.format_price(price_24h)}

{emoji_24h} 24h Change: {sign_24h}{change_24h:.2f}%
{emoji_1m} 1min Change: {sign_1m}{candle_change:.2f}%
📈 Volume: {candle_data['volume']:,.0f}

🚨 Alert Threshold: ±{self.threshold}%
🕒 Time: {datetime.now().strftime("%H:%M:%S")}"""
        
        await self.send_telegram(message)
        logger.info(f"📊 Status report sent - 24h: {sign_24h}{change_24h:.2f}%, 1m: {sign_1m}{candle_change:.2f}%")
    
    async def check_candle_alerts(self):
        """چک کردن کندل برای هشدار"""
        candle_data = await self.get_1min_candle()
        if candle_data:
            await self.send_pump_dump_alert(candle_data)
    
    async def run(self):
        """اجرای اصلی"""
        await self.init_session()
        logger.info("🪙 ALPINE Monitor started!")
        
        # ارسال پیام شروع
        await self.send_telegram("🪙 ALPINE Monitor started!\n⚡ 1min candle alerts (±1%)\n📊 Status reports every 5min")
        
        retry_count = 0
        max_retries = 3
        minute_counter = 0
        
        try:
            while True:
                try:
                    # هر دقیقه چک کردن کندل برای هشدار
                    await self.check_candle_alerts()
                    minute_counter += 1
                    
                    # هر 5 دقیقه ارسال گزارش وضعیت
                    if minute_counter >= 5:
                        await self.send_status_report()
                        minute_counter = 0
                    
                    retry_count = 0  # Reset counter on success
                    logger.info(f"✅ Check completed. Next in 1min (Report in {5-minute_counter}min)")
                    await asyncio.sleep(60)  # 1 دقیقه
                    
                except Exception as e:
                    retry_count += 1
                    logger.error(f"❌ Error {retry_count}/{max_retries}: {e}")
                    
                    if retry_count >= max_retries:
                        logger.error("Max retries reached. Waiting longer...")
                        await self.send_telegram(f"🚨 ALPINE Monitor having issues. Will retry in 5 minutes.")
                        await asyncio.sleep(300)  # 5 minutes
                        retry_count = 0
                        minute_counter = 0
                    else:
                        await asyncio.sleep(30)  # Wait 30s before retry
                        
        except KeyboardInterrupt:
            logger.info("Stopping...")
        except Exception as e:
            logger.error(f"خطای کلی: {e}")
            await self.send_telegram(f"🚨 ALPINE Monitor Critical Error: {str(e)[:100]}")
        finally:
            await self.close_session()
            logger.info("Monitor stopped")

# برای Render.com
from aiohttp import web

async def health_handler(request):
    return web.json_response({"status": "ALPINE Monitor OK"})

async def init_bot(app):
    monitor = AlpineMonitor()
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
    
    logger.info(f"Starting ALPINE Monitor on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
