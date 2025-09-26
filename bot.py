import asyncio
import aiohttp
import os
from datetime import datetime
import logging
import sys
import random
import time

# --- Settings ---
os.environ['PYTHONIOENCODING'] = 'utf-8'
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

class CryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.api_base = 'https://api.bitunix.com'  # <-- BITUNIX API BASE
        
        # --- Your Custom Thresholds ---
        self.pump_threshold = 4.0
        self.dump_threshold = -4.0
        self.report_pump_threshold = 5.0
        self.report_dump_threshold = -5.0
        
        self.report_interval = 600  # 10 minutes
        self.last_report_time = 0
        
        # Cache for symbols
        self.symbols_list = []
        self.last_symbols_fetch = 0

    async def init_session(self):
        timeout = aiohttp.ClientTimeout(total=20, connect=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        logger.info("Session initialized for Bitunix")

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")

    async def safe_request(self, url: str, params: dict = None):
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    # Bitunix API returns success code in 'code' field
                    if data.get('code') == 200:
                        return data.get('data')
                    else:
                        logger.warning(f"API Error from Bitunix: {data.get('msg')}")
                        return None
                else:
                    logger.warning(f"HTTP Error {response.status}: {await response.text()}")
                    return None
        except Exception as e:
            logger.error(f"Request error to {url}: {e}")
            return None

    # --- BITUNIX SPECIFIC FUNCTIONS ---

    async def get_symbols(self):
        """Get USDT symbols from Bitunix"""
        if self.symbols_list and time.time() - self.last_symbols_fetch < 3600: # Cache for 1 hour
             return self.symbols_list
        
        url = f"{self.api_base}/api/v1/market/tickers"
        data = await self.safe_request(url)
        
        if data:
            usdt_symbols = [item['symbol'] for item in data if item['symbol'].endswith('_USDT')]
            self.symbols_list = usdt_symbols
            self.last_symbols_fetch = time.time()
            logger.info(f"Loaded {len(usdt_symbols)} USDT pairs from Bitunix")
            return usdt_symbols
        return self.symbols_list

    async def get_kline(self, symbol: str):
        """Get 1m kline data from Bitunix"""
        url = f"{self.api_base}/api/v1/market/klines"
        params = {'symbol': symbol, 'period': '1m', 'size': 2} # Bitunix uses 'period' and 'size'
        data = await self.safe_request(url, params)

        if data and len(data) >= 2:
            current = data[0] # Bitunix returns newest first
            prev = data[1]

            open_price = float(current[1])
            close_price = float(current[4])
            
            if open_price > 0:
                candle_change = ((close_price - open_price) / open_price) * 100
                return {
                    'symbol': symbol,
                    'close': close_price,
                    'candle_change': candle_change,
                }
        return None

    async def get_24h_movers(self):
        """Get top 24h movers from Bitunix"""
        pumps, dumps = [], []
        url = f"{self.api_base}/api/v1/market/tickers"
        data = await self.safe_request(url)

        if not data:
            return {'pumps': [], 'dumps': []}

        for item in data:
            if not item['symbol'].endswith('_USDT'):
                continue

            # Bitunix provides change as a decimal, so multiply by 100
            change_percent = float(item.get('change_24h', 0)) * 100
            
            if change_percent >= self.report_pump_threshold:
                pumps.append({'symbol': item['symbol'], 'change': change_percent, 'price': float(item['close'])})
            elif change_percent <= self.report_dump_threshold:
                dumps.append({'symbol': item['symbol'], 'change': change_percent, 'price': float(item['close'])})

        pumps.sort(key=lambda x: x['change'], reverse=True)
        dumps.sort(key=lambda x: x['change'])
        return {'pumps': pumps[:5], 'dumps': dumps[:5]}

    # --- TELEGRAM AND REPORTING (No changes needed here) ---

    def format_price(self, price: float) -> str:
        if price >= 1: return f"${price:.4f}"
        if price >= 0.01: return f"${price:.6f}"
        return f"${price:.8f}"
    
    async def send_telegram(self, message: str) -> bool:
        if not BOT_TOKEN or not CHAT_ID:
            logger.info(f"TEST MODE: {message[:100]}")
            return True
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
        try:
            async with self.session.post(url, json=payload) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    async def send_alert(self, kline_data: dict, is_pump: bool):
        symbol = kline_data['symbol']
        change = kline_data['candle_change']
        price = kline_data['close']
        coin_name = symbol.replace('_USDT', '')
        
        alert_type, emoji, sign = ("PUMP", "🚀", "+") if is_pump else ("DUMP", "📉", "")
        
        message = (f"{emoji} {alert_type} ALERT!\n\n"
                   f"Coin: {coin_name}\n"
                   f"Change: {sign}{change:.2f}%\n"
                   f"Price: {self.format_price(price)}\n"
                   f"Time: {datetime.now().strftime('%H:%M:%S')}")
        await self.send_telegram(message)
        logger.info(f"Alert sent for {symbol}: {sign}{change:.2f}%")

    async def send_status_report(self):
        movers = await self.get_24h_movers()
        pumps, dumps = movers['pumps'], movers['dumps']
        
        message = f"📊 <b>10-Minute Report (Bitunix)</b>\n"
        message += f"🕒 Time: {datetime.now().strftime('%H:%M:%S')}\n\n"
        
        if pumps:
            message += "🚀 <b>Top Pumps (24h):</b>\n"
            for coin in pumps:
                coin_name = coin['symbol'].replace('_USDT', '')
                message += f"• {coin_name}: +{coin['change']:.2f}% - {self.format_price(coin['price'])}\n"
            message += "\n"
        
        if dumps:
            message += "📉 <b>Top Dumps (24h):</b>\n"
            for coin in dumps:
                coin_name = coin['symbol'].replace('_USDT', '')
                message += f"• {coin_name}: {coin['change']:.2f}% - {self.format_price(coin['price'])}\n"
            message += "\n"
        
        if not pumps and not dumps:
            message += "😴 No significant movers (±{self.report_pump_threshold}%) found.\n\n"
        
        message += "❤️ Bot is alive and scanning..."
        await self.send_telegram(message)
        logger.info(f"Status report sent - Pumps: {len(pumps)}, Dumps: {len(dumps)}")

    async def run(self):
        """Main monitoring loop"""
        await self.init_session()
        logger.info("Crypto Monitor starting with Bitunix...")
        await self.send_telegram("🤖 <b>Bot Started (Bitunix)</b>\nMonitoring Bitunix API...")

        self.last_report_time = time.time()

        while self.running:
            start_time = time.time()
            symbols = await self.get_symbols()
            if not symbols:
                logger.error("Could not fetch symbols from Bitunix, sleeping for 60s")
                await asyncio.sleep(60)
                continue

            # Check a random subset for 1-minute alerts
            tasks = []
            for symbol in random.sample(symbols, min(len(symbols), 50)): # Check 50 random coins
                tasks.append(self.get_kline(symbol))
            
            results = await asyncio.gather(*tasks)

            for kline in results:
                if not kline: continue
                change = kline['candle_change']
                if change >= self.pump_threshold:
                    await self.send_alert(kline, True)
                elif change <= self.dump_threshold:
                    await self.send_alert(kline, False)

            # Send 10-minute report
            if time.time() - self.last_report_time >= self.report_interval:
                await self.send_status_report()
                self.last_report_time = time.time()
            
            elapsed = time.time() - start_time
            sleep_time = max(1, 60 - elapsed) # Loop every ~60 seconds
            logger.info(f"Scan finished in {elapsed:.1f}s, sleeping for {sleep_time:.1f}s")
            await asyncio.sleep(sleep_time)

# --- Web Server for Render ---
from aiohttp import web

async def home_handler(request):
    return web.Response(text="🤖 Crypto Monitor (Bitunix) - Running")

def create_app():
    app = web.Application()
    app.router.add_get('/', home_handler)
    monitor = CryptoMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())
    return app

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("BOT_TOKEN or CHAT_ID not set. Running in test mode.")
    
    app = create_app()
    port = int(os.getenv('PORT', 8080))
    web.run_app(app, host='0.0.0.0', port=port)
