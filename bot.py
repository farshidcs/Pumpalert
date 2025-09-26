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
import random

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

# تنظیمات API Keys
BITUNIX_API_KEY = os.getenv('BITUNIX_API_KEY', 'b948c60da5436f3030a0f502f71fa11b')
BITUNIX_SECRET_KEY = os.getenv('BITUNIX_SECRET_KEY', 'ff27796f41c323d2309234350d50135e')

class MultiExchangeCryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0  # 4% برای پامپ فوری
        self.dump_threshold = -4.0  # -4% برای دامپ فوری
        self.daily_threshold = 20.0  # 20% برای گزارش روزانه
        
        # API URLs for different exchanges
        self.exchanges = {
            'binance': {
                'base_url': 'https://api.binance.com',
                'active': True,
                'backup_order': 1
            },
            'bybit': {
                'base_url': 'https://api.bybit.com',
                'active': True,
                'backup_order': 2
            },
            'kucoin': {
                'base_url': 'https://api.kucoin.com',
                'active': True,
                'backup_order': 3
            },
            'mexc': {
                'base_url': 'https://api.mexc.com',
                'active': True,
                'backup_order': 4
            },
            'coinex': {
                'base_url': 'https://api.coinex.com',
                'active': True,
                'backup_order': 5
            },
            'bitunix': {
                'base_url': 'https://open-api.bitunix.com',
                'active': True,
                'backup_order': 6
            }
        }
        
        self.current_exchange = None
        self.exchange_failures = {}
        
        # Cache
        self.symbols_list = []
        self.last_symbols_fetch = 0
        self.price_history = {}
        self.last_report_time = 0
        self.last_30min_prices = {}
        
    async def init_session(self):
        """شروع HTTP session"""
        connector = aiohttp.TCPConnector(
            limit=30,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
            ssl=False  # برای تست - در production True کنید
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive'
        }
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers
        )
        logger.info("HTTP Session initialized for multi-exchange monitoring")
    
    async def close_session(self):
        """بستن session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")
    
    async def test_network_connectivity(self):
        """تست اتصال شبکه"""
        try:
            # تست اتصال ساده
            async with self.session.get('https://httpbin.org/ip', timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    logger.info(f"✅ Network test successful, IP: {data.get('origin', 'unknown')}")
                    return True
        except Exception as e:
            logger.error(f"❌ Network test failed: {e}")
            return False
        
        return False
    
    async def test_exchange_connection(self, exchange_name: str) -> bool:
        """تست اتصال به صرافی خاص"""
        try:
            if exchange_name == 'binance':
                url = f"{self.exchanges[exchange_name]['base_url']}/api/v3/ping"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
                    
            elif exchange_name == 'bybit':
                url = f"{self.exchanges[exchange_name]['base_url']}/v2/public/time"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
                    
            elif exchange_name == 'kucoin':
                url = f"{self.exchanges[exchange_name]['base_url']}/api/v1/timestamp"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
                    
            elif exchange_name == 'mexc':
                url = f"{self.exchanges[exchange_name]['base_url']}/api/v3/ping"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
                    
            elif exchange_name == 'coinex':
                url = f"{self.exchanges[exchange_name]['base_url']}/v1/common/currency_rate"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
                    
            elif exchange_name == 'bitunix':
                url = f"{self.exchanges[exchange_name]['base_url']}/api/spot/v1/common/time"
                async with self.session.get(url, timeout=15) as response:
                    success = response.status == 200
            else:
                success = False
            
            if success:
                logger.info(f"✅ {exchange_name.upper()} API connection successful")
                self.exchange_failures[exchange_name] = 0
                return True
            else:
                logger.error(f"❌ {exchange_name.upper()} API connection failed")
                self.exchange_failures[exchange_name] = self.exchange_failures.get(exchange_name, 0) + 1
                return False
                
        except Exception as e:
            logger.error(f"❌ {exchange_name.upper()} connection test error: {e}")
            self.exchange_failures[exchange_name] = self.exchange_failures.get(exchange_name, 0) + 1
            return False
    
    async def find_working_exchange(self):
        """پیدا کردن صرافی که کار میکند"""
        logger.info("🔍 Searching for working exchange...")
        
        # مرتب کردن بر اساس backup_order
        sorted_exchanges = sorted(
            self.exchanges.items(),
            key=lambda x: (self.exchange_failures.get(x[0], 0), x[1]['backup_order'])
        )
        
        for exchange_name, exchange_info in sorted_exchanges:
            if not exchange_info['active']:
                continue
                
            logger.info(f"🧪 Testing {exchange_name.upper()}...")
            if await self.test_exchange_connection(exchange_name):
                self.current_exchange = exchange_name
                logger.info(f"✅ Using {exchange_name.upper()} as primary exchange")
                return True
            
            await asyncio.sleep(2)  # وقفه بین تست‌ها
        
        logger.error("❌ No working exchange found!")
        return False
    
    async def get_symbols_binance(self) -> List[Dict]:
        """دریافت symbols از Binance"""
        try:
            url = f"{self.exchanges['binance']['base_url']}/api/v3/exchangeInfo"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol in data.get('symbols', []):
                        if (symbol['symbol'].endswith('USDT') and 
                            symbol['status'] == 'TRADING'):
                            usdt_symbols.append({
                                'symbol': symbol['symbol'],
                                'status': symbol['status']
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting Binance symbols: {e}")
        return []
    
    async def get_symbols_bybit(self) -> List[Dict]:
        """دریافت symbols از Bybit"""
        try:
            url = f"{self.exchanges['bybit']['base_url']}/v2/public/symbols"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol in data.get('result', []):
                        if symbol['name'].endswith('USDT'):
                            usdt_symbols.append({
                                'symbol': symbol['name'],
                                'status': 'TRADING'
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting Bybit symbols: {e}")
        return []
    
    async def get_symbols_kucoin(self) -> List[Dict]:
        """دریافت symbols از KuCoin"""
        try:
            url = f"{self.exchanges['kucoin']['base_url']}/api/v1/symbols"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol in data.get('data', []):
                        if (symbol['symbol'].endswith('-USDT') and 
                            symbol['enableTrading']):
                            usdt_symbols.append({
                                'symbol': symbol['symbol'].replace('-', ''),
                                'status': 'TRADING'
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting KuCoin symbols: {e}")
        return []
    
    async def get_symbols_mexc(self) -> List[Dict]:
        """دریافت symbols از MEXC"""
        try:
            url = f"{self.exchanges['mexc']['base_url']}/api/v3/exchangeInfo"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol in data.get('symbols', []):
                        if (symbol['symbol'].endswith('USDT') and 
                            symbol['status'] == 'ENABLED'):
                            usdt_symbols.append({
                                'symbol': symbol['symbol'],
                                'status': 'TRADING'
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting MEXC symbols: {e}")
        return []
    
    async def get_symbols_coinex(self) -> List[Dict]:
        """دریافت symbols از CoinEx"""
        try:
            url = f"{self.exchanges['coinex']['base_url']}/v1/market/info"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol_name, symbol_info in data.get('data', {}).items():
                        if symbol_name.endswith('USDT'):
                            usdt_symbols.append({
                                'symbol': symbol_name,
                                'status': 'TRADING'
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting CoinEx symbols: {e}")
        return []
    
    async def get_symbols_bitunix(self) -> List[Dict]:
        """دریافت symbols از BitUnix"""
        try:
            url = f"{self.exchanges['bitunix']['base_url']}/api/spot/v1/market/symbols"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    usdt_symbols = []
                    for symbol in data.get('data', []):
                        if (symbol.get('symbol', '').endswith('USDT') and 
                            symbol.get('status') == 'TRADING'):
                            usdt_symbols.append({
                                'symbol': symbol['symbol'],
                                'status': symbol['status']
                            })
                    return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting BitUnix symbols: {e}")
        return []
    
    async def get_all_symbols(self) -> List[Dict]:
        """دریافت تمام symbols از صرافی فعلی"""
        current_time = time.time()
        
        # استفاده از cache اگر تازه باشه
        if (self.symbols_list and 
            current_time - self.last_symbols_fetch < 1800):  # 30 دقیقه
            logger.info(f"Using cached symbols: {len(self.symbols_list)} pairs from {self.current_exchange}")
            return self.symbols_list
        
        if not self.current_exchange:
            if not await self.find_working_exchange():
                return []
        
        try:
            symbols = []
            
            if self.current_exchange == 'binance':
                symbols = await self.get_symbols_binance()
            elif self.current_exchange == 'bybit':
                symbols = await self.get_symbols_bybit()
            elif self.current_exchange == 'kucoin':
                symbols = await self.get_symbols_kucoin()
            elif self.current_exchange == 'mexc':
                symbols = await self.get_symbols_mexc()
            elif self.current_exchange == 'coinex':
                symbols = await self.get_symbols_coinex()
            elif self.current_exchange == 'bitunix':
                symbols = await self.get_symbols_bitunix()
            
            if symbols:
                self.symbols_list = symbols
                self.last_symbols_fetch = current_time
                logger.info(f"✅ Fetched {len(symbols)} USDT pairs from {self.current_exchange.upper()}")
                return symbols
            else:
                # اگر صرافی فعلی کار نکرد، صرافی دیگه پیدا کن
                logger.warning(f"No symbols from {self.current_exchange}, trying other exchanges...")
                self.current_exchange = None
                if await self.find_working_exchange():
                    return await self.get_all_symbols()
                
        except Exception as e:
            logger.error(f"Error getting symbols from {self.current_exchange}: {e}")
            self.current_exchange = None
            
        return self.symbols_list if self.symbols_list else []
    
    async def get_kline_binance(self, symbol: str) -> Optional[Dict]:
        """دریافت کندل از Binance"""
        try:
            url = f"{self.exchanges['binance']['base_url']}/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': '1m',
                'limit': 2
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if len(data) >= 2:
                        current_candle = data[-1]  # آخرین کندل
                        prev_candle = data[-2]     # کندل قبلی
                        
                        current_open = float(current_candle[1])
                        current_close = float(current_candle[4])
                        current_high = float(current_candle[2])
                        current_low = float(current_candle[3])
                        current_volume = float(current_candle[5])
                        prev_close = float(prev_candle[4])
                        
                        # محاسبه تغییرات
                        candle_change = ((current_close - current_open) / current_open) * 100 if current_open > 0 else 0
                        total_change = ((current_close - prev_close) / prev_close) * 100 if prev_close > 0 else 0
                        
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
                            'timestamp': current_candle[0]
                        }
        except Exception as e:
            logger.error(f"Error getting Binance kline for {symbol}: {e}")
        return None
    
    async def get_24h_binance(self) -> Dict:
        """دریافت تیکر 24 ساعته از Binance"""
        try:
            url = f"{self.exchanges['binance']['base_url']}/api/v3/ticker/24hr"
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    tickers = {}
                    for ticker in data:
                        symbol = ticker['symbol']
                        if symbol.endswith('USDT'):
                            tickers[symbol] = {
                                'symbol': symbol,
                                'price': float(ticker['lastPrice']),
                                'change_24h': float(ticker['priceChangePercent']),
                                'volume': float(ticker['volume'])
                            }
                    return tickers
        except Exception as e:
            logger.error(f"Error getting Binance 24h tickers: {e}")
        return {}
    
    async def get_kline_data(self, symbol: str) -> Optional[Dict]:
        """دریافت کندل از صرافی فعلی"""
        if not self.current_exchange:
            return None
        
        try:
            if self.current_exchange == 'binance':
                return await self.get_kline_binance(symbol)
            # برای سایر صرافی‌ها، فعلاً از Binance استفاده کن
            # میتونی بعداً سایر صرافی‌ها رو اضافه کنی
            else:
                return await self.get_kline_binance(symbol)
                
        except Exception as e:
            logger.error(f"Error getting kline for {symbol} from {self.current_exchange}: {e}")
            return None
    
    async def get_24h_tickers(self) -> Dict:
        """دریافت تیکرهای 24 ساعته"""
        if not self.current_exchange:
            return {}
        
        try:
            if self.current_exchange == 'binance':
                return await self.get_24h_binance()
            else:
                return await self.get_24h_binance()  # fallback
                
        except Exception as e:
            logger.error(f"Error getting 24h tickers from {self.current_exchange}: {e}")
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
        
        # انتخاب تصادفی subset از symbols برای کاهش load
        max_check = min(100, len(symbols))  # حداکثر 100 جفت
        selected_symbols = random.sample(symbols, max_check) if len(symbols) > max_check else symbols
        
        # بررسی batch
        batch_size = 20
        
        for i in range(0, len(selected_symbols), batch_size):
            batch = selected_symbols[i:i + batch_size]
            
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
            if i + batch_size < len(selected_symbols):
                await asyncio.sleep(3)  # وقفه بیشتر برای rate limiting
        
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
<b>Exchange:</b> {self.current_exchange.upper()}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>🔥 1-minute candle moved above {self.pump_threshold}%!</b>

#pump #alert #{coin_name.lower()} #{self.current_exchange}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Pump alert sent: {symbol} +{candle_change:.2f}% via {self.current_exchange}")
    
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
<b>Exchange:</b> {self.current_exchange.upper()}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>⚠️ 1-minute candle moved below {self.dump_threshold}%!</b>

#dump #alert #{coin_name.lower()} #{self.current_exchange}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"✅ Dump alert sent: {symbol} {candle_change:.2f}% via {self.current_exchange}")
    
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
                message += "<b>📈 Daily Gains +
