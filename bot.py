import asyncio
import aiohttp
import os
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = "8454411687:AAGLoczSqO_ptazxaCaBfHiiyL05yMMuCGw"
CHAT_ID = "1758259682"

class MultiCoinMonitor:
    def __init__(self):
        self.session = None
        self.symbols = [
            "XPLUSUSDT", "ASTERUSDT", "SUPERUSDT", "FFUSDT", "LINKUSDT",
            "ADAUSDT", "MYXUSDT", "HYPEUSDT", "ENAUSDT", "ALPINEUSDT",
            "AAVEUSDT", "FARTCOINUSDT", "BLESSUSDT", "KAITOUSDT", "STBLUSDT",
            "ORDERUSDT", "WLDUSDT", "DOTUSDT", "FORMUSDT", "DOODUSDT",
            "WIFUSDT", "COWUSDT", "HBARUSDT", "ONDOUSDT", "MIRAUSDT",
            "PENGUUSDT", "0GUSDT", "SNXUSDT", "PUMPFUNUSDT", "AVNTUSDT",
            "WLFIUSDT", "ZORAUSDT", "YFIUSDT", "CFXUSDT", "PIUSDT",
            "AEROUSDT", "BRETTUSDT", "BBUSDT", "MEMEUSDT", "TRBUSDT",
            "PORT3USDT", "QUSDT", "POLUSDT", "WOOUSDT", "PENDLEUSDT",
            "SANDUSDT", "CROUSDT", "B2USDT", "BOMEUSDT", "DOLUSDT",
            "MAGICUSDT", "1MBABYDOGEUSDT", "SKATEUSDT", "RAYUSDT", "AI16ZUSDT",
            "PEOPLEUSDT", "REZUSDT", "STRKUSDT", "VETUSDT", "AXSUSDT",
            "POPCATUSDT", "TWTUSDT", "DEXEUSDT", "ARUSDT", "FIDAUSDT",
            "PTBUSDT", "ALCHUSDT", "FLOWUSDT", "TREEUSDT", "BLURUSDT",
            "THETAUSDT", "SIGNUSDT", "SUNUSDT", "SFPUSDT", "TRADOORUSDT",
            "FLUIDUSDT", "RUNEUSDT", "SPKUSDT", "MEUSDT", "TSTUSDT",
            "TUTUSDT", "PROVEUSDT", "MAVUSDT", "OGUSDT", "FUNUSDT",
            "BEAMXUSDT", "SOLVUSDT", "IDOLUSDT", "PLUMEUSDT", "LRCUSDT",
            "FLOCKUSDT", "PONKEUSDT", "SUSHIUSDT", "XPINUSDT", "1000000MOGUUSDT",
            "JTOUSDT", "DOGUSDT", "BUSDT", "MANAUSDT", "MOVEUSDT",
            "TURBOUSDT", "AIXBTUSDT", "BANDUSDT", "MITOUSDT", "ICNTUSDT",
            "AWEUSDT", "MERLUSDT", "ZENUSDT"
        ]
        self.threshold = 0.5  # موقتاً برای تست
        
    async def init_session(self):
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        self.session = aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers={'User-Agent': 'MultiCoinMonitor/1.0'}
        )
        logger.info("Session started")
    
    async def close_session(self):
        if self.session:
            await self.session.close()
            logger.info("Session closed")
    
    async def get_1min_candle(self, symbol):
        try:
            url = "https://fapi.bitunix.com/api/v1/futures/market/kline"
            params = {
                'symbol': symbol,
                'interval': '1m',
                'limit': 2
            }
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with self.session.get(url, params=params, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Debug: log first few responses
                    if symbol in ["LINKUSDT", "ADAUSDT", "DOTUSDT"]:
                        logger.info(f"API response for {symbol}: {data}")
                    
                    if isinstance(data, list) and len(data) >= 2:
                        current_candle = data[-1]
                        open_price = float(current_candle[1])
                        close_price = float(current_candle[4])
                        
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
                        if symbol == "BTCUSDT":
                            logger.error(f"Unexpected format: {type(data)}, length: {len(data) if isinstance(data, list) else 'N/A'}")
                        
        except Exception as e:
            logger.error(f"Error getting candle for {symbol}: {e}")
        return None
    
    async def send_telegram(self, message):
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
            logger.error(f"Error sending telegram: {e}")
            return False
    
    async def send_alert(self, coin_name, change, price):
        if change >= self.threshold:
            emoji = "🚀"
            sign = "+"
            alert_type = "PUMP"
        elif change <= -self.threshold:
            emoji = "📉"
            sign = ""
            alert_type = "DUMP"
        else:
            return
        
        message = f"""{emoji} <b>{alert_type}</b>

{coin_name}: {sign}{change:.2f}%
💰 ${price:.6f}
🕒 {datetime.now().strftime("%H:%M:%S")}"""
        
        success = await self.send_telegram(message)
        if success:
            logger.info(f"Alert: {coin_name} {sign}{change:.2f}%")
    
    async def check_all_coins(self):
        for symbol in self.symbols:
            candle_data = await self.get_1min_candle(symbol)
            if candle_data:
                coin_name = symbol.replace('USDT', '')
                change = candle_data['candle_change']
                price = candle_data.get('price')
                
                # فقط log کن
                if abs(change) >= 0.1:  # بیشتر از 0.1% log بزن
                    logger.info(f"{coin_name}: {change:+.2f}%")
                
                await self.send_alert(coin_name, change, price)
            
            await asyncio.sleep(0.2)  # 200ms بین هر request
    
    async def run(self):
        await self.init_session()
        logger.info("Multi-Coin Monitor started!")
        
        await self.send_telegram(f"🤖 <b>Bot Started!</b>\n\n{len(self.symbols)} coins monitoring\nThreshold: +{self.threshold}%\nCheck: every 30 sec")
        
        retry_count = 0
        max_retries = 3
        
        try:
            while True:
                try:
                    await self.check_all_coins()
                    retry_count = 0
                    logger.info(f"Check completed. Next in 30sec")
                    await asyncio.sleep(30)
                    
                except Exception as e:
                    retry_count += 1
                    logger.error(f"Error {retry_count}/{max_retries}: {e}")
                    
                    if retry_count >= max_retries:
                        await self.send_telegram("🚨 Bot having issues. Retry in 5min")
                        await asyncio.sleep(300)
                        retry_count = 0
                    else:
                        await asyncio.sleep(30)
                        
        except KeyboardInterrupt:
            logger.info("Stopping...")
        except Exception as e:
            logger.error(f"Critical error: {e}")
            await self.send_telegram(f"🚨 Critical Error: {str(e)[:100]}")
        finally:
            await self.close_session()

from aiohttp import web

async def health_handler(request):
    return web.json_response({"status": "OK"})

async def init_bot(app):
    monitor = MultiCoinMonitor()
    app['monitor_task'] = asyncio.create_task(monitor.run())

async def cleanup_bot(app):
    if 'monitor_task' in app:
        app['monitor_task'].cancel()

def create_app():
    app = web.Application()
    app.router.add_get('/health', health_handler)
    app.router.add_get('/', health_handler)
    app.on_startup.append(init_bot)
    app.on_cleanup.append(cleanup_bot)
    return app

if __name__ == "__main__":
    app = create_app()
    port = int(os.getenv('PORT', 10000))
    logger.info(f"Starting on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
