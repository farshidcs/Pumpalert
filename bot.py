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

class CryptoPumpDumpMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0  # 4% برای پامپ فوری
        self.dump_threshold = -4.0  # -4% برای دامپ فوری
        self.daily_threshold = 20.0  # 20% برای گزارش روزانه
        
        # CoinGecko API - رایگان و بدون محدودیت سخت
        self.coingecko_base_url = "https://api.coingecko.com/api/v3"
        
        # Cache برای اطلاعات
        self.coins_list = []
        self.last_coins_fetch = 0
        self.price_history = {}  # نگهداری قیمت‌های قبلی
        self.last_report_time = 0
        self.last_30min_prices = {}  # قیمت‌های 30 دقیقه قبل
        
    async def init_session(self):
        """شروع HTTP session"""
        connector = aiohttp.TCPConnector(
            limit=30,
            limit_per_host=10,
            ttl_dns_cache=300,
            use_dns_cache=True
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; CryptoMonitor/1.0)',
            'Accept': 'application/json'
        }
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers
        )
        logger.info("HTTP Session initialized for CoinGecko API")
    
    async def close_session(self):
        """بستن session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")
    
    async def test_coingecko_connection(self):
        """تست اتصال به CoinGecko"""
        try:
            url = f"{self.coingecko_base_url}/ping"
            async with self.session.get(url) as response:
                if response.status == 200:
                    logger.info("CoinGecko connection test successful")
                    return True
                else:
                    logger.error(f"CoinGecko connection test failed: {response.status}")
                    return False
        except Exception as e:
            logger.error(f"CoinGecko connection test error: {e}")
            return False
    
    async def get_top_coins(self) -> List[Dict]:
        """دریافت لیست top coins"""
        current_time = time.time()
        
        # اگر کمتر از 30 دقیقه از آخرین fetch گذشته، از cache استفاده کن
        if (self.coins_list and 
            current_time - self.last_coins_fetch < 1800):  # 30 دقیقه
            logger.info(f"Using cached coins list: {len(self.coins_list)} coins")
            return self.coins_list
        
        try:
            # دریافت top 200 coins بر اساس market cap
            url = f"{self.coingecko_base_url}/coins/markets"
            params = {
                'vs_currency': 'usd',
                'order': 'market_cap_desc',
                'per_page': 200,
                'page': 1,
                'sparkline': False,
                'price_change_percentage': '1h,24h'
            }
            
            await asyncio.sleep(0.5)  # Rate limiting
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # فیلتر کردن coins که قیمت معقول دارند
                    filtered_coins = []
                    for coin in data:
                        if (coin.get('current_price') and 
                            coin.get('market_cap') and
                            coin.get('total_volume') and
                            coin.get('current_price') > 0.000001):  # حداقل قیمت
                            filtered_coins.append(coin)
                    
                    self.coins_list = filtered_coins
                    self.last_coins_fetch = current_time
                    
                    logger.info(f"Fetched {len(filtered_coins)} coins from CoinGecko")
                    return filtered_coins
                else:
                    logger.error(f"Error getting coins from CoinGecko: {response.status}")
                    if self.coins_list:
                        return self.coins_list
                    return []
                    
        except Exception as e:
            logger.error(f"Error in get_top_coins: {e}")
            if self.coins_list:
                return self.coins_list
            return []
    
    async def get_current_prices(self, coin_ids: List[str]) -> Dict:
        """دریافت قیمت‌های فعلی"""
        try:
            # CoinGecko محدودیت 100 coin در هر درخواست دارد
            all_prices = {}
            
            for i in range(0, len(coin_ids), 100):
                batch = coin_ids[i:i + 100]
                ids_param = ','.join(batch)
                
                url = f"{self.coingecko_base_url}/simple/price"
                params = {
                    'ids': ids_param,
                    'vs_currencies': 'usd',
                    'include_24hr_change': 'true',
                    'include_1hr_change': 'true'
                }
                
                await asyncio.sleep(1)  # Rate limiting برای CoinGecko
                
                async with self.session.get(url, params=params) as response:
                    if response.status == 200:
                        batch_data = await response.json()
                        all_prices.update(batch_data)
                    else:
                        logger.warning(f"Error getting prices for batch: {response.status}")
            
            logger.info(f"Got current prices for {len(all_prices)} coins")
            return all_prices
            
        except Exception as e:
            logger.error(f"Error in get_current_prices: {e}")
            return {}
    
    def calculate_price_change(self, current_price: float, previous_price: float) -> float:
        """محاسبه درصد تغییر قیمت"""
        if previous_price and previous_price > 0:
            return ((current_price - previous_price) / previous_price) * 100
        return 0.0
    
    def get_coin_rank_category(self, market_cap_rank: int) -> str:
        """دسته‌بندی coin بر اساس رنک"""
        if market_cap_rank <= 3:
            return "TOP3"
        elif market_cap_rank <= 10:
            return "TOP10"
        elif market_cap_rank <= 50:
            return "TOP50"
        elif market_cap_rank <= 100:
            return "TOP100"
        else:
            return "ALT"
    
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
    
    def format_price(self, price: float) -> str:
        """فرمت کردن قیمت"""
        if price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"
    
    async def check_minute_moves(self, coins: List[Dict]) -> tuple:
        """بررسی تغییرات دقیقه‌ای (شبیه‌سازی کندل 1 دقیقه‌ای)"""
        pumps_found = 0
        dumps_found = 0
        current_time = time.time()
        
        try:
            # دریافت قیمت‌های فعلی
            coin_ids = [coin['id'] for coin in coins]
            current_prices = await self.get_current_prices(coin_ids)
            
            for coin in coins:
                coin_id = coin['id']
                coin_symbol = coin['symbol'].upper()
                coin_name = coin['name']
                market_cap_rank = coin.get('market_cap_rank', 999)
                
                if coin_id not in current_prices:
                    continue
                
                current_price = current_prices[coin_id].get('usd', 0)
                if not current_price:
                    continue
                
                # بررسی تغییر 1 ساعته (به عنوان شبیه‌سازی تغییر کوتاه مدت)
                hourly_change = current_prices[coin_id].get('usd_1h_change', 0)
                
                if hourly_change is None:
                    continue
                
                # چک پامپ فوری
                if hourly_change >= self.pump_threshold:
                    await self.send_pump_alert({
                        'symbol': coin_symbol,
                        'name': coin_name,
                        'price': current_price,
                        'change': hourly_change,
                        'rank': market_cap_rank,
                        'volume': coin.get('total_volume', 0),
                        'market_cap': coin.get('market_cap', 0)
                    })
                    pumps_found += 1
                
                # چک دامپ فوری
                elif hourly_change <= self.dump_threshold:
                    await self.send_dump_alert({
                        'symbol': coin_symbol,
                        'name': coin_name,
                        'price': current_price,
                        'change': hourly_change,
                        'rank': market_cap_rank,
                        'volume': coin.get('total_volume', 0),
                        'market_cap': coin.get('market_cap', 0)
                    })
                    dumps_found += 1
                
                # ذخیره قیمت برای بار بعد
                self.price_history[coin_id] = {
                    'price': current_price,
                    'timestamp': current_time
                }
            
            return pumps_found, dumps_found
            
        except Exception as e:
            logger.error(f"Error in check_minute_moves: {e}")
            return 0, 0
    
    async def send_pump_alert(self, coin_data: Dict):
        """ارسال هشدار پامپ"""
        symbol = coin_data['symbol']
        name = coin_data['name']
        price = coin_data['price']
        change = coin_data['change']
        rank = coin_data['rank']
        volume = coin_data.get('volume', 0)
        
        rank_category = self.get_coin_rank_category(rank)
        
        message = f"""
<b>🚀 PUMP ALERT!</b>

<b>Coin:</b> {name} (#{symbol})
<b>Rank:</b> #{rank} ({rank_category})
<b>1h Change:</b> <b>+{change:.2f}%</b>
<b>Price:</b> {self.format_price(price)}
<b>Volume:</b> ${volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>Strong upward movement detected!</b>

#pump #alert #{symbol.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"Pump alert sent: {symbol} +{change:.2f}%")
    
    async def send_dump_alert(self, coin_data: Dict):
        """ارسال هشدار دامپ"""
        symbol = coin_data['symbol']
        name = coin_data['name']
        price = coin_data['price']
        change = coin_data['change']
        rank = coin_data['rank']
        volume = coin_data.get('volume', 0)
        
        rank_category = self.get_coin_rank_category(rank)
        
        message = f"""
<b>📉 DUMP ALERT!</b>

<b>Coin:</b> {name} (#{symbol})
<b>Rank:</b> #{rank} ({rank_category})
<b>1h Change:</b> <b>{change:.2f}%</b>
<b>Price:</b> {self.format_price(price)}
<b>Volume:</b> ${volume:,.0f}
<b>Time:</b> {datetime.now().strftime("%H:%M:%S")}

<b>Strong downward movement detected!</b>

#dump #alert #{symbol.lower()}
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info(f"Dump alert sent: {symbol} {change:.2f}%")
    
    async def send_30min_report(self, coins: List[Dict]):
        """گزارش هر 30 دقیقه"""
        try:
            current_time = datetime.now().strftime("%H:%M - %d/%m")
            
            daily_gainers = []
            daily_losers = []
            recent_movers = []  # برای حرکات 30 دقیقه اخیر
            
            # دریافت قیمت‌های فعلی
            coin_ids = [coin['id'] for coin in coins]
            current_prices = await self.get_current_prices(coin_ids)
            current_timestamp = time.time()
            
            for coin in coins:
                coin_id = coin['id']
                coin_symbol = coin['symbol'].upper()
                coin_name = coin['name']
                market_cap_rank = coin.get('market_cap_rank', 999)
                
                if coin_id not in current_prices:
                    continue
                
                current_price = current_prices[coin_id].get('usd', 0)
                daily_change = current_prices[coin_id].get('usd_24h_change', 0)
                
                if not current_price or daily_change is None:
                    continue
                
                # چک تغییرات روزانه بالای 20%
                if daily_change >= self.daily_threshold:
                    daily_gainers.append({
                        'symbol': coin_symbol,
                        'name': coin_name,
                        'change': daily_change,
                        'price': current_price,
                        'rank': market_cap_rank
                    })
                elif daily_change <= -self.daily_threshold:
                    daily_losers.append({
                        'symbol': coin_symbol,
                        'name': coin_name,
                        'change': daily_change,
                        'price': current_price,
                        'rank': market_cap_rank
                    })
                
                # چک تغییرات 30 دقیقه اخیر
                if coin_id in self.last_30min_prices:
                    last_30min_price = self.last_30min_prices[coin_id]['price']
                    time_diff = current_timestamp - self.last_30min_prices[coin_id]['timestamp']
                    
                    # اگر حدود 30 دقیقه گذشته باشد
                    if 1500 <= time_diff <= 2100:  # 25-35 دقیقه
                        change_30min = self.calculate_price_change(current_price, last_30min_price)
                        
                        if abs(change_30min) >= self.daily_threshold:
                            recent_movers.append({
                                'symbol': coin_symbol,
                                'name': coin_name,
                                'change': change_30min,
                                'price': current_price,
                                'rank': market_cap_rank
                            })
                
                # بروزرسانی قیمت‌های 30 دقیقه قبل
                self.last_30min_prices[coin_id] = {
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
                    rank_cat = self.get_coin_rank_category(coin['rank'])
                    message += f"{i+1}. {coin['name']} (#{coin['symbol']}) - {rank_cat}: <b>+{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # ریزش‌های روزانه زیر -20%
            if daily_losers:
                message += "<b>📉 Daily Losses -20%:</b>\n"
                for i, coin in enumerate(daily_losers[:5]):
                    rank_cat = self.get_coin_rank_category(coin['rank'])
                    message += f"{i+1}. {coin['name']} (#{coin['symbol']}) - {rank_cat}: <b>{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # حرکات 30 دقیقه اخیر
            if recent_movers:
                message += "<b>⚡ 30-min Big Moves ±20%:</b>\n"
                for i, coin in enumerate(recent_movers[:3]):
                    rank_cat = self.get_coin_rank_category(coin['rank'])
                    sign = "+" if coin['change'] > 0 else ""
                    message += f"{i+1}. {coin['name']} (#{coin['symbol']}) - {rank_cat}: <b>{sign}{coin['change']:.1f}%</b>\n"
                message += "\n"
            
            # اگر هیچ حرکت خاصی نبود
            if not daily_gainers and not daily_losers and not recent_movers:
                message += "<b>😴 Quiet Market:</b>\n"
                message += "• No significant moves detected\n"
                message += "• Market is consolidating\n\n"
            
            message += f"<b>📊 Monitored:</b> {len(coins)} coins\n"
            message += f"<b>⏰ Next Report:</b> {(datetime.now() + timedelta(minutes=30)).strftime('%H:%M')}\n\n"
            message += "#report #30min #summary"
            
            # ارسال گزارش
            success = await self.send_telegram(message)
            if success:
                logger.info(f"30min report sent | Daily gains: {len(daily_gainers)} | Daily losses: {len(daily_losers)} | Recent movers: {len(recent_movers)}")
            
        except Exception as e:
            logger.error(f"Error in send_30min_report: {e}")
    
    async def send_startup_message(self):
        """پیام شروع بات"""
        current_time = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        coins = await self.get_top_coins()
        coin_count = len(coins) if coins else 0
        
        message = f"""
<b>🤖 Crypto Monitor Started!</b>

<b>🕐 Start Time:</b> {current_time}
<b>📊 Monitoring:</b> Top {coin_count} cryptocurrencies
<b>⚡ Instant Alerts:</b> ±4% hourly moves
<b>📈 30min Reports:</b> ±20% daily/30min changes
<b>🔍 Check Interval:</b> Every 2 minutes
<b>📡 Data Source:</b> CoinGecko API

<b>✅ Bot is now actively monitoring!</b>

#start #monitoring #coingecko
        """
        
        success = await self.send_telegram(message.strip())
        if success:
            logger.info("Startup message sent!")
        return success
    
    async def run(self):
        """اجرای اصلی بات"""
        await self.init_session()
        logger.info("Crypto Monitor Starting with CoinGecko API...")
        
        # تست اتصال
        connection_ok = await self.test_coingecko_connection()
        if not connection_ok:
            logger.error("Cannot connect to CoinGecko API!")
            return
        
        # ارسال پیام شروع
        startup_success = await self.send_startup_message()
        if not startup_success:
            logger.warning("Startup message failed")
        
        # متغیرها
        self.last_report_time = time.time()
        total_scans = 0
        consecutive_errors = 0
        
        try:
            while self.running:
                start_time = time.time()
                
                try:
                    # دریافت لیست coins
                    coins = await self.get_top_coins()
                    if not coins:
                        consecutive_errors += 1
                        logger.error(f"No coins received! Error #{consecutive_errors}")
                        
                        if consecutive_errors >= 5:
                            logger.error("Too many errors, stopping...")
                            break
                        
                        await asyncio.sleep(120)
                        continue
                    
                    consecutive_errors = 0
                    
                    # بررسی حرکات فوری
                    pumps, dumps = await self.check_minute_moves(coins)
                    
                    total_scans += 1
                    current_time = datetime.now().strftime("%H:%M:%S")
                    logger.info(f"Scan #{total_scans} | Coins: {len(coins)} | Pumps: {pumps} | Dumps: {dumps} | {current_time}")
                    
                    # گزارش 30 دقیقه‌ای
                    if time.time() - self.last_report_time >= 1800:  # 30 دقیقه
                        await self.send_30min_report(coins)
                        self.last_report_time = time.time()
                    
                except Exception as scan_error:
                    consecutive_errors += 1
                    logger.error(f"Scan error: {scan_error} (#{consecutive_errors})")
                    
                    if consecutive_errors >= 10:
                        logger.error("Too many errors, stopping...")
                        break
                
                # محاسبه زمان استراحت
                execution_time = time.time() - start_time
                sleep_time = max(30, 120 - execution_time)  # حداقل 30 ثانیه، هدف 2 دقیقه
                
                logger.info(f"Execution: {execution_time:.2f}s | Sleep: {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Stop signal received...")
        except Exception as e:
            logger.error(f"Critical error: {e}")
            error_msg = f"Bot critical error: {str(e)[:200]}"
            await self.send_telegram(error_msg)
        finally:
            self.running = False
            await self.close_session()
            logger.info("Bot stopped")

# Web Server برای deployment
async def home_handler(request):
    return web.Response(
        text="""🤖 Crypto Pump/Dump Monitor - CoinGecko API
        
Status: Active
Data Source: CoinGecko API
Instant Alerts: ±4% hourly moves  
30min Reports: ±20% daily/30min changes
Check Interval: 2 minutes
        
Bot is monitoring top cryptocurrencies!""",
        content_type='text/plain'
    )

async def health_handler(request):
    return web.json_response({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "crypto-monitor-coingecko",
        "api_source": "CoinGecko"
    })

async def stats_handler(request):
    return web.json_response({
        "instant_threshold": "±4%",
        "report_threshold": "±20%",
        "check_interval": "2 minutes",
        "report_interval": "30min",
        "monitoring": "Top 200 coins by market cap",
        "api_source": "CoinGecko"
    })

async def init_bot(app):
    """شروع بات در background"""
    logger.info("Starting Crypto Monitor with CoinGecko...")
    monitor = CryptoPumpDumpMonitor()
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
    
    logger.info(f"Starting Crypto Monitor on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
