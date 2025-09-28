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
        # تبدیل نمادها به فرمت کوین‌بیس
        self.symbols = [
            "PORT3-USD",
            "KAITO-USD", 
            "AEVO-USD",
            "COAI-USD"
        ]
        self.threshold = 1.0  # 1% threshold for alerts
        self.base_url = "https://api.exchange.coinbase.com"
        
    async def init_session(self):
        """شروع session"""
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={
                'User-Agent': 'MultiCoinMonitor/1.0',
                'Accept': 'application/json'
            }
        )
        logger.info("Session started")
    
    async def close_session(self):
        """بستن session"""
        if self.session:
            await self.session.close()
            logger.info("Session closed")
    
    async def get_1min_candle(self, symbol):
        """گرفتن کندل 1 دقیقه ای از کوین‌بیس"""
        try:
            # استفاده از Coinbase Pro API برای کندل‌ها
            url = f"{self.base_url}/products/{symbol}/candles"
            params = {
                'start': (datetime.now().timestamp() - 180),  # 3 دقیقه قبل
                'end': datetime.now().timestamp(),
                'granularity': 60  # 60 ثانیه (1 دقیقه)
            }
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    if len(data) >= 2:
                        # کوین‌بیس کندل‌ها را به صورت [timestamp, low, high, open, close, volume] برمی‌گرداند
                        current_candle = data[-1]  # آخرین کندل
                        prev_candle = data[-2]     # کندل قبلی
                        
                        open_price = float(current_candle[3])   # open
                        close_price = float(current_candle[4])  # close
                        prev_close = float(prev_candle[4])      # close قبلی
                        
                        # محاسبه تغییر کندل
                        if open_price > 0:
                            candle_change = ((close_price - open_price) / open_price) * 100
                        else:
                            candle_change = 0
                        
                        return {
                            'symbol': symbol,
                            'candle_change': candle_change,
                            'price': close_price
                        }
                    else:
                        logger.warning(f"نداده کافی برای {symbol}")
                        return None
                        
                else:
                    logger.error(f"خطای HTTP {response.status} برای {symbol}")
                    return None
                        
        except Exception as e:
            logger.error(f"خطا در گرفتن کندل {symbol}: {e}")
        return None
    
    async def get_24h_stats(self, symbol):
        """گرفتن آمار 24 ساعته"""
        try:
            url = f"{self.base_url}/products/{symbol}/stats"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'volume': float(data.get('volume', 0)),
                        'high': float(data.get('high', 0)),
                        'low': float(data.get('low', 0)),
                        'last': float(data.get('last', 0))
                    }
        except Exception as e:
            logger.error(f"خطا در گرفتن آمار {symbol}: {e}")
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
    
    async def send_alert(self, coin_name, change, price=None):
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
        
        price_info = f"\n💰 Price: ${price:.4f}" if price else ""
        
        message = f"""{emoji} <b>{alert_type}</b>

{coin_name}: {sign}{change:.2f}%{price_info}
🕒 {datetime.now().strftime("%H:%M:%S")}
📊 Exchange: Coinbase"""
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"Alert sent: {coin_name} {sign}{change:.2f}%")
    
    async def send_status_report(self):
        """گزارش وضعیت ساده"""
        report_lines = ["📊 <b>Status Report</b> (Coinbase)\n"]
        
        for symbol in self.symbols:
            candle_data = await self.get_1min_candle(symbol)
            if candle_data:
                coin_name = symbol.replace('-USD', '')
                change = candle_data['candle_change']
                price = candle_data.get('price', 0)
                
                if change > 0:
                    emoji = "🟢"
                    sign = "+"
                else:
                    emoji = "🔴"
                    sign = ""
                
                report_lines.append(f"{emoji} {coin_name}: {sign}{change:.2f}% (${price:.4f})")
            else:
                coin_name = symbol.replace('-USD', '')
                report_lines.append(f"⚠️ {coin_name}: Data unavailable")
        
        report_lines.append(f"\n🕒 {datetime.now().strftime('%H:%M:%S')}")
        report_lines.append("📊 Exchange: Coinbase")
        message = "\n".join(report_lines)
        
        await self.send_telegram(message)
        logger.info("Status report sent")
    
    async def check_all_coins(self):
        """چک کردن همه ارزها"""
        for symbol in self.symbols:
            candle_data = await self.get_1min_candle(symbol)
            if candle_data:
                coin_name = symbol.replace('-USD', '')
                change = candle_data['candle_change']
                price = candle_data.get('price')
                await self.send_alert(coin_name, change, price)
            
            # فاصله کوتاه بین درخواست‌ها برای جلوگیری از rate limiting
            await asyncio.sleep(1)
    
    async def run(self):
        """اجرای اصلی"""
        await self.init_session()
        logger.info("Multi-Coin Monitor started with Coinbase API!")
        
        # پیام شروع
        coin_list = ", ".join([s.replace('-USD', '') for s in self.symbols])
        await self.send_telegram(f"""🤖 <b>Multi-Coin Monitor Started!</b>

📊 Exchange: Coinbase
💰 Coins: {coin_list}
📈 Threshold: ±{self.threshold}%
⏰ Reports every 5min
🔄 Check interval: 1min""")
        
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
                        await self.send_telegram("🚨 Monitor having issues with Coinbase API. Will retry in 5 minutes.")
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
    return web.json_response({"status": "Multi-Coin Monitor OK - Coinbase API"})

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
    
    logger.info(f"Starting Multi-Coin Monitor (Coinbase) on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
