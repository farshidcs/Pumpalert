import asyncio
import aiohttp
import os
from datetime import datetime, timedelta
import logging
import json
from typing import List, Dict, Optional
from aiohttp import web
import time
import sys

# حل مشکل کدگذاری
os.environ['PYTHONIOENCODING'] = 'utf-8'

# تنظیم لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# تنظیمات بات
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
CHAT_ID = os.getenv('CHAT_ID', 'YOUR_CHAT_ID_HERE')

class ProfessionalCryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0  # 4% برای پامپ فوری
        self.dump_threshold = -4.0  # -4% برای دامپ فوری
        self.binance_base_url = "https://api.binance.com"
        self.kline_interval = "1m"  # کندل 1 دقیقه‌ای
        self.last_report_time = 0  # آخرین زمان گزارش 30 دقیقه‌ای
        self.symbols_cache = []  # کش برای symbols
        self.last_symbols_fetch = 0  # آخرین زمان دریافت symbols
        
    async def init_session(self):
        """شروع HTTP session"""
        # تنظیمات بهتر برای جلوگیری از محدودیت
        connector = aiohttp.TCPConnector(
            limit=50, 
            limit_per_host=20,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=60
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        # User-Agent بهتر
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9',
            'Cache-Control': 'no-cache'
        }
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers
        )
        logger.info("HTTP Session initialized with better headers")
    
    async def close_session(self):
        """بستن session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")
    
    async def test_binance_connection(self):
        """تست اتصال به Binance"""
        try:
            url = f"{self.binance_base_url}/api/v3/ping"
            async with self.session.get(url) as response:
                if response.status == 200:
                    logger.info("✅ Binance connection test successful")
                    return True
                else:
                    logger.error(f"❌ Binance connection test failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"❌ Binance connection test error: {e}")
            return False
    
    async def get_all_usdt_symbols(self) -> List[str]:
        """دریافت همه سیمبل‌های USDT از بایننس"""
        # اگه symbols کش شده و کمتر از 1 ساعت گذشته، از کش استفاده کن
        current_time = time.time()
        if (self.symbols_cache and 
            current_time - self.last_symbols_fetch < 3600):  # 1 ساعت
            logger.info(f"Using cached symbols: {len(self.symbols_cache)} pairs")
            return self.symbols_cache
            
        try:
            # تست اتصال اول
            connection_ok = await self.test_binance_connection()
            if not connection_ok:
                if self.symbols_cache:  # اگه کش داریم از اون استفاده کن
                    logger.warning("Using cached symbols due to connection issue")
                    return self.symbols_cache
                else:
                    return []
            
            url = f"{self.binance_base_url}/api/v3/exchangeInfo"
            
            # اضافه کردن وقفه برای جلوگیری از rate limit
            await asyncio.sleep(0.5)
            
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
                    
                    self.symbols_cache = symbols
                    self.last_symbols_fetch = current_time
                    
                    logger.info(f"✅ Found: {len(symbols)} active USDT pairs")
                    return symbols
                    
                elif response.status == 451:
                    logger.error("❌ Error 451: Request blocked - IP might be restricted")
                    logger.info("💡 Try using a VPN or wait 30 minutes")
                    
                    # اگه کش داریم از اون استفاده کن
                    if self.symbols_cache:
                        logger.info("Using cached symbols due to 451 error")
                        return self.symbols_cache
                    else:
                        # fallback symbols - فقط معروف‌ترین ارزها
                        fallback_symbols = [
                            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT',
                            'SOLUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT',
                            'AVAXUSDT', 'LINKUSDT', 'UNIUSDT', 'ATOMUSDT', 'NEARUSDT'
                        ]
                        logger.info(f"Using fallback symbols: {len(fallback_symbols)} pairs")
                        return fallback_symbols
                        
                else:
                    logger.error(f"❌ Error getting symbols: {response.status}")
                    error_text = await response.text()
                    logger.error(f"Error details: {error_text[:200]}...")
                    
                    # fallback
                    if self.symbols_cache:
                        return self.symbols_cache
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error in get_all_usdt_symbols: {e}")
            
            # اگه کش داریم از اون استفاده کن
            if self.symbols_cache:
                logger.info("Using cached symbols due to exception")
                return self.symbols_cache
            
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
            
            # وقفه کوچک برای rate limiting
            await asyncio.sleep(0.05)
            
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
                    logger.warning(f"⚠️ Rate limit for {symbol}")
                    await asyncio.sleep(2)
                    return None
                else:
                    return None
                    
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Timeout for {symbol}")
            return None
        except Exception as e:
            logger.error(f"❌ Error getting kline {symbol}: {e}")
            return None
    
    async def get_24h_change_data(self, symbols: List[str]) -> List[Dict]:
        """دریافت تغییرات 24 ساعته همه ارزها"""
        try:
            url = f"{self.binance_base_url}/api/v3/ticker/24hr"
            
            # وقفه قبل از درخواست مهم
            await asyncio.sleep(0.5)
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    # فیلتر کردن فقط USDT pairs
                    usdt_data = [
                        item for item in data 
                        if item['symbol'] in symbols
                    ]
                    logger.info(f"✅ Got 24h data for {len(usdt_data)} pairs")
                    return usdt_data
                else:
                    logger.error(f"❌ Error getting 24h data: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"❌ Error in get_24h_change_data: {e}")
            return []
    
    async def send_telegram(self, message: str) -> bool:
        """ارسال پیام تلگرام"""
        try:
            if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE' or CHAT_ID == 'YOUR_CHAT_ID_HERE':
                logger.warning("⚠️ BOT_TOKEN or CHAT_ID not set!")
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
                    logger.error(f"❌ Telegram error: {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error in send_telegram: {e}")
            return False
    
    def format_number(self, num: float) -> str:
        """فرمت کردن اعداد"""
        if num >= 1:
            return f"{num:.4f}"
        else:
            return f"{num:.8f}"
    
    def get_market_cap_rank_emoji(self, symbol: str) -> str:
        """تخمین رنک بازار بر اساس سیمبل"""
        top_coins = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 
                    'SOLUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT']
        
        if symbol in top_coins[:3]:
            return "TOP3"  # TOP 3
        elif symbol in top_coins[:10]:
            return "TOP10"  # TOP 10
        elif symbol in top_coins:
            return "MAJOR"  # TOP coins
        else:
            return "ALT"  # Other coins
    
    async def check_instant_moves(self, symbols: List[str]) -> tuple:
        """بررسی حرکات فوری (4%+ در یک کندل)"""
        pumps_found = 0
        dumps_found = 0
        
        if not symbols:
            logger.warning("⚠️ No symbols to check")
            return 0, 0
        
        # کاهش batch size برای جلوگیری از rate limit
        batch_size = 30
        
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
                    
                    # چک پامپ فوری (کندل فعلی بالای 4%)
                    if candle_change >= self.pump_threshold:
                        await self.send_pump_alert(result)
                        pumps_found += 1
                        
                    # چک دامپ فوری (کندل فعلی زیر -4%)
                    elif candle_change <= self.dump_threshold:
                        await self.send_dump_alert(result)
                        dumps_found += 1
                
                # وقفه بیشتر بین batch ها
                if i + batch_size < len(symbols):
                    await asyncio.sleep(1.0)  # 1 ثانیه وقفه
                    
            except Exception as e:
                logger.error(f"❌ Error processing batch: {e}")
                await asyncio.sleep(2.0)  # وقفه بیشتر در صورت خطا
        
        return pumps_found, dumps_found
    
    async def send_pump_alert(self, data: Dict):
        """ارسال هشدار پامپ فوری"""
        symbol = data['symbol']
        candle_change = data['candle_change']
        total_change = data['total_change']
        volume = data['volume']
        current_price = data['close']
        
        rank = self.get_market_cap_rank_emoji(symbol)
        coin_name = symbol.replace('USDT', '')
        
        message = f"""
<b>INSTANT PUMP ALERT!</b>

<b>Coin:</b> #{coin_name} ({rank})
<b>Symbol:</b> {symbol}
<b>1m Candle:</b> +{candle_change:.2f}%
<b>Total Change:</b> {total_change:+.2f}%
<b>Price:</b> ${self.format_number(current_price)}
<b>Volume:</b> {volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>1-minute candle moved above 4%!</b>

#pump #instant #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Pump alert sent: {symbol} +{candle_change:.2f}%")
    
    async def send_dump_alert(self, data: Dict):
        """ارسال هشدار دامپ فوری"""
        symbol = data['symbol']
        candle_change = data['candle_change']
        total_change = data['total_change']
        volume = data['volume']
        current_price = data['close']
        
        rank = self.get_market_cap_rank_emoji(symbol)
        coin_name = symbol.replace('USDT', '')
        
        message = f"""
<b>INSTANT DUMP ALERT!</b>

<b>Coin:</b> #{coin_name} ({rank})
<b>Symbol:</b> {symbol}
<b>1m Candle:</b> {candle_change:.2f}%
<b>Total Change:</b> {total_change:+.2f}%
<b>Price:</b> ${self.format_number(current_price)}
<b>Volume:</b> {volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>1-minute candle moved below -4%!</b>

#dump #instant #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Dump alert sent: {symbol} {candle_change:.2f}%")
    
    async def send_30min_report(self, symbols: List[str]):
        """گزارش هر 30 دقیقه"""
        try:
            current_time = datetime.now().strftime("%H:%M - %d/%m")
            
            # دریافت تغییرات 24 ساعته
            daily_data = await self.get_24h_change_data(symbols)
            
            # پیدا کردن ارزهای بالای 20% رشد روزانه
            daily_gainers = []
            daily_losers = []
            
            for item in daily_data:
                try:
                    change = float(item['priceChangePercent'])
                    if change >= 20:
                        daily_gainers.append({
                            'symbol': item['symbol'],
                            'change': change,
                            'price': float(item['lastPrice']),
                            'volume': float(item['volume'])
                        })
                    elif change <= -20:
                        daily_losers.append({
                            'symbol': item['symbol'],
                            'change': change,
                            'price': float(item['lastPrice']),
                            'volume': float(item['volume'])
                        })
                except (ValueError, KeyError):
                    continue
            
            # مرتب‌سازی
            daily_gainers.sort(key=lambda x: x['change'], reverse=True)
            daily_losers.sort(key=lambda x: x['change'])
            
            # ساخت پیام گزارش
            message = f"<b>30-MINUTE REPORT</b> | {current_time}\n\n"
            
            # رشدهای روزانه بالای 20%
            if daily_gainers:
                message += "<b>Daily Gains +20%:</b>\n"
                for i, coin in enumerate(daily_gainers[:5]):  # فقط 5 تای اول
                    coin_name = coin['symbol'].replace('USDT', '')
                    message += f"{i+1}. #{coin_name}: +{coin['change']:.1f}%\n"
                message += "\n"
            
            # ریزش‌های روزانه زیر -20%
            if daily_losers:
                message += "<b>Daily Losses -20%:</b>\n"
                for i, coin in enumerate(daily_losers[:3]):  # فقط 3 تای اول
                    coin_name = coin['symbol'].replace('USDT', '')
                    message += f"{i+1}. #{coin_name}: {coin['change']:.1f}%\n"
                message += "\n"
            
            # اگر هیچ حرکت خاصی نبود
            if not daily_gainers and not daily_losers:
                message += "<b>Quiet Market:</b>\n"
                message += "• No +20% daily gains/losses\n"
                message += "• Market is consolidating\n\n"
            
            message += f"<b>Monitored:</b> {len(symbols)} pairs\n"
            message += f"<b>Next Report:</b> {(datetime.now() + timedelta(minutes=30)).strftime('%H:%M')}\n\n"
            message += "#report #30min #summary"
            
            # ارسال گزارش
            success = await self.send_telegram(message)
            if success:
                logger.info(f"📊 30min report sent | Gains: {len(daily_gainers)} | Losses: {len(daily_losers)}")
            
        except Exception as e:
            logger.error(f"❌ Error in send_30min_report: {e}")
    
    async def send_startup_message(self):
        """پیام شروع بات"""
        current_time = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        symbols = await self.get_all_usdt_symbols()
        symbol_count = len(symbols) if symbols else 0
        
        message = f"""
<b>Professional Crypto Monitor Started!</b>

<b>Start Time:</b> {current_time}
<b>Monitoring:</b> {symbol_count} USDT pairs
<b>Instant Alerts:</b> ±4% candle moves
<b>30min Reports:</b> +20% daily changes
<b>Candle:</b> 1 minute
<b>Check:</b> Every 1.5 minutes

<b>Bot is now actively monitoring!</b>

#start #monitoring #professional
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info("✅ Startup message sent!")
        return success
    
    async def run(self):
        """اجرای اصلی بات"""
        await self.init_session()
        logger.info("Professional Crypto Monitor Starting...")
        
        # تست اتصال اول
        connection_ok = await self.test_binance_connection()
        if not connection_ok:
            logger.error("❌ Cannot connect to Binance API!")
            logger.info("💡 Solutions:")
            logger.info("1. Check your internet connection")
            logger.info("2. Try using a VPN")
            logger.info("3. Wait 30 minutes and try again")
            return
        
        # ارسال پیام شروع
        startup_success = await self.send_startup_message()
        if not startup_success:
            logger.warning("⚠️ Startup message failed")
        
        # متغیرها
        self.last_report_time = time.time()
        total_scans = 0
        consecutive_errors = 0
        
        try:
            while self.running:
                start_time = time.time()
                
                try:
                    # دریافت همه symbols
                    symbols = await self.get_all_usdt_symbols()
                    if not symbols:
                        consecutive_errors += 1
                        logger.error(f"❌ No symbols received! Error #{consecutive_errors}")
                        
                        if consecutive_errors >= 5:
                            logger.error("❌ Too many errors, stopping...")
                            break
                        
                        await asyncio.sleep(120)  # 2 دقیقه استراحت
                        continue
                    
                    # Reset error counter
                    consecutive_errors = 0
                    
                    # بررسی حرکات فوری (4%+ کندل)
                    pumps, dumps = await self.check_instant_moves(symbols)
                    
                    total_scans += 1
                    current_time = datetime.now().strftime("%H:%M:%S")
                    logger.info(f"Scan #{total_scans} | Pairs: {len(symbols)} | Pumps: {pumps} | Dumps: {dumps} | {current_time}")
                    
                    # گزارش 30 دقیقه‌ای
                    if time.time() - self.last_report_time >= 1800:  # 30 دقیقه
                        await self.send_30min_report(symbols)
                        self.last_report_time = time.time()
                    
                except Exception as scan_error:
                    consecutive_errors += 1
                    logger.error(f"❌ Scan error: {scan_error} (#{consecutive_errors})")
                    
                    if consecutive_errors >= 10:
                        logger.error("❌ Too many errors, stopping...")
                        break
                
                # محاسبه زمان اجرا و استراحت
                execution_time = time.time() - start_time
                sleep_time = max(15, 90 - execution_time)  # حداقل 15 ثانیه، هدف 90 ثانیه
                
                logger.info(f"Execution: {execution_time:.2f}s | Sleep: {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Stop signal received...")
        except Exception as e:
            logger.error(f"❌ Critical error: {e}")
            error_msg = f"Bot critical error: {str(e)[:200]}"
            await self.send_telegram(error_msg)
        finally:
            self.running = False
            await self.close_session()
            logger.info("Bot stopped")

# Web Server برای deployment
async def home_handler(request):
    return web.Response(
        text="""Professional Crypto Pump/Dump Monitor
        
Status: Active
Instant Alerts: ±4% candle moves  
30min Reports: +20% daily changes
Check Interval: 1.5 minutes
        
Bot is monitoring all cryptocurrencies!""",
        content_type='text/plain'
    )

async def health_handler(request):
    return web.json_response({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "crypto-monitor-pro"
    })

async def stats_handler(request):
    return web.json_response({
        "instant_threshold": "±4%",
        "report_threshold": "±20%",
        "candle_interval": "1m",
        "report_interval": "30min",
        "monitoring": "All USDT pairs"
    })

async def init_bot(app):
    """شروع بات در background"""
    logger.info("Starting Professional Crypto Monitor...")
    monitor = ProfessionalCryptoMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())

async def cleanup_bot(app):
    """تمیز کردن منابع"""
    if 'monitor_task' in app:
        app['monitor_task'].cancel()
        try:
            await app['monitor_task']
        except asyncio.CancelledError:
            logger.info("Monitor task cancelled")

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
    
    logger.info(f"Starting Professional Crypto Monitor on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
