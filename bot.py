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
import hmac
import hashlib

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

# تنظیمات BitUnix API
BITUNIX_API_KEY = os.getenv('BITUNIX_API_KEY', 'b948c60da5436f3030a0f502f71fa11b')
BITUNIX_SECRET_KEY = os.getenv('BITUNIX_SECRET_KEY', 'ff27796f41c323d2309234350d50135e')

class BitUnixCryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0  # 4% برای پامپ فوری
        self.dump_threshold = -4.0  # -4% برای دامپ فوری
        self.daily_threshold = 20.0  # 20% برای گزارش روزانه
        
        # BitUnix API URLs
        self.bitunix_base_url = "https://open-api.bitunix.com"
        
        # Cache
        self.symbols_list = []
        self.last_symbols_fetch = 0
        self.price_history = {}  # نگهداری قیمت‌های قبلی برای محاسبه تغییرات
        self.last_report_time = 0
        self.last_30min_prices = {}
        
    def generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """تولید امضای BitUnix"""
        try:
            # ساخت پیام برای امضا
            message = f"{timestamp}{method}{path}{body}"
            
            # تولید امضا
            signature = hmac.new(
                BITUNIX_SECRET_KEY.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return signature
        except Exception as e:
            logger.error(f"Error generating signature: {e}")
            return ""
    
    async def init_session(self):
        """شروع HTTP session"""
        connector = aiohttp.TCPConnector(
            limit=30,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
        logger.info("HTTP Session initialized for BitUnix API")
    
    async def close_session(self):
        """بستن session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")
    
    async def make_request(self, method: str, path: str, params: dict = None, private: bool = False) -> dict:
        """درخواست عمومی به BitUnix API"""
        try:
            url = f"{self.bitunix_base_url}{path}"
            headers = {
                'Content-Type': 'application/json'
            }
            
            if private:
                timestamp = str(int(time.time() * 1000))
                headers.update({
                    'ACCESS-KEY': BITUNIX_API_KEY,
                    'ACCESS-TIMESTAMP': timestamp,
                    'ACCESS-SIGN': self.generate_signature(timestamp, method, path)
                })
            
            await asyncio.sleep(0.1)  # Rate limiting
            
            if method == 'GET':
                async with self.session.get(url, headers=headers, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API Error: {response.status} - {await response.text()}")
                        return {}
            
        except Exception as e:
            logger.error(f"Request error: {e}")
            return {}
    
    async def test_bitunix_connection(self):
        """تست اتصال به BitUnix"""
        try:
            # تست با endpoint ساده
            result = await self.make_request('GET', '/api/spot/v1/market/symbols')
            if result and 'data' in result:
                logger.info("BitUnix API connection successful")
                return True
            else:
                logger.error("BitUnix API connection failed")
                return False
        except Exception as e:
            logger.error(f"BitUnix connection test error: {e}")
            return False
    
    async def get_all_symbols(self) -> List[Dict]:
        """دریافت تمام symbols"""
        current_time = time.time()
        
        # اگه کمتر از 30 دقیقه از آخرین fetch گذشته، از cache استفاده کن
        if (self.symbols_list and 
            current_time - self.last_symbols_fetch < 1800):  # 30 دقیقه
            logger.info(f"Using cached symbols: {len(self.symbols_list)} pairs")
            return self.symbols_list
        
        try:
            result = await self.make_request('GET', '/api/spot/v1/market/symbols')
            
            if result and 'data' in result:
                # فیلتر کردن فقط USDT pairs
                usdt_symbols = []
                for symbol_data in result['data']:
                    if (symbol_data.get('symbol', '').endswith('USDT') and 
                        symbol_data.get('status') == 'TRADING'):
                        usdt_symbols.append(symbol_data)
                
                self.symbols_list = usdt_symbols
                self.last_symbols_fetch = current_time
                
                logger.info(f"Fetched {len(usdt_symbols)} USDT pairs from BitUnix")
                return usdt_symbols
            else:
                logger.error("No symbols data received")
                return self.symbols_list if self.symbols_list else []
            
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
            return self.symbols_list if self.symbols_list else []
    
    async def get_kline_data(self, symbol: str) -> Optional[Dict]:
        """دریافت کندل 1 دقیقه‌ای"""
        try:
            params = {
                'symbol': symbol,
                'period': '1min',
                'size': 2  # آخرین 2 کندل
            }
            
            result = await self.make_request('GET', '/api/spot/v1/market/history/kline', params)
            
            if result and 'data' in result and len(result['data']) >= 2:
                klines = result['data']
                
                # آخرین کندل
                current_kline = klines[0]
                prev_kline = klines[1]
                
                current_open = float(current_kline['open'])
                current_close = float(current_kline['close'])
                current_high = float(current_kline['high'])
                current_low = float(current_kline['low'])
                current_volume = float(current_kline['vol'])
                
                prev_close = float(prev_kline['close'])
                
                # محاسبه تغییرات
                if current_open > 0:
                    candle_change = ((current_close - current_open) / current_open) * 100
                else:
                    candle_change = 0
                
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
                    'volume': current_volume,
                    'candle_change': candle_change,
                    'total_change': total_change,
                    'prev_close': prev_close,
                    'timestamp': current_kline['id']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"Error getting kline for {symbol}: {e}")
            return None
    
    async def get_24h_tickers(self) -> Dict:
        """دریافت تغییرات 24 ساعته"""
        try:
            result = await self.make_request('GET', '/api/spot/v1/market/tickers')
            
            if result and 'data' in result:
                tickers = {}
                for ticker in result['data']:
                    symbol = ticker.get('symbol')
                    if symbol and symbol.endswith('USDT'):
                        tickers[symbol] = {
                            'symbol': symbol,
                            'price': float(ticker.get('close', 0)),
                            'change_24h': float(ticker.get('chg', 0)),
                            'volume': float(ticker.get('vol', 0))
                        }
                
                logger.info(f"Got 24h data for {len(tickers)} pairs")
                return tickers
            
            return {}
            
        except Exception as e:
            logger.error(f"Error getting 24h tickers: {e}")
            return {}
    
    def get_coin_rank_category(self, symbol: str) -> str:
        """دسته‌بندی بر اساس symbol"""
        top_coins = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'XRPUSDT', 'ADAUSDT', 
                    'SOLUSDT', 'DOGEUSDT', 'DOTUSDT', 'MATICUSDT', 'LTCUSDT']
        
        if symbol in top_coins[:3]:
            return "TOP3"
        elif symbol in top_coins[:10]:
            return "TOP10"
        elif symbol in top_coins:
            return "MAJOR"
        else:
            return "ALT"
    
    def format_price(self, price: float) -> str:
        """فرمت کردن قیمت"""
        if price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"
    
    async def send_telegram(self, message: str) -> bool:
        """ارسال پیام تلگرام"""
        try:
            if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE' or CHAT_ID == 'YOUR_CHAT_ID_HERE':
                logger.warning("BOT_TOKEN or CHAT_ID not set!")
                logger.info(f"TEST MESSAGE: {message[:100]}...")
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
                    logger.error(f"Telegram error: {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error in send_telegram: {e}")
            return False
    
    async def check_instant_moves(self, symbols: List[Dict]) -> tuple:
        """بررسی حرکات فوری 4%+"""
        pumps_found = 0
        dumps_found = 0
        
        if not symbols:
            logger.warning("No symbols to check")
            return 0, 0
        
        # بررسی batch
        batch_size = 20
        
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            
            for symbol_data in batch:
                symbol = symbol_data.get('symbol')
                if not symbol:
                    continue
                
                kline_data = await self.get_kline_data(symbol)
                if not kline_data:
                    continue
                
                candle_change = kline_data['candle_change']
                
                # چک پامپ فوری
                if candle_change >= self.pump_threshold:
                    await self.send_pump_alert(kline_data)
                    pumps_found += 1
                
                # چک دامپ فوری
                elif candle_change <= self.dump_threshold:
                    await self.send_dump_alert(kline_data)
                    dumps_found += 1
            
            # وقفه بین batch ها
            if i + batch_size < len(symbols):
                await asyncio.sleep(2)
        
        return pumps_found, dumps_found
    
    async def send_pump_alert(self, kline_data: Dict):
        """ارسال هشدار پامپ"""
        symbol = kline_data['symbol']
        candle_change = kline_data['candle_change']
        total_change = kline_data['total_change']
        current_price = kline_data['close']
        volume = kline_data['volume']
        
        coin_name = symbol.replace('USDT', '')
        rank_category = self.get_coin_rank_category(symbol)
        
        message = f"""
<b>🚀 PUMP ALERT!</b>

<b>Coin:</b> #{coin_name} ({rank_category})
<b>Symbol:</b> {symbol}
<b>1m Candle:</b> <b>+{candle_change:.2f}%</b>
<b>Total Change:</b> {total_change:+.2f}%
<b>Price:</b> {self.format_price(current_price)}
<b>Volume:</b> {volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>🔥 1-minute candle moved above {self.pump_threshold}%!</b>

#pump #alert #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Pump alert sent: {symbol} +{candle_change:.2f}%")
    
    async def send_dump_alert(self, kline_data: Dict):
        """ارسال هشدار دامپ"""
        symbol = kline_data['symbol']
        candle_change = kline_data['candle_change']
        total_change = kline_data['total_change']
        current_price = kline_data['close']
        volume = kline_data['volume']
        
        coin_name = symbol.replace('USDT', '')
        rank_category = self.get_coin_rank_category(symbol)
        
        message = f"""
<b>📉 DUMP ALERT!</b>

<b>Coin:</b> #{coin_name} ({rank_category})
<b>Symbol:</b> {symbol}
<b>1m Candle:</b> <b>{candle_change:.2f}%</b>
<b>Total Change:</b> {total_change:+.2f}%
<b>Price:</b> {self.format_price(current_price)}
<b>Volume:</b> {volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>⚠️ 1-minute candle moved below {self.dump_threshold}%!</b>

#dump #alert #{coin_name.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Dump alert sent: {symbol} {candle_change:.2f}%")
    
    async def send_30min_report(self, symbols: List[Dict]):
        """گزارش هر 30 دقیقه"""
        try:
            current_time = datetime.now().strftime("%H:%M - %d/%m")
            
            # دریافت تیکرهای 24 ساعته
            tickers_24h = await self.get_24h_tickers()
            
            daily_gainers = []
            daily_losers = []
            recent_movers = []
            current_timestamp = time.time()
            
            for ticker_symbol, ticker_data in tickers_24h.items():
                change_24h = ticker_data['change_24h']
                current_price = ticker_data['price']
                
                # تغییرات روزانه بالای 20%
                if change_24h >= self.daily_threshold:
                    daily_gainers.append({
                        'symbol': ticker_symbol,
                        'change': change_24h,
                        'price': current_price,
                        'volume': ticker_data['volume']
                    })
                elif change_24h <= -self.daily_threshold:
                    daily_losers.append({
                        'symbol': ticker_symbol,
                        'change': change_24h,
                        'price': current_price,
                        'volume': ticker_data['volume']
                    })
                
                # چک تغییرات 30 دقیقه اخیر
                if ticker_symbol in self.last_30min_prices:
                    last_30min_price = self.last_30min_prices[ticker_symbol]['price']
                    time_diff = current_timestamp - self.last_30min_prices[ticker_symbol]['timestamp']
                    
                    if 1500 <= time_diff <= 2100:  # 25-35 دقیقه
                        if last_30min_price > 0:
                            change_30min = ((current_price - last_30min_price) / last_30min_price) * 100
                            
                            if abs(change_30min) >= self.daily_threshold:
                                recent_movers.append({
                                    'symbol': ticker_symbol,
                                    'change': change_30min,
                                    'price': current_price,
                                    'volume': ticker_data['volume']
                                })
                
                # بروزرسانی قیمت‌های 30 دقیقه قبل
                self.last_30min_prices[ticker_symbol] = {
                    'price': current_price,
                    'timestamp': current_timestamp
                }
            
            # مرتب‌سازی
            daily_gainers.sort(key=lambda x: x['change'], reverse=True)
            daily_losers.sort(key=lambda x: x['change'])
            recent_movers.sort(key=lambda x: abs(x['change']), reverse=True)
            
            # ساخت پیام گزارش
            message = f"<b>📊 30-MINUTE REPORT</b> | {current_time}\n\n"
            
            # رشدهای روزانه بالای 20%
            if daily_gainers:
                message += "<b>📈 Daily Gains +20%:</b>\n"
                for i, coin in enumerate(daily_gainers[:5]):
                    coin_name = coin['symbol'].replace('USDT', '')
                    rank_cat = self.get_coin_rank_category(coin['symbol'])
                    message += f"{i+1}. #{coin_name} ({rank_cat}): <b>+{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # ریزش‌های روزانه زیر -20%
            if daily_losers:
                message += "<b>📉 Daily Losses -20%:</b>\n"
                for i, coin in enumerate(daily_losers[:5]):
                    coin_name = coin['symbol'].replace('USDT', '')
                    rank_cat = self.get_coin_rank_category(coin['symbol'])
                    message += f"{i+1}. #{coin_name} ({rank_cat}): <b>{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # حرکات 30 دقیقه اخیر
            if recent_movers:
                message += "<b>⚡ 30-min Big Moves ±20%:</b>\n"
                for i, coin in enumerate(recent_movers[:3]):
                    coin_name = coin['symbol'].replace('USDT', '')
                    rank_cat = self.get_coin_rank_category(coin['symbol'])
                    sign = "+" if coin['change'] > 0 else ""
                    message += f"{i+1}. #{coin_name} ({rank_cat}): <b>{sign}{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # اگر هیچ حرکت خاصی نبود
            if not daily_gainers and not daily_losers and not recent_movers:
                message += "<b>😴 Quiet Market:</b>\n"
                message += "• No significant moves detected\n"
                message += "• Market consolidating\n\n"
            
            message += f"<b>📊 Monitored:</b> {len(symbols)} USDT pairs\n"
            message += f"<b>⏰ Next Report:</b> {(datetime.now() + timedelta(minutes=30)).strftime('%H:%M')}\n\n"
            message += "#report #30min #bitunix"
            
            # ارسال گزارش
            success = await self.send_telegram(message)
            if success:
                logger.info(f"📊 30min report sent | Gains: {len(daily_gainers)} | Losses: {len(daily_losers)} | Recent: {len(recent_movers)}")
            
        except Exception as e:
            logger.error(f"Error in send_30min_report: {e}")
    
    async def send_startup_message(self):
        """پیام شروع بات"""
        current_time = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        symbols = await self.get_all_symbols()
        symbol_count = len(symbols) if symbols else 0
        
        message = f"""
<b>🤖 BitUnix Crypto Monitor Started!</b>

<b>🕐 Start Time:</b> {current_time}
<b>📊 Monitoring:</b> {symbol_count} USDT pairs
<b>⚡ Instant Alerts:</b> ±{self.pump_threshold}% candle moves
<b>📈 30min Reports:</b> ±{self.daily_threshold}% daily/30min changes
<b>🕐 Candle:</b> 1 minute
<b>🔍 Check:</b> Every 2 minutes
<b>📡 Data Source:</b> BitUnix API

<b>✅ Bot is now actively monitoring!</b>

#start #monitoring #bitunix
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info("✅ Startup message sent!")
        return success
    
    async def run(self):
        """اجرای اصلی بات"""
        await self.init_session()
        logger.info("🚀 BitUnix Crypto Monitor Starting...")
        
        # تست اتصال
        connection_ok = await self.test_bitunix_connection()
        if not connection_ok:
            logger.error("❌ Cannot connect to BitUnix API!")
            logger.info("💡 Check your API credentials")
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
                    # دریافت symbols
                    symbols = await self.get_all_symbols()
                    if not symbols:
                        consecutive_errors += 1
                        logger.error(f"❌ No symbols received! Error #{consecutive_errors}")
                        
                        if consecutive_errors >= 5:
                            logger.error("❌ Too many errors, stopping...")
                            break
                        
                        await asyncio.sleep(120)
                        continue
                    
                    consecutive_errors = 0
                    
                    # بررسی حرکات فوری
                    pumps, dumps = await self.check_instant_moves(symbols)
                    
                    total_scans += 1
                    current_time_str = datetime.now().strftime("%H:%M:%S")
                    logger.info(f"📊 Scan #{total_scans} | Pairs: {len(symbols)} | 🚀Pumps: {pumps} | 📉Dumps: {dumps} | {current_time_str}")
                    
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
                
                # محاسبه زمان استراحت
                execution_time = time.time() - start_time
                sleep_time = max(30, 120 - execution_time)  # هدف 2 دقیقه، حداقل 30 ثانیه
                
                logger.info(f"⏱️ Execution: {execution_time:.2f}s | Sleep: {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("🛑 Stop signal received...")
        except Exception as e:
            logger.error(f"❌ Critical error: {e}")
            error_msg = f"🚨 Bot critical error: {str(e)[:200]}"
            await self.send_telegram(error_msg)
        finally:
            self.running = False
            await self.close_session()
            logger.info("🛑 Bot stopped")

# Web Server برای deployment
async def home_handler(request):
    return web.Response(
        text="""🤖 BitUnix Crypto Pump/Dump Monitor
        
Status: Active
Data Source: BitUnix API
Instant Alerts: ±4% candle moves  
30min Reports: ±20% daily/30min changes
Check Interval: 2 minutes
        
Bot is monitoring USDT pairs from BitUnix!""",
        content_type='text/plain'
    )

async def health_handler(request):
    return web.json_response({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "bitunix-crypto-monitor",
        "api_source": "BitUnix"
    })

async def stats_handler(request):
    return web.json_response({
        "instant_threshold": "±4%",
        "report_threshold": "±20%",
        "check_interval": "2 minutes",
        "report_interval": "30min",
        "monitoring": "USDT pairs",
        "api_source": "BitUnix"
    })

async def init_bot(app):
    """شروع بات در background"""
    logger.info("🚀 Starting BitUnix Crypto Monitor...")
    monitor = BitUnixCryptoMonitor()
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
    
    logger.info(f"🚀 Starting BitUnix Crypto Monitor on port {port}")
    logger.info(f"🔑 API Key: {BITUNIX_API_KEY[:10]}...")
    web.run_app(app, host='0.0.0.0', port=port)
