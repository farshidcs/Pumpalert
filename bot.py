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
import random

# Encoding fix
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# Bot settings - ONLY from environment variables
BOT_TOKEN = os.getenv('BOT_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

class CryptoMonitor:
    def __init__(self):
        self.session = None
        self.running = True
        self.pump_threshold = 4.0
        self.dump_threshold = -4.0
        
        # Report settings
        self.report_pump_threshold = 2.0    # 2% for reports
        self.report_dump_threshold = -2.0   # -2% for reports
        self.last_report_time = 0
        self.report_interval = 600  # 10 minutes in seconds
        
        # Only use Binance for stability
        self.api_base = 'https://api.binance.com'
        
        # Cache
        self.symbols_list = []
        self.last_symbols_fetch = 0
        
    async def init_session(self):
        """Initialize HTTP session"""
        connector = aiohttp.TCPConnector(
            limit=20,
            limit_per_host=5,
            ttl_dns_cache=300,
            use_dns_cache=True
        )
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        headers = {
            'User-Agent': 'CryptoMonitor/1.0',
            'Accept': 'application/json'
        }
        
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers
        )
        logger.info("Session initialized")
    
    async def close_session(self):
        """Close session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("Session closed")
    
    async def safe_request(self, url: str, params: dict = None):
        """Safe request with error handling"""
        try:
            await asyncio.sleep(0.1)  # Rate limiting
            async with self.session.get(url, params=params, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    logger.warning("Rate limited, waiting...")
                    await asyncio.sleep(60)
                    return None
                else:
                    logger.warning(f"HTTP {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Request error: {e}")
            return None
    
    async def get_symbols(self) -> List[str]:
        """Get USDT symbols from Binance"""
        current_time = time.time()
        
        # Use cache if recent
        if self.symbols_list and current_time - self.last_symbols_fetch < 1800:
            return self.symbols_list
        
        try:
            url = f"{self.api_base}/api/v3/exchangeInfo"
            data = await self.safe_request(url)
            
            if data:
                usdt_symbols = []
                for symbol in data.get('symbols', []):
                    if symbol['symbol'].endswith('USDT') and symbol['status'] == 'TRADING':
                        usdt_symbols.append(symbol['symbol'])
                
                self.symbols_list = usdt_symbols
                self.last_symbols_fetch = current_time
                logger.info(f"Loaded {len(usdt_symbols)} USDT pairs")
                return usdt_symbols
        except Exception as e:
            logger.error(f"Error getting symbols: {e}")
        
        return self.symbols_list
    
    async def get_kline(self, symbol: str) -> Optional[Dict]:
        """Get 1m kline data"""
        try:
            url = f"{self.api_base}/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': '1m',
                'limit': 2
            }
            
            data = await self.safe_request(url, params)
            if data and len(data) >= 2:
                current = data[-1]
                prev = data[-2]
                
                open_price = float(current[1])
                close_price = float(current[4])
                high_price = float(current[2])
                low_price = float(current[3])
                volume = float(current[5])
                prev_close = float(prev[4])
                
                if open_price > 0 and prev_close > 0:
                    candle_change = ((close_price - open_price) / open_price) * 100
                    total_change = ((close_price - prev_close) / prev_close) * 100
                    
                    return {
                        'symbol': symbol,
                        'open': open_price,
                        'close': close_price,
                        'high': high_price,
                        'low': low_price,
                        'volume': volume,
                        'candle_change': candle_change,
                        'total_change': total_change
                    }
        except Exception as e:
            logger.error(f"Error getting kline for {symbol}: {e}")
        return None
    
    async def get_24h_ticker(self, symbol: str) -> Optional[Dict]:
        """Get 24h ticker data"""
        try:
            url = f"{self.api_base}/api/v3/ticker/24hr"
            params = {'symbol': symbol}
            
            data = await self.safe_request(url, params)
            if data:
                return {
                    'symbol': symbol,
                    'price_change_percent': float(data['priceChangePercent']),
                    'last_price': float(data['lastPrice']),
                    'volume': float(data['volume']),
                    'count': int(data['count'])
                }
        except Exception as e:
            logger.error(f"Error getting 24h ticker for {symbol}: {e}")
        return None
    
    def format_price(self, price: float) -> str:
        """Format price"""
        if price >= 1:
            return f"${price:.4f}"
        elif price >= 0.01:
            return f"${price:.6f}"
        else:
            return f"${price:.8f}"
    
    async def send_telegram(self, message: str) -> bool:
        """Send Telegram message"""
        try:
            if not BOT_TOKEN or not CHAT_ID:
                logger.info(f"TEST: {message[:100]}")
                return True  # Simulate success for testing
                
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            data = {
                'chat_id': CHAT_ID,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            async with self.session.post(url, json=data) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Telegram error: {e}")
            return False
    
    async def send_alert(self, kline_data: Dict, is_pump: bool):
        """Send pump/dump alert"""
        symbol = kline_data['symbol']
        candle_change = kline_data['candle_change']
        close_price = kline_data['close']
        
        coin_name = symbol.replace('USDT', '')
        alert_type = "PUMP" if is_pump else "DUMP"
        emoji = "🚀" if is_pump else "📉"
        sign = "+" if is_pump else ""
        
        message = f"""{emoji} {alert_type} ALERT!

Coin: {coin_name}
Symbol: {symbol}
Change: {sign}{candle_change:.2f}%
Price: {self.format_price(close_price)}
Time: {datetime.now().strftime("%H:%M:%S")}

#{coin_name.lower()} #{alert_type.lower()}"""
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"Alert sent: {symbol} {sign}{candle_change:.2f}%")
    
    async def get_24h_movers(self, symbols: List[str]) -> Dict:
        """Get coins with significant 24h moves"""
        pumps = []
        dumps = []
        
        # Check a larger subset for reports
        check_count = min(100, len(symbols))
        selected = random.sample(symbols, check_count)
        
        for symbol in selected:
            ticker = await self.get_24h_ticker(symbol)
            if not ticker:
                continue
            
            change_percent = ticker['price_change_percent']
            
            if change_percent >= self.report_pump_threshold:
                pumps.append({
                    'symbol': symbol,
                    'change': change_percent,
                    'price': ticker['last_price']
                })
            elif change_percent <= self.report_dump_threshold:
                dumps.append({
                    'symbol': symbol,
                    'change': change_percent,
                    'price': ticker['last_price']
                })
        
        # Sort by change percentage
        pumps.sort(key=lambda x: x['change'], reverse=True)
        dumps.sort(key=lambda x: x['change'])
        
        return {'pumps': pumps[:5], 'dumps': dumps[:5]}  # Top 5 of each
    
    async def send_status_report(self, symbols: List[str]):
        """Send 10-minute status report"""
        try:
            movers = await self.get_24h_movers(symbols)
            pumps = movers['pumps']
            dumps = movers['dumps']
            
            message = f"📊 <b>10-Minute Report</b>\n"
            message += f"🕒 Time: {datetime.now().strftime('%H:%M:%S')}\n"
            message += f"📈 Checking: {len(symbols)} USDT pairs\n\n"
            
            if pumps:
                message += "🚀 <b>Top Pumps (24h):</b>\n"
                for coin in pumps:
                    coin_name = coin['symbol'].replace('USDT', '')
                    message += f"• {coin_name}: +{coin['change']:.2f}% - {self.format_price(coin['price'])}\n"
                message += "\n"
            
            if dumps:
                message += "📉 <b>Top Dumps (24h):</b>\n"
                for coin in dumps:
                    coin_name = coin['symbol'].replace('USDT', '')
                    message += f"• {coin_name}: {coin['change']:.2f}% - {self.format_price(coin['price'])}\n"
                message += "\n"
            
            if not pumps and not dumps:
                message += "😴 <b>Market Status:</b>\n"
                message += "No significant moves (±2%) detected in checked pairs.\n"
                message += "Market seems stable right now.\n\n"
            
            message += f"⚡ Alert thresholds: ±{self.pump_threshold}%\n"
            message += f"📋 Report thresholds: ±{self.report_pump_threshold}%"
            
            await self.send_telegram(message)
            logger.info(f"Status report sent - Pumps: {len(pumps)}, Dumps: {len(dumps)}")
            
        except Exception as e:
            logger.error(f"Error sending status report: {e}")
    
    async def check_moves(self, symbols: List[str]):
        """Check for significant price moves"""
        if not symbols:
            return
        
        # Check random subset to reduce load
        check_count = min(30, len(symbols))
        selected = random.sample(symbols, check_count)
        
        pumps = 0
        dumps = 0
        
        for symbol in selected:
            kline = await self.get_kline(symbol)
            if not kline:
                continue
                
            change = kline['candle_change']
            
            if change >= self.pump_threshold:
                await self.send_alert(kline, True)
                pumps += 1
            elif change <= self.dump_threshold:
                await self.send_alert(kline, False)
                dumps += 1
        
        return pumps, dumps
    
    async def send_startup_message(self):
        """Send startup notification"""
        symbols = await self.get_symbols()
        message = f"""🤖 <b>Crypto Monitor Started!</b>

🕒 Time: {datetime.now().strftime("%H:%M:%S")}
📈 Monitoring: {len(symbols)} USDT pairs
⚡ Alert thresholds: ±{self.pump_threshold}% (1min candle)
📊 Report thresholds: ±{self.report_pump_threshold}% (24h)
📋 Reports: Every 10 minutes
🔗 Source: Binance API

Bot is now active!"""
        
        return await self.send_telegram(message)
    
    async def run(self):
        """Main monitoring loop"""
        await self.init_session()
        logger.info("Crypto Monitor starting...")
        
        # Send startup message
        await self.send_startup_message()
        
        scan_count = 0
        errors = 0
        self.last_report_time = time.time()
        
        try:
            while self.running:
                start_time = time.time()
                current_time = time.time()
                
                try:
                    symbols = await self.get_symbols()
                    if not symbols:
                        errors += 1
                        logger.error(f"No symbols! Error #{errors}")
                        if errors >= 5:
                            break
                        await asyncio.sleep(300)  # 5 minutes
                        continue
                    
                    errors = 0
                    
                    # Check for immediate alerts
                    pumps, dumps = await self.check_moves(symbols)
                    
                    # Send 10-minute status report
                    if current_time - self.last_report_time >= self.report_interval:
                        await self.send_status_report(symbols)
                        self.last_report_time = current_time
                    
                    scan_count += 1
                    next_report_in = int((self.last_report_time + self.report_interval - current_time) / 60)
                    logger.info(f"Scan #{scan_count} | Pairs: {len(symbols)} | Pumps: {pumps} | Dumps: {dumps} | Next report: {next_report_in}min")
                    
                except Exception as e:
                    errors += 1
                    logger.error(f"Scan error: {e} (#{errors})")
                    if errors >= 10:
                        break
                
                # Sleep calculation
                elapsed = time.time() - start_time
                sleep_time = max(60, 240 - elapsed)  # Target 4 minutes, min 1 minute
                
                logger.info(f"Scan took {elapsed:.1f}s, sleeping {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
                
        except KeyboardInterrupt:
            logger.info("Shutdown requested")
        except Exception as e:
            logger.error(f"Critical error: {e}")
            await self.send_telegram(f"🚨 Bot error: {str(e)[:100]}")
        finally:
            self.running = False
            await self.close_session()
            logger.info("Monitor stopped")

# Web handlers for Render.com
async def home_handler(request):
    return web.Response(text="🤖 Crypto Monitor - Running", content_type='text/plain')

async def health_handler(request):
    return web.json_response({"status": "healthy", "timestamp": datetime.now().isoformat()})

async def init_bot(app):
    """Start bot in background"""
    monitor = CryptoMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())

async def cleanup_bot(app):
    """Cleanup resources"""
    if 'monitor_task' in app:
        app['monitor_task'].cancel()
        try:
            await app['monitor_task']
        except asyncio.CancelledError:
            pass

def create_app():
    """Create web application"""
    app = web.Application()
    app.router.add_get('/', home_handler)
    app.router.add_get('/health', health_handler)
    app.on_startup.append(init_bot)
    app.on_cleanup.append(cleanup_bot)
    return app

if __name__ == "__main__":
    # Environment check
    if not BOT_TOKEN:
        logger.warning("BOT_TOKEN not set - running in test mode")
    if not CHAT_ID:
        logger.warning("CHAT_ID not set - running in test mode")
    
    app = create_app()
    port = int(os.getenv('PORT', 8080))
    
    logger.info(f"Starting server on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
