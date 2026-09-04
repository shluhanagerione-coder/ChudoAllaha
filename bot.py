import asyncio
import re
import os
from collections import deque

import discord
from discord.ext import commands
from discord import FFmpegPCMAudio, PCMVolumeTransformer
import yt_dlp

try:
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    SPOTIFY_AVAILABLE = True
except ImportError:
    SPOTIFY_AVAILABLE = False

# На некоторых системах (в т.ч. в контейнерах Railway/Nixpacks) discord.py
# не находит libopus по имени через ctypes.util.find_library, хотя она
# установлена — ищем файл библиотеки на диске напрямую и грузим его.
if not discord.opus.is_loaded():
    import glob

    candidates = [
        "libopus.so.0", "libopus.so", "opus", "libopus-0.dll",
    ]
    candidates += glob.glob("/usr/lib/*/libopus.so*")
    candidates += glob.glob("/usr/lib/libopus.so*")
    candidates += glob.glob("/nix/store/*/lib/libopus.so*")
    candidates += glob.glob("/opt/venv/lib/libopus.so*")

    loaded = False
    for opus_path in candidates:
        try:
            discord.opus.load_opus(opus_path)
            loaded = True
            break
        except OSError:
            continue

    if not loaded:
        print("⚠️  Не удалось загрузить libopus ни по одному из путей:", candidates)

# ====================== НАСТРОЙКИ ======================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_БОТА")

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")

COMMAND_PREFIX = "!"
DEFAULT_VOLUME = 0.5

# Оформление embed-сообщений
COLOR_MAIN = 0x8B5CF6     # фиолетовый — обычные сообщения
COLOR_SUCCESS = 0x57F287  # зелёный — успешные действия
COLOR_ERROR = 0xED4245    # красный — ошибки
COLOR_INFO = 0x5865F2     # синий — нейтральная инфа
FOOTER_TEXT = "🎵 Музыкальный бот"
# ========================================================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "socket_timeout": 30,   # ждать ответа сервера дольше, если сеть медленная
    "retries": 5,           # повторить попытку, если сервер не ответил
    "extractor_retries": 5,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

SPOTIFY_TRACK_RE = re.compile(r"open\.spotify\.com/track/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_RE = re.compile(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)")
SPOTIFY_ALBUM_RE = re.compile(r"open\.spotify\.com/album/([a-zA-Z0-9]+)")
URL_RE = re.compile(r"^https?://")

spotify_client = None
if SPOTIFY_AVAILABLE and SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        spotify_client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
            )
        )
    except Exception as e:
        print(f"Не удалось инициализировать Spotify клиент: {e}")


def make_embed(title=None, description=None, color=COLOR_MAIN, thumbnail=None, footer=FOOTER_TEXT):
    embed = discord.Embed(title=title, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if footer:
        embed.set_footer(text=footer)
    return embed


class Track:
    def __init__(self, title, url, webpage_url, duration=None, requester=None, thumbnail=None):
        self.title = title
        self.url = url  # прямой стрим-урл для ffmpeg
        self.webpage_url = webpage_url
        self.duration = duration
        self.requester = requester
        self.thumbnail = thumbnail

    def format_duration(self):
        if not self.duration:
            return "??:??"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h:02}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"


class GuildMusicState:
    def __init__(self, guild_id):
        self.guild_id = guild_id
        self.queue = deque()
        self.voice_client: discord.VoiceClient | None = None
        self.current: Track | None = None
        self.volume = DEFAULT_VOLUME
        self.loop = False

    def next_track(self):
        if self.loop and self.current:
            return self.current
        if self.queue:
            return self.queue.popleft()
        return None


guild_states: dict[int, GuildMusicState] = {}


def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildMusicState(guild_id)
    return guild_states[guild_id]


def extract_youtube_or_direct(query: str) -> Track:
    """Достаёт трек через yt-dlp: работает с YouTube, SoundCloud,
    прямыми ссылками на аудио и обычным текстовым поиском (ищет на YouTube)."""
    data = ytdl.extract_info(query, download=False)
    if "entries" in data:
        data = data["entries"][0]
    return Track(
        title=data.get("title", "Без названия"),
        url=data["url"],
        webpage_url=data.get("webpage_url", query),
        duration=data.get("duration"),
        thumbnail=data.get("thumbnail"),
    )


def resolve_spotify_track(track_id: str) -> str:
    """Возвращает поисковый запрос 'исполнитель - название' по Spotify track id."""
    if not spotify_client:
        raise RuntimeError(
            "Spotify не настроен. Добавь SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET."
        )
    info = spotify_client.track(track_id)
    artists = ", ".join(a["name"] for a in info["artists"])
    return f"{artists} - {info['name']}"


def resolve_spotify_collection(collection_id: str, kind: str) -> list[str]:
    if not spotify_client:
        raise RuntimeError(
            "Spotify не настроен. Добавь SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET."
        )
    queries = []
    if kind == "playlist":
        results = spotify_client.playlist_items(collection_id)
        items = results["items"]
        while results["next"]:
            results = spotify_client.next(results)
            items.extend(results["items"])
        for item in items:
            t = item.get("track")
            if not t:
                continue
            artists = ", ".join(a["name"] for a in t["artists"])
            queries.append(f"{artists} - {t['name']}")
    else:  # album
        results = spotify_client.album_tracks(collection_id)
        items = results["items"]
        while results["next"]:
            results = spotify_client.next(results)
            items.extend(results["items"])
        for t in items:
            artists = ", ".join(a["name"] for a in t["artists"])
            queries.append(f"{artists} - {t['name']}")
    return queries


async def resolve_queries(query: str) -> list[str]:
    """Превращает вход пользователя (ссылка/текст) в список поисковых
    запросов/ссылок, которые уже можно скормить в yt-dlp."""
    loop = asyncio.get_event_loop()

    m = SPOTIFY_TRACK_RE.search(query)
    if m:
        q = await loop.run_in_executor(None, resolve_spotify_track, m.group(1))
        return [q]

    m = SPOTIFY_PLAYLIST_RE.search(query)
    if m:
        return await loop.run_in_executor(
            None, resolve_spotify_collection, m.group(1), "playlist"
        )

    m = SPOTIFY_ALBUM_RE.search(query)
    if m:
        return await loop.run_in_executor(
            None, resolve_spotify_collection, m.group(1), "album"
        )

    # YouTube / SoundCloud / прямые ссылки / обычный текст — всё это
    # напрямую понимает yt-dlp (в т.ч. поиск по ytsearch).
    return [query]


def play_next(guild_id: int):
    state = get_state(guild_id)
    nxt = state.next_track()
    if not nxt:
        state.current = None
        return

    state.current = nxt
    source = PCMVolumeTransformer(
        FFmpegPCMAudio(nxt.url, **FFMPEG_OPTIONS), volume=state.volume
    )

    def after_playing(error):
        if error:
            print(f"Ошибка плеера: {error}")
        fut = asyncio.run_coroutine_threadsafe(_advance(guild_id), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Ошибка при переходе к следующему треку: {e}")

    state.voice_client.play(source, after=after_playing)


async def _advance(guild_id: int):
    play_next(guild_id)


async def ensure_voice(ctx) -> GuildMusicState:
    state = get_state(ctx.guild.id)
    if ctx.author.voice is None or ctx.author.voice.channel is None:
        raise commands.CommandError("Зайди сначала в голосовой канал.")

    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await ctx.author.voice.channel.connect()
    elif state.voice_client.channel != ctx.author.voice.channel:
        await state.voice_client.move_to(ctx.author.voice.channel)

    return state


class ServiceSelect(discord.ui.Select):
    def __init__(self, query: str):
        options = [
            discord.SelectOption(label="YouTube", value="yt", emoji="▶️"),
            discord.SelectOption(label="SoundCloud", value="sc", emoji="☁️"),
        ]
        super().__init__(placeholder="Выбери сервис для поиска...", options=options)
        self.query = query
        self.chosen: str | None = None

    async def callback(self, interaction: discord.Interaction):
        self.chosen = self.values[0]
        self.view.stop()
        label = "YouTube" if self.chosen == "yt" else "SoundCloud"
        embed = make_embed(
            title="🔎 Ищу трек...",
            description=f"**{self.query}**\nИсточник: {label}",
            color=COLOR_INFO,
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class ServiceView(discord.ui.View):
    def __init__(self, author: discord.abc.User, query: str, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.select = ServiceSelect(query)
        self.add_item(self.select)
        self.author = author

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Это меню не для тебя — напиши свою команду !play.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        self.select.disabled = True


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user}")
    print(f"Opus загружен: {discord.opus.is_loaded()}")


async def _enqueue_and_play(ctx, state: "GuildMusicState", raw_query: str, status_msg=None):
    """Резолвит запрос(ы), добавляет треки в очередь и запускает воспроизведение."""
    try:
        queries = await resolve_queries(raw_query)
    except Exception as e:
        embed = make_embed(title="❌ Ошибка", description=str(e), color=COLOR_ERROR)
        if status_msg:
            await status_msg.edit(content=None, embed=embed)
        else:
            await ctx.send(embed=embed)
        return

    loop = asyncio.get_event_loop()
    added_tracks = []
    for q in queries:
        try:
            track = await loop.run_in_executor(None, extract_youtube_or_direct, q)
            track.requester = ctx.author.display_name
            state.queue.append(track)
            added_tracks.append(track)
        except Exception as e:
            await ctx.send(embed=make_embed(
                title="⚠️ Пропускаю трек",
                description=f"`{q}`\n{e}",
                color=COLOR_ERROR,
            ))

    if not added_tracks:
        embed = make_embed(title="❌ Ничего не нашлось", color=COLOR_ERROR)
        if status_msg:
            await status_msg.edit(content=None, embed=embed)
        else:
            await ctx.send(embed=embed)
        return

    if len(added_tracks) == 1:
        t = added_tracks[0]
        embed = make_embed(
            title="➕ Добавлено в очередь",
            description=f"**{t.title}**\n⏱️ {t.format_duration()} · запросил {t.requester}",
            color=COLOR_SUCCESS,
            thumbnail=t.thumbnail,
        )
    else:
        embed = make_embed(
            title="➕ Добавлено в очередь",
            description=f"**{len(added_tracks)}** треков",
            color=COLOR_SUCCESS,
        )

    if status_msg:
        await status_msg.edit(content=None, embed=embed)
    else:
        await ctx.send(embed=embed)

    if not state.voice_client.is_playing() and not state.voice_client.is_paused():
        play_next(ctx.guild.id)


@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query: str = ""):
    """!play <название / ссылка YouTube / SoundCloud / Spotify / прямая ссылка>"""
    state = await ensure_voice(ctx)

    # Вложение (аудиофайл), прикреплённое к сообщению
    if not query and ctx.message.attachments:
        query = ctx.message.attachments[0].url

    if not query:
        await ctx.send(embed=make_embed(
            title="Нужно название или ссылка",
            description="Пример: `!play never gonna give you up`",
            color=COLOR_ERROR,
        ))
        return

    # Если это уже готовая ссылка (YouTube/SoundCloud/Spotify/прямая на
    # файл) — сервис и так понятен из самой ссылки, меню выбора не нужно.
    if URL_RE.match(query):
        status_msg = await ctx.send(embed=make_embed(
            title="🔎 Обрабатываю ссылку...",
            description=query,
            color=COLOR_INFO,
        ))
        await _enqueue_and_play(ctx, state, query, status_msg=status_msg)
        return

    # Обычный текстовый запрос без ссылки — спрашиваем, где искать
    view = ServiceView(ctx.author, query)
    embed = make_embed(
        title="Где искать?",
        description=f"**{query}**",
        color=COLOR_MAIN,
    )
    msg = await ctx.send(embed=embed, view=view)
    await view.wait()

    if view.select.chosen is None:
        await msg.edit(embed=make_embed(
            title="⌛ Время выбора истекло",
            description=f"**{query}**",
            color=COLOR_ERROR,
        ), view=None)
        return

    prefix = "scsearch" if view.select.chosen == "sc" else "ytsearch"
    await _enqueue_and_play(ctx, state, f"{prefix}:{query}", status_msg=msg)


@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    state = get_state(ctx.guild.id)
    if state.voice_client and (state.voice_client.is_playing() or state.voice_client.is_paused()):
        state.voice_client.stop()  # вызовет after_playing -> следующий трек
        await ctx.send(embed=make_embed(title="⏭️ Пропускаю", color=COLOR_MAIN))
    else:
        await ctx.send(embed=make_embed(title="Сейчас ничего не играет", color=COLOR_ERROR))


@bot.command(name="pause")
async def pause(ctx):
    state = get_state(ctx.guild.id)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.pause()
        await ctx.send(embed=make_embed(title="⏸️ Пауза", color=COLOR_MAIN))


@bot.command(name="resume")
async def resume(ctx):
    state = get_state(ctx.guild.id)
    if state.voice_client and state.voice_client.is_paused():
        state.voice_client.resume()
        await ctx.send(embed=make_embed(title="▶️ Продолжаю", color=COLOR_MAIN))


@bot.command(name="stop")
async def stop(ctx):
    state = get_state(ctx.guild.id)
    state.queue.clear()
    state.current = None
    if state.voice_client:
        state.voice_client.stop()
    await ctx.send(embed=make_embed(title="⏹️ Остановлено", description="Очередь очищена", color=COLOR_MAIN))


@bot.command(name="leave", aliases=["disconnect", "dc"])
async def leave(ctx):
    state = get_state(ctx.guild.id)
    if state.voice_client:
        await state.voice_client.disconnect()
        state.voice_client = None
        state.queue.clear()
        state.current = None
    await ctx.send(embed=make_embed(title="👋 Вышел из канала", color=COLOR_MAIN))


@bot.command(name="queue", aliases=["q"])
async def queue_cmd(ctx):
    state = get_state(ctx.guild.id)
    if not state.current and not state.queue:
        await ctx.send(embed=make_embed(title="Очередь пуста", color=COLOR_INFO))
        return

    embed = make_embed(title="📜 Очередь", color=COLOR_MAIN)

    if state.current:
        embed.add_field(
            name="▶️ Сейчас играет",
            value=f"**{state.current.title}** [{state.current.format_duration()}]",
            inline=False,
        )
        if state.current.thumbnail:
            embed.set_thumbnail(url=state.current.thumbnail)

    upcoming = list(state.queue)[:10]
    if upcoming:
        lines = [f"**{i}.** {t.title} [{t.format_duration()}]" for i, t in enumerate(upcoming, start=1)]
        if len(state.queue) > 10:
            lines.append(f"...и ещё {len(state.queue) - 10} треков")
        embed.add_field(name="Далее", value="\n".join(lines), inline=False)

    await ctx.send(embed=embed)


@bot.command(name="nowplaying", aliases=["np"])
async def nowplaying(ctx):
    state = get_state(ctx.guild.id)
    if state.current:
        embed = make_embed(
            title="▶️ Сейчас играет",
            description=f"**{state.current.title}**\n⏱️ {state.current.format_duration()}",
            color=COLOR_MAIN,
            thumbnail=state.current.thumbnail,
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send(embed=make_embed(title="Сейчас ничего не играет", color=COLOR_INFO))


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx, level: int):
    state = get_state(ctx.guild.id)
    level = max(0, min(150, level))
    state.volume = level / 100
    if state.voice_client and state.voice_client.source:
        state.voice_client.source.volume = state.volume
    await ctx.send(embed=make_embed(title="🔊 Громкость", description=f"{level}%", color=COLOR_MAIN))


@bot.command(name="loop")
async def loop_cmd(ctx):
    state = get_state(ctx.guild.id)
    state.loop = not state.loop
    status = "включён" if state.loop else "выключен"
    await ctx.send(embed=make_embed(title="🔁 Повтор текущего трека", description=status, color=COLOR_MAIN))


@bot.event
async def on_command_error(ctx, error):
    await ctx.send(embed=make_embed(title="⚠️ Ошибка", description=str(error), color=COLOR_ERROR))


if __name__ == "__main__":
    if DISCORD_TOKEN == "ВСТАВЬ_СЮДА_ТОКЕН_БОТА":
        print("⚠️  Укажи токен бота в переменной окружения DISCORD_TOKEN или прямо в коде.")
    bot.run(DISCORD_TOKEN)
