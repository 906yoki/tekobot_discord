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

# Конфигурация
DANBOORU_URL = "https://danbooru.donmai.us/posts.json"

# Проверка переменных окружения
def check_environment():
    errors = []

    # Проверка DISCORD_TOKEN
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        errors.append("❌ DISCORD_TOKEN не установлен!")
    else:
        print("✅ DISCORD_TOKEN найден")

    # Проверка CHANNEL_ID
    channel_id_str = os.environ.get("CHANNEL_ID")
    if not channel_id_str:
        errors.append("❌ CHANNEL_ID не установлен!")
    else:
        try:
            channel_id = int(channel_id_str)
            print(f"✅ CHANNEL_ID найден: {channel_id}")
            return channel_id, token
        except ValueError:
            errors.append(
                f"❌ CHANNEL_ID должен быть числом, получено: {channel_id_str}")

    # Показать ошибки и завершить
    if errors:
        print("\n".join(errors))
        print("\nКак исправить на Railway:")
        print("1. Перейдите в Dashboard вашего проекта")
        print("2. Выберите вкладку 'Variables'")
        print("3. Добавьте переменные:")
        print("   DISCORD_TOKEN = ваш_токен_бота")
        print("   CHANNEL_ID = ID_вашего_канала")
        exit(1)

# Проверяем переменные окружения
CHANNEL_ID, DISCORD_TOKEN = check_environment()

RATING = "safe"
TAGS = "1girl solo"
LAST_POST_FILE = "last_checked.json"
ARTIST_COOLDOWN_FILE = "artist_cooldowns.json"
COOLDOWN_HOURS = 1

intents = disnake.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Flask для поддержания активности
app = Flask('')

@app.route('/')
def home():
    return "✅ Бот активен и работает!"

@app.route('/health')
def health():
    # Проверяем статус бота
    bot_status = "ready" if hasattr(bot, 'user') and bot.user else "starting"
    return {
        "status": "ok", 
        "bot": str(bot.user) if bot.user else "not_ready",
        "bot_status": bot_status,
        "platform": "Railway",
        "timestamp": datetime.now().isoformat()
    }

def run_flask():
    """Запускает Flask на порту от Railway"""
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"
    
    print(f"🌐 Flask запускается на {host}:{port}")
    print(f"📊 Railway PORT: {port}")
    
    # Получаем Railway URL если есть
    railway_url = os.environ.get("RAILWAY_STATIC_URL")
    if railway_url:
        print(f"🌍 Railway URL: {railway_url}")
        print(f"🔗 Health Check: {railway_url}/health")
    
    # Запускаем Flask сразу
    app.run(host=host, port=port, threaded=True)

def keep_alive():
    """Запускает Flask в отдельном потоке"""
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    return flask_thread

class DanbooruBot(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.last_checked_id = self.load_last_id()
        self.artist_cooldowns = self.load_artist_cooldowns()
        self.target_channel = None
        print(f"✅ Бот инициализирован. Ожидаемый канал: {CHANNEL_ID}")
        print(f"⏰ КД на художников: {COOLDOWN_HOURS} часа")
        print(f"👨‍🎨 Загружено {len(self.artist_cooldowns)} художников в кд")
        print(f"🚂 Платформа: Railway")

    def load_last_id(self):
        """Загружает последний проверенный ID из файла"""
        try:
            with open(LAST_POST_FILE, "r") as f:
                data = json.load(f)
                return data.get("last_id", 0)
        except FileNotFoundError:
            return 0

    def load_artist_cooldowns(self):
        """Загружает кд художников из файла"""
        try:
            with open(ARTIST_COOLDOWN_FILE, "r") as f:
                data = json.load(f)

                # Очищаем старые записи (старше кд времени)
                cleaned_data = {}
                current_time = datetime.now()

                for artist, last_post_time in data.items():
                    last_time = datetime.fromisoformat(last_post_time)
                    time_diff = current_time - last_time

                    # Сохраняем только если кд еще действует
                    if time_diff < timedelta(hours=COOLDOWN_HOURS):
                        cleaned_data[artist] = last_post_time

                # Если были удалены старые записи, сохраняем
                if len(cleaned_data) != len(data):
                    with open(ARTIST_COOLDOWN_FILE, "w") as f_save:
                        json.dump(cleaned_data, f_save, indent=2)
                    print(
                        f"🗑️  Очищено {len(data) - len(cleaned_data)} устаревших записей"
                    )

                return cleaned_data
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_last_id(self, last_id):
        """Сохраняет последний проверенный ID в файл"""
        with open(LAST_POST_FILE, "w") as f:
            json.dump({"last_id": last_id}, f)

    def save_artist_cooldowns(self):
        """Сохраняет кд художников в файл"""
        with open(ARTIST_COOLDOWN_FILE, "w") as f:
            json.dump(self.artist_cooldowns, f, indent=2, default=str)

    def is_artist_on_cooldown(self, artist_name):
        """Проверяет, находится ли художник в кд"""
        if not artist_name:
            return False

        if artist_name in self.artist_cooldowns:
            last_post_time = datetime.fromisoformat(
                self.artist_cooldowns[artist_name])
            current_time = datetime.now()
            time_diff = current_time - last_post_time

            if time_diff < timedelta(hours=COOLDOWN_HOURS):
                remaining = timedelta(hours=COOLDOWN_HOURS) - time_diff
                hours = int(remaining.seconds // 3600)
                minutes = int((remaining.seconds % 3600) // 60)
                return True, f"{hours}ч {minutes}м"
            else:
                # Удаляем из кд если время вышло
                del self.artist_cooldowns[artist_name]
                self.save_artist_cooldowns()
                return False, None
        return False, None

    def add_artist_to_cooldown(self, artist_name):
        """Добавляет художника в кд"""
        if artist_name:
            self.artist_cooldowns[artist_name] = datetime.now().isoformat()
            self.save_artist_cooldowns()

    async def fetch_posts(self):
        """Получает новые посты с Danbooru"""
        if not self.session:
            self.session = aiohttp.ClientSession()

        params = {"limit": 20, "tags": f"{TAGS} rating:{RATING}", "page": 1}

        if self.last_checked_id > 0:
            params["tags"] += f" id:>{self.last_checked_id}"

        try:
            async with self.session.get(DANBOORU_URL,
                                        params=params) as response:
                if response.status == 200:
                    posts = await response.json()
                    # Фильтруем посты без artist tag
                    posts_with_artist = []
                    for post in posts:
                        if post.get('tag_string_artist'):
                            posts_with_artist.append(post)
                    return posts_with_artist
                else:
                    print(f"⚠️ Ошибка при запросе Danbooru: {response.status}")
                    return []
        except Exception as e:
            print(f"⚠️ Ошибка подключения к Danbooru: {e}")
            return []

    def create_embed(self, post):
        """Создает Embed для поста"""
        embed = disnake.Embed(
            url=f"https://danbooru.donmai.us/posts/{post['id']}",
            color=disnake.Color.random(),
            timestamp=datetime.now())

        # Добавляем изображение
        if post.get('file_url'):
            embed.set_image(url=post['file_url'])
        elif post.get('large_file_url'):
            embed.set_image(url=post['large_file_url'])
        elif post.get('preview_file_url'):
            embed.set_image(url=post['preview_file_url'])
        else:
            return None  # Не создаем embed без изображения

        # Добавляем информацию об авторе
        if post.get('tag_string_artist'):
            artists = post['tag_string_artist'].split()
            embed.add_field(name="**artist**",
                            value=", ".join(artists[:3]),
                            inline=True)

        # Добавляем информацию о персонаже (если есть)
        if post.get('tag_string_character'):
            characters = post['tag_string_character'].split()
            # Ограничиваем количество персонажей, чтобы не перегружать embed
            character_text = ", ".join(characters[:3])
            if len(characters) > 3:
                character_text += f" (+{len(characters) - 3} more)"
            embed.add_field(name="**character**",
                            value=character_text,
                            inline=True)

        # Добавляем информацию об источнике (если есть)
        if post.get('tag_string_copyright'):
            copyrights = post['tag_string_copyright'].split()
            # Ограничиваем количество источников
            copyright_text = ", ".join(copyrights[:2])
            if len(copyrights) > 2:
                copyright_text += f" (+{len(copyrights) - 2} more)"
            embed.add_field(name="**source**",
                            value=copyright_text,
                            inline=True)

        # Добавляем дату создания
        created_at = post.get('created_at')
        if created_at:
            try:
                created_time = datetime.strptime(created_at,
                                                 "%Y-%m-%dT%H:%M:%S.%f%z")
                embed.add_field(name="**created**",
                                value=created_time.strftime("%Y-%m-%d %H:%M"),
                                inline=True)
            except:
                pass

        embed.set_footer(text=f"Tekobot by seomt | Railway")

        return embed

    @tasks.loop(minutes=5)
    async def post_new_art(self):
        """Задача для публикации новых артов"""
        await self.bot.wait_until_ready()

        # Проверяем канал при каждом запуске задачи
        if not self.target_channel:
            self.target_channel = self.bot.get_channel(CHANNEL_ID)

        if not self.target_channel:
            print(f"⚠️ Канал с ID {CHANNEL_ID} не найден!")
            print("Проверьте:")
            print(f"1. Бот добавлен на сервер с каналом {CHANNEL_ID}?")
            print("2. ID канала указан правильно?")
            print("3. Бот имеет доступ к каналу?")

            # Попробуем найти канал по-другому
            for guild in self.bot.guilds:
                for channel in guild.text_channels:
                    if channel.id == CHANNEL_ID:
                        self.target_channel = channel
                        print(
                            f"✅ Канал найден: #{channel.name} на сервере {guild.name}"
                        )
                        break
                if self.target_channel:
                    break

            if not self.target_channel:
                return

        print(
            f"🔍 Проверка новых артов... (последний ID: {self.last_checked_id})"
        )
        print(f"👨‍🎨 Художников в кд: {len(self.artist_cooldowns)}")
        posts = await self.fetch_posts()

        if not posts:
            print("✅ Новых артов не найдено")
            return

        # Сортируем по ID
        posts.sort(key=lambda x: x['id'])

        # Отправляем посты
        sent_count = 0
        skipped_artists = 0
        for post in posts[-20:]:  # Берем 20 самых новых
            try:
                # Получаем художника
                artist_tag = post.get('tag_string_artist', '')
                if not artist_tag:
                    print(
                        f"⚠️ Пропущен арт #{post['id']} (нет тега художника)")
                    continue

                # Берем первого художника из списка
                artist = artist_tag.split()[0]

                # Проверяем кд художника
                on_cooldown, remaining_time = self.is_artist_on_cooldown(
                    artist)

                if on_cooldown:
                    print(
                        f"⏳ Пропущен арт #{post['id']} от {artist} (кд: {remaining_time})"
                    )
                    skipped_artists += 1
                    continue

                # Создаем и отправляем embed
                embed = self.create_embed(post)
                if embed:
                    await self.target_channel.send(embed=embed)
                    sent_count += 1

                    # Добавляем художника в кд
                    self.add_artist_to_cooldown(artist)
                    print(f"✅ Отправлен арт #{post['id']} от {artist}")
                    await asyncio.sleep(2)  # Задержка между сообщениями

            except Exception as e:
                print(f"⚠️ Ошибка при отправке арта #{post['id']}: {e}")

        # Обновляем последний проверенный ID
        if posts:
            new_last_id = max(post['id'] for post in posts)
            self.last_checked_id = new_last_id
            self.save_last_id(new_last_id)
            print(f"📊 Обновлен последний ID: {new_last_id}, "
                  f"отправлено: {sent_count}, "
                  f"пропущено из-за кд: {skipped_artists}")

    @post_new_art.before_loop
    async def before_post_new_art(self):
        """Ожидание готовности бота перед запуском задачи"""
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self):
        """Событие при готовности бота"""
        print(f"\n{'='*50}")
        print(f"🤖 Бот {self.bot.user} готов к работе!")
        print(f"🆔 ID бота: {self.bot.user.id}")
        print(f"📊 Всего серверов: {len(self.bot.guilds)}")
        print(f"🎯 Целевой канал: {CHANNEL_ID}")
        print(f"📝 Последний проверенный ID: {self.last_checked_id}")
        print(f"⏰ КД на художников: {COOLDOWN_HOURS} часа")
        print(f"👨‍🎨 Художников в кд: {len(self.artist_cooldowns)}")
        print(f"🚂 Платформа: Railway")
        print(f"{'='*50}\n")

        # Пытаемся найти канал
        self.target_channel = self.bot.get_channel(CHANNEL_ID)
        if self.target_channel:
            print(f"✅ Канал найден: #{self.target_channel.name}")
            print(f"📌 Сервер: {self.target_channel.guild.name}")
        else:
            print("⚠️ Канал не найден. Пытаюсь найти...")
            for guild in self.bot.guilds:
                print(f"🔍 Сервер: {guild.name} (ID: {guild.id})")
                for channel in guild.text_channels:
                    print(f"  📁 Канал: #{channel.name} (ID: {channel.id})")
                    if channel.id == CHANNEL_ID:
                        self.target_channel = channel
                        print(
                            f"✅ Найден канал #{channel.name} на сервере {guild.name}"
                        )
                        break
                if self.target_channel:
                    break

        # Запускаем задачу
        if not self.post_new_art.is_running():
            self.post_new_art.start()
            print("✅ Задача автопостинга запущена (раз в пять минут)")

    @commands.slash_command(name="test",
                            description="Проверить подключение к каналу")
    async def test_channel(self, inter: disnake.ApplicationCommandInteraction):
        """Тестовая команда для проверки"""
        if self.target_channel:
            await inter.response.send_message(
                f"✅ Бот подключен к каналу #{self.target_channel.name}\n"
                f"📌 Сервер: {self.target_channel.guild.name}\n"
                f"⏰ КД на художников: {COOLDOWN_HOURS} часа\n"
                f"👨‍🎨 Сейчас в кд: {len(self.artist_cooldowns)} художников\n"
                f"🚂 Платформа: Railway",
                ephemeral=True)
        else:
            await inter.response.send_message(
                f"⚠️ Канал с ID {CHANNEL_ID} не найден. Проверьте настройки.",
                ephemeral=True)

    @commands.slash_command(name="force",
                            description="Принудительно проверить новые арты")
    async def force_check(self, inter: disnake.ApplicationCommandInteraction):
        """Принудительная проверка"""
        await inter.response.defer()

        if not self.target_channel:
            await inter.followup.send(
                "❌ Канал не найден. Настройте CHANNEL_ID.")
            return

        await self.post_new_art()
        await inter.followup.send("✅ Проверка завершена!")

    @commands.slash_command(name="cooldowns",
                            description="Показать список художников в кд")
    async def show_cooldowns(self,
                             inter: disnake.ApplicationCommandInteraction):
        """Показать художников в кд"""
        await inter.response.defer()

        if not self.artist_cooldowns:
            await inter.followup.send("📭 Список кд пуст")
            return

        # Сортируем по времени
        sorted_artists = sorted(self.artist_cooldowns.items(),
                                key=lambda x: datetime.fromisoformat(x[1]),
                                reverse=True)

        # Берем первые 20
        lines = []
        current_time = datetime.now()

        for i, (artist, last_post_time) in enumerate(sorted_artists[:20], 1):
            last_time = datetime.fromisoformat(last_post_time)
            time_diff = current_time - last_time
            remaining = timedelta(hours=COOLDOWN_HOURS) - time_diff

            if remaining.total_seconds() > 0:
                hours = int(remaining.seconds // 3600)
                minutes = int((remaining.seconds % 3600) // 60)
                lines.append(f"`{i:2d}.` **{artist}** — {hours}ч {minutes}м")

        if lines:
            embed = disnake.Embed(title="👨‍🎨 Художники в кд",
                                  description="\n".join(lines),
                                  color=disnake.Color.orange(),
                                  timestamp=datetime.now())
            embed.set_footer(
                text=
                f"Всего: {len(self.artist_cooldowns)} | КД: {COOLDOWN_HOURS} часа | Railway"
            )
            await inter.followup.send(embed=embed)
        else:
            await inter.followup.send("📭 Список кд пуст")

    @commands.slash_command(name="clear_cooldowns",
                            description="Очистить все кд художников")
    @commands.has_permissions(administrator=True)
    async def clear_cooldowns(self,
                              inter: disnake.ApplicationCommandInteraction):
        """Очистить кд"""
        await inter.response.defer()

        count = len(self.artist_cooldowns)
        self.artist_cooldowns.clear()
        self.save_artist_cooldowns()

        await inter.followup.send(f"✅ Очищено кд для {count} художников")

    @commands.slash_command(name="url",
                            description="Получить URL для мониторинга")
    async def get_url(self, inter: disnake.ApplicationCommandInteraction):
        """Получить URL бота"""
        # Проверяем Railway переменные
        railway_url = os.environ.get("RAILWAY_STATIC_URL")
        port = os.environ.get("PORT", 8080)
        
        if railway_url:
            base_url = railway_url
            url_type = "Railway URL"
        else:
            # Если нет Railway URL, используем внутренний адрес
            import socket
            try:
                hostname = socket.gethostname()
                base_url = f"http://{hostname}:{port}"
                url_type = "Внутренний URL"
            except:
                base_url = f"http://localhost:{port}"
                url_type = "Локальный URL"

        embed = disnake.Embed(
            title="🌐 URL для мониторинга",
            description=f"**{url_type}:**\n{base_url}\n\n"
            f"**Health Check:**\n{base_url}/health\n\n"
            f"Railway автоматически поддерживает активность бота.",
            color=disnake.Color.green())

        await inter.response.send_message(embed=embed, ephemeral=True)

    @commands.slash_command(name="status",
                            description="Показать статус бота")
    async def bot_status(self, inter: disnake.ApplicationCommandInteraction):
        """Показать статус бота"""
        embed = disnake.Embed(
            title="🤖 Статус бота",
            description="Бот для автоматической публикации артов с Danbooru",
            color=disnake.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="📊 Статус", value="✅ Активен", inline=True)
        embed.add_field(name="🚂 Платформа", value="Railway", inline=True)
        embed.add_field(name="⏰ КД художников", value=f"{COOLDOWN_HOURS} часа", inline=True)
        
        if self.target_channel:
            embed.add_field(name="📺 Канал", value=f"#{self.target_channel.name}", inline=True)
            embed.add_field(name="🏰 Сервер", value=self.target_channel.guild.name, inline=True)
        
        embed.add_field(name="👨‍🎨 В кд", value=f"{len(self.artist_cooldowns)} художников", inline=True)
        embed.add_field(name="🆔 Последний ID", value=str(self.last_checked_id), inline=True)
        
        embed.set_footer(text="Tekobot by seomt")
        
        await inter.response.send_message(embed=embed, ephemeral=True)

# Запуск бота
@bot.event
async def on_ready():
    print(f"✅ Бот {bot.user} успешно запущен и готов!")

    await bot.change_presence(activity=disnake.Activity(
        type=disnake.ActivityType.watching, name="by seomt | Railway"))

# Основной запуск
if __name__ == "__main__":
    print("=" * 50)
    print("🚂 Запуск бота на Railway")
    print("=" * 50)
    
    # Сначала запускаем Flask сервер
    print("\n1. Запуск Flask сервера...")
    flask_thread = keep_alive()
    
    # Ждем немного чтобы Flask точно запустился
    print("2. Ожидание запуска Flask...")
    time.sleep(5)
    
    # Теперь запускаем Discord бота
    print("3. Загрузка Discord бота...")
    bot.add_cog(DanbooruBot(bot))
    
    print("4. Запуск Discord бота...")
    
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        print("Проверьте на Railway:")
        print("1. Правильность DISCORD_TOKEN в Variables")
        print("2. Бот приглашен на сервер")
        print("3. Разрешения бота корректны")
        sys.exit(1)
