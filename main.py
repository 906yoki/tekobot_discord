import os
import json
import aiohttp
import disnake
from disnake.ext import tasks, commands
from datetime import datetime, timedelta
import asyncio
from flask import Flask
from threading import Thread
import time
import sys

# --- Конфигурация ---
DANBOORU_URL = "https://danbooru.donmai.us/posts.json"
LAST_POST_FILE = "last_checked.json"
ARTIST_COOLDOWN_FILE = "artist_cooldowns.json"
COOLDOWN_HOURS = 1
RATING = "safe"
TAGS = "1girl solo"

# --- Проверка переменных окружения ---
def check_environment():
    errors = []
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        errors.append("❌ DISCORD_TOKEN не установлен!")

    channel_id_str = os.environ.get("CHANNEL_ID")
    if not channel_id_str:
        errors.append("❌ CHANNEL_ID не установлен!")
    else:
        try:
            channel_id = int(channel_id_str)
            return channel_id, token
        except ValueError:
            errors.append(f"❌ CHANNEL_ID должен быть числом, получено: {channel_id_str}")

    if errors:
        print("\n".join(errors))
        exit(1)

CHANNEL_ID, DISCORD_TOKEN = check_environment()

# --- Flask для Railway (Keep Alive) ---
app = Flask('')

@app.route('/')
def home():
    return "✅ Бот активен и работает!"

@app.route('/health')
def health():
    bot_status = "ready" if hasattr(bot, 'user') and bot.user else "starting"
    return {
        "status": "ok",
        "bot_status": bot_status,
        "timestamp": datetime.now().isoformat()
    }

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, threaded=True)

def keep_alive():
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

# --- Логика Бота ---
intents = disnake.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

class DanbooruBot(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._session = None # Используем приватную переменную для сессии
        self.last_checked_id = self.load_last_id()
        self.artist_cooldowns = self.load_artist_cooldowns()
        self.target_channel = None

    def cog_unload(self):
        """ЗАКРЫТИЕ СЕССИИ: Исправляет ошибку Unclosed client session"""
        if self._session and not self._session.closed:
            loop = asyncio.get_event_loop()
            loop.create_task(self._session.close())
            print("Cleanup: aiohttp session closed.")

    async def get_session(self):
        """Гарантирует наличие сессии в асинхронном контексте"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def load_last_id(self):
        try:
            with open(LAST_POST_FILE, "r") as f:
                return json.load(f).get("last_id", 0)
        except (FileNotFoundError, json.JSONDecodeError):
            return 0

    def load_artist_cooldowns(self):
        try:
            with open(ARTIST_COOLDOWN_FILE, "r") as f:
                data = json.load(f)
            cleaned_data = {}
            current_time = datetime.now()
            for artist, last_post_time in data.items():
                last_time = datetime.fromisoformat(last_post_time)
                if current_time - last_time < timedelta(hours=COOLDOWN_HOURS):
                    cleaned_data[artist] = last_post_time
            return cleaned_data
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_last_id(self, last_id):
        with open(LAST_POST_FILE, "w") as f:
            json.dump({"last_id": last_id}, f)

    def save_artist_cooldowns(self):
        with open(ARTIST_COOLDOWN_FILE, "w") as f:
            json.dump(self.artist_cooldowns, f, indent=2)

    def is_artist_on_cooldown(self, artist_name):
        if not artist_name or artist_name not in self.artist_cooldowns:
            return False, None
        
        last_time = datetime.fromisoformat(self.artist_cooldowns[artist_name])
        remaining = timedelta(hours=COOLDOWN_HOURS) - (datetime.now() - last_time)
        
        if remaining.total_seconds() > 0:
            hours, remainder = divmod(int(remaining.total_seconds()), 3600)
            minutes = remainder // 60
            return True, f"{hours}ч {minutes}м"
        
        del self.artist_cooldowns[artist_name]
        return False, None

    async def fetch_posts(self):
        session = await self.get_session()
        params = {"limit": 20, "tags": f"{TAGS} rating:{RATING}", "page": 1}
        if self.last_checked_id > 0:
            params["tags"] += f" id:>{self.last_checked_id}"

        try:
            async with session.get(DANBOORU_URL, params=params) as response:
                if response.status == 200:
                    posts = await response.json()
                    return [p for p in posts if p.get('tag_string_artist')]
                return []
        except Exception as e:
            print(f"⚠️ Ошибка Danbooru: {e}")
            return []

    def create_embed(self, post):
        embed = disnake.Embed(
            url=f"https://danbooru.donmai.us/posts/{post['id']}",
            color=disnake.Color.dark_grey(), # Твой монохромный стиль
            timestamp=datetime.now()
        )
        img_url = post.get('file_url') or post.get('large_file_url') or post.get('preview_file_url')
        if not img_url: return None
        
        embed.set_image(url=img_url)
        
        if post.get('tag_string_artist'):
            artists = post['tag_string_artist'].split()
            embed.add_field(name="**artist**", value=", ".join(artists[:3]), inline=True)
        
        if post.get('tag_string_character'):
            chars = post['tag_string_character'].split()
            embed.add_field(name="**character**", value=", ".join(chars[:3]), inline=True)

        embed.set_footer(text="Tekobot | Danbooru Automation")
        return embed

    @tasks.loop(minutes=5)
    async def post_new_art(self):
        await self.bot.wait_until_ready()
        if not self.target_channel:
            self.target_channel = self.bot.get_channel(CHANNEL_ID)
        
        if not self.target_channel: return

        posts = await self.fetch_posts()
        if not posts: return

        posts.sort(key=lambda x: x['id'])
        
        for post in posts[-10:]: # Берем последние 10 для безопасности
            artist_tag = post.get('tag_string_artist', '').split()
            if not artist_tag: continue
            
            artist = artist_tag[0]
            on_cooldown, _ = self.is_artist_on_cooldown(artist)
            
            if on_cooldown: continue

            embed = self.create_embed(post)
            if embed:
                await self.target_channel.send(embed=embed)
                self.artist_cooldowns[artist] = datetime.now().isoformat()
                await asyncio.sleep(2)

        new_last_id = max(post['id'] for post in posts)
        self.last_checked_id = new_last_id
        self.save_last_id(new_last_id)
        self.save_artist_cooldowns()

    @post_new_art.before_loop
    async def before_post(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"🤖 Бот {self.bot.user} онлайн!")
        if not self.post_new_art.is_running():
            self.post_new_art.start()

    # --- Команды ---
    @commands.slash_command(name="status", description="Статус бота")
    async def status(self, inter):
        embed = disnake.Embed(title="🤖 Tekobot Status", color=disnake.Color.silver())
        embed.add_field(name="В кд художников", value=len(self.artist_cooldowns))
        embed.add_field(name="Последний ID", value=self.last_checked_id)
        await inter.response.send_message(embed=embed, ephemeral=True)

# --- Запуск ---
if __name__ == "__main__":
    keep_alive()
    bot.add_cog(DanbooruBot(bot))
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Ошибка запуска: {e}")
        sys.exit(1)
