import asyncio
import aiohttp
import os
from datetime import datetime
import logging
import json
from typing import List, Dict, Optional
from aiohttp import web
import time

# تنظیم لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# تنظیمات بات
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.getenv('CHAT_ID', 'YOUR_CHAT_ID_HERE')

class ProfessionalCryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0  # 4% برای پامپ
        self.dump_threshold = -4.0  # -4% برای دامپ
        self.binance_base_url = "https://api.binance.com"
        self.processed_symbols = set()  # جلوگیری از spam
        self.last_check_time = {}  # آخرین بار چک شدن هر سیمبل
        self.kline_interval = "1m"  # کندل 1 دقیقه‌ای
        
    async def init_session(self):
        """شروع HTTP session"""
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=30)
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={'User-Agent': 'CryptoPumpMonitor/1.0'}
        )
        logger.info("🚀 HTTP Session initialized")
    
    async def close_session(self):
        """بستن session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("❌ Session closed")
    
    async def get_all_usdt_symbols(self) -> List[str]:
        """دریافت همه سیمبل‌های USDT از بایننس"""
        try:
            url = f"{self.binance_base_url}/api/v3/exchangeInfo"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    symbols = []
                    
                    for symbol_info in data['symbols']:
                        symbol = symbol_info['symbol']
                        status = symbol_info['status']
                        
                        # فقط جفت ارزهای USDT که فعال هستند
                        if (symbol.endswith('USDT') and 
                            status == 'TRADING' and 
                            symbol_info['quoteAsset'] == 'USDT'):
                            symbols.append(symbol)
                    
                    logger.info(f"✅ پیدا شد: {len(symbols)} جفت ارز USDT فعال")
                    return symbols
                else:
                    logger.error(f"❌ خطا در دریافت symbols: {response.status}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ خطا در get_all_usdt_symbols: {e}")
            return []
    
    async def get_kline_data(self, symbol: str) -> Optional[Dict]:
        """دریافت آخرین کندل 1 دقیقه‌ای"""
        try:
            url = f"{self.binance_base_url}/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': self.kline_interval,
                'limit': 2  # آخرین کندل + کندل قبلی
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if len(data) >= 2:
                        # کندل قبلی (تمام شده)
                        prev_kline = data[-2]
                        current_kline = data[-1]
                        
                        prev_close = float(prev_kline[4])
                        current_close = float(current_kline[4])
                        current_open = float(current_kline[1])
                        current_high = float(current_kline[2])
                        current_low = float(current_kline[3])
                        volume = float(current_kline[5])
                        
                        # محاسبه درصد تغییر کندل فعلی
                        if current_open > 0:
                            candle_change = ((current_close - current_open) / current_open) * 100
                        else:
                            candle_change = 0
                        
                        # محاسبه درصد تغییر کل
                        if prev_close > 0:
                            total_change = ((current_close - prev_close) / prev_close) * 100
                        else:
                            total_change = 0
                        
                        return {
                            'symbol': symbol,
                            'open': current_open,
                            'high': current_high,
                            'low': current_low,
                            'close': current_close,
                            'volume': volume,
                            'candle_change': candle_change,
                            'total_change': total_change,
                            'prev_close': prev_close,
                            'timestamp': int(current_kline[0])
                        }
                elif response.status == 429:  # Rate limit
                    logger.warning(f"⚠️ Rate limit برای {symbol}")
                    await asyncio.sleep(1)
                    return None
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"❌ خطا در دریافت کندل {symbol}: {e}")
            return None
    
    async def send_telegram(self, message: str) -> bool:
        """ارسال پیام تلگرام"""
        try:
            if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE' or CHAT_ID == 'YOUR_CHAT_ID_HERE':
                logger.warning("⚠️ BOT_TOKEN یا CHAT_ID تنظیم نشده!")
                logger.info(f"📝 TEST MESSAGE: {message[:100]}...")
                return False
                
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            async with self.session.post(url, json=data) as response:
                if response.status == 200:
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"❌ خطا در ارسال تلگرام: {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ خطا در send_telegram: {e}")
            return False
    
    def format_number(self, num: float) -> str:
        """فرمت کردن اعداد"""
        if num >= 1:
            return f"{num:.4f}"
        else:
            return f"{num:.8f}"
    
    def get_market_cap_rank_emoji(self, symbol: str) -> str:
        """تخمین رنک بازار بر اساس سیمبل (تقریبی)"""
        top_coins = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 
                    'SOLUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT']
        
        if symbol in top_coins[:3]:
            return "👑"  # TOP 3
        elif symbol in top_coins[:10]:
            return "🥇"  # TOP 10
        elif symbol in top_coins:
            return "🏆"  # TOP coins
        else:
            return "🚀"  # Other coins
    
    async def check_all_symbols(self):
        """بررسی همه سیمبل‌ها برای پامپ/دامپ"""
        symbols = await self.get_all_usdt_symbols()
        if not symbols:
            logger.error("❌ هیچ سیمبلی دریافت نشد!")
            return
        
        logger.info(f"🔍 شروع بررسی {len(symbols)} جفت ارز...")
        
        # بررسی به صورت batch برای جلوگیری از rate limit
        batch_size = 50
        pumps_found = 0
        dumps_found = 0
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            tasks = [self.get_kline_data(symbol) for symbol in batch]
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception):
                        continue
                    
                    if result is None:
                        continue
                    
                    symbol = result['symbol']
                    candle_change = result['candle_change']
                    total_change = result['total_change']
                    
                    # چک پامپ (کندل فعلی بالای 4%)
                    if candle_change >= self.pump_threshold:
                        await self.send_pump_alert(result)
                        pumps_found += 1
                        
                    # چک دامپ (کندل فعلی زیر -4%)
                    elif candle_change <= self.dump_threshold:
                        await self.send_dump_alert(result)
                        dumps_found += 1
                
                # وقفه بین batch ها
                if i + batch_size < len(symbols):
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                logger.error(f"❌ خطا در پردازش batch: {e}")
        
        current_time = datetime.now().strftime("%H:%M:%S")
        logger.info(f"✅ اسکن تمام شد | پامپ: {pumps_found} | دامپ: {dumps_found} | زمان: {current_time}")
        
        # اگر تعداد زیادی پامپ/دامپ بود، یه خلاصه بفرست
        if pumps_found + dumps_found >= 5:
            summary = f"""
📊 <b>خلاصه اسکن {current_time}</b>

🚀 پامپ‌های +4%: {pumps_found}
📉 دامپ‌های -4%: {dumps_found}
🔍 کل بررسی شده: {len(symbols)}

#summary #scan
            """
            await self.send_telegram(summary)
    
    async def send_pump_alert(self, data: Dict):
        """ارسال هشدار پامپ"""
        symbol = data['symbol']
        candle_change = data['candle_change']
        total_change = data['total_change']
        volume = data['volume']
        current_price = data['close']
        
        emoji = self.get_market_cap_rank_emoji(symbol)
        coin_name = symbol.replace('USDT', '')
        
        message = f"""
{emoji} <b>PUMP ALERT!</b>

💰 <b>Coin:</b> #{coin_name}
📊 <b>Symbol:</b> {symbol}
🕯️ <b>Candle:</b> +{candle_change:.2f}%
📈 <b>Total Change:</b> {total_change:+.2f}%
💵 <b>Price:</b> ${self.format_number(current_price)}
📊 <b>Volume:</b> {volume:,.0f}
🕐 <b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

#pump #alert #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"🚀 پامپ ارسال شد: {symbol} +{candle_change:.2f}%")
    
    async def send_dump_alert(self, data: Dict):
        """ارسال هشدار دامپ"""
        symbol = data['symbol']
        candle_change = data['candle_change']
        total_change = data['total_change']
        volume = data['volume']
        current_price = data['close']
        
        emoji = self.get_market_cap_rank_emoji(symbol)
        coin_name = symbol.replace('USDT', '')
        
        message = f"""
📉 <b>DUMP ALERT!</b>

💰 <b>Coin:</b> #{coin_name}
📊 <b>Symbol:</b> {symbol}
🕯️ <b>Candle:</b> {candle_change:.2f}%
📉 <b>Total Change:</b> {total_change:+.2f}%
💵 <b>Price:</b> ${self.format_number(current_price)}
📊 <b>Volume:</b> {volume:,.0f}
🕐 <b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

#dump #alert #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"📉 دامپ ارسال شد: {symbol} {candle_change:.2f}%")
    
    async def send_startup_message(self):
        """پیام شروع بات"""
        current_time = datetime.now().strftime("%H:%M:%S - %Y/%m/%d")
        message = f"""
🤖 <b>Professional Crypto Monitor Started!</b>

🕐 <b>Start Time:</b> {current_time}
📊 <b>Monitoring:</b> All USDT pairs
🎯 <b>Pump Threshold:</b> +{self.pump_threshold}%
📉 <b>Dump Threshold:</b> {self.dump_threshold}%
🕯️ <b>Candle Interval:</b> {self.kline_interval}
🔄 <b>Check Interval:</b> 1 minute

<b>✅ Bot is now monitoring all available cryptocurrencies!</b>

#start #monitoring #professional
        """
        
        success = await self.send_telegram(message)
        if success:
            logger.info("🎉 پیام شروع ارسال شد!")
        return success
    
    async def run(self):
        """اجرای اصلی بات"""
        await self.init_session()
        logger.info("🤖 Professional Crypto Monitor Starting...")
        
        # ارسال پیام شروع
        await self.send_startup_message()
        
        # اجرای اصلی
        try:
            while self.running:
                start_time = time.time()
                
                await self.check_all_symbols()
                
                # محاسبه زمان اجرا
                execution_time = time.time() - start_time
                logger.info(f"⏱️ زمان اجرا: {execution_time:.2f} ثانیه")
                
                # استراحت تا دقیقه بعد
                await asyncio.sleep(max(1, 60 - execution_time))
                
        except KeyboardInterrupt:
            logger.info("🛑 دریافت سیگنال توقف...")
        except Exception as e:
            logger.error(f"❌ خطای کلی: {e}")
            # ارسال پیام خطا
            error_msg = f"⚠️ خطا در بات: {str(e)[:200]}"
            await self.send_telegram(error_msg)
        finally:
            self.running = False
            await self.close_session()
            logger.info("✅ بات متوقف شد")

# Web Server برای deployment
async def home_handler(request):
    return web.Response(
        text="""🤖 Professional Crypto Pump/Dump Monitor
        
✅ Status: Active
📊 Monitoring: All USDT pairs on Binance
🎯 Threshold: ±4% candle changes
🔄 Interval: 1 minute checks
        
Bot is running and monitoring cryptocurrency markets!""",
        content_type='text/plain'
    )

async def health_handler(request):
    return web.json_response({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "crypto-monitor"
    })

async def stats_handler(request):
    """نمایش آمار کلی"""
    return web.json_response({
        "pump_threshold": 4.0,
        "dump_threshold": -4.0,
        "interval": "1m",
        "check_frequency": "60 seconds",
        "monitoring": "All USDT pairs"
    })

async def init_bot(app):
    """شروع بات در background"""
    logger.info("🚀 Starting background crypto monitor...")
    monitor = ProfessionalCryptoMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())

async def cleanup_bot(app):
    """تمیز کردن منابع"""
    if 'monitor_task' in app:
        app['monitor_task'].cancel()
        try:
            await app['monitor_task']
        except asyncio.CancelledError:
            logger.info("🛑 Monitor task cancelled")

def create_app():
    """ساخت وب اپلیکیشن"""
    app = web.Application()
    
    # Routes
    app.router.add_get('/', home_handler)
    app.router.add_get('/health', health_handler)
    app.router.add_get('/stats', stats_handler)
    
    # Events
    app.on_startup.append(init_bot)
    app.on_cleanup.append(cleanup_bot)
    
    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv('PORT', 8080))
    
    logger.info(f"🚀 Starting Professional Crypto Monitor on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
