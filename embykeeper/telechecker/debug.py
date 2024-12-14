import asyncio
import operator

import aiofiles
import yaml
from dateutil import parser
from loguru import logger
from telethon import events
from telethon.tl.types import (
    Message,
    User,
    Chat,
    Channel,
    KeyboardButtonCallback,
    ReplyInlineMarkup,
    ReplyKeyboardMarkup,
    ChatType,
)
from rich import box
from rich.live import Live
from rich.panel import Panel
from rich.table import Column, Table
from rich.text import Text

from ..utils import async_partial, batch, flatten, idle, time_in_range
from .tele import Client, ClientsSession

log = logger.bind(scheme="debugtool")


async def _dump_message(client: Client, message: Message, table: Table):
    """消息调试工具, 将消息更新列到 table 中."""
    text = message.text or message.raw_text
    if text:
        text = text.replace("\n", " ")
        if not text:
            return
    else:
        return
    if message.sender:
        user = message.sender
        sender_id = str(user.id)
        sender_icon = "👤"
        if message.out:
            sender = Text("Me", style="bold red")
            text = Text(text, style="red")
        else:
            sender = getattr(user, 'first_name', '') or ''
            if getattr(user, 'bot', False):
                sender_icon = "🤖"
                sender = Text(sender, style="bold yellow")
    else:
        sender = sender_id = sender_icon = None

    chat_id = "{: }".format(message.chat_id)
    chat = message.chat
    if isinstance(chat, (Chat, Channel)):
        if chat.megagroup or (isinstance(chat, Chat) and not chat.broadcast):
            chat_title = chat.title
            chat_icon = "👥"
        elif chat.broadcast:
            chat_title = chat.title
            chat_icon = "📢"
        elif getattr(chat, 'bot', False):
            chat_title = None
            chat_icon = "🤖"
        else:
            chat_title = chat_icon = None
    else:
        chat_title = chat_icon = None

    others = []
    if message.photo:
        others.append(f"照片: {message.photo.id}")
    if message.reply_markup:
        if isinstance(message.reply_markup, ReplyInlineMarkup):
            key_info = "|".join([k.text for r in message.reply_markup.rows for k in r.buttons if hasattr(k, 'text')])
            others.append(f"按钮: {key_info}")
        elif isinstance(message.reply_markup, ReplyKeyboardMarkup):
            key_info = "|".join([k.text for r in message.reply_markup.rows for k in r.buttons if hasattr(k, 'text')])
            others.append(f"按钮: {key_info}")
    return table.add_row(
        f"{client.me.first_name}",
        "│",
        chat_icon,
        chat_title,
        chat_id,
        "│",
        sender_icon,
        sender,
        sender_id,
        "│",
        text,
        "|",
        "; ".join(others),
    )


async def follower(config: dict):
    """消息调试工具入口函数."""
    columns = [
        Column("用户", style="cyan", justify="center"),
        Column("", max_width=1, style="white"),
        Column("", max_width=2, overflow="crop"),
        Column("会话", style="bright_blue", no_wrap=True, justify="right", max_width=15),
        Column("(ChatID)", style="gray50", no_wrap=True, max_width=20),
        Column("", max_width=1, style="white"),
        Column("", max_width=2, overflow="crop"),
        Column("发信人", style="green", no_wrap=True, max_width=15, justify="right"),
        Column("(UserID)", style="gray50", no_wrap=True, max_width=15),
        Column("", max_width=1, style="white"),
        Column("信息", no_wrap=False, min_width=30, max_width=50),
        Column("", max_width=1, style="white"),
        Column("其他", no_wrap=False, min_width=30, max_width=50),
    ]
    async with ClientsSession.from_config(config) as clients:
        table = Table(*columns, header_style="bold magenta", box=box.SIMPLE)
        func = async_partial(_dump_message, table=table)
        async for tg in clients:
            tg.add_event_handler(func, events.NewMessage())
            tg.add_event_handler(func, events.MessageEdited())
        with Live(table, refresh_per_second=4, vertical_overflow="visible"):
            await idle()


def _dumper_mask_update(update):
    if getattr(update, "out", False):
        sender = getattr(update, "sender", None)
        if sender:
            if hasattr(sender, "id"):
                setattr(sender, "id", "(sender)")
            if hasattr(sender, "first_name"):
                setattr(sender, "first_name", "(sender)")
            if hasattr(sender, "last_name"):
                setattr(sender, "last_name", "(sender)")
            if hasattr(sender, "username"):
                setattr(sender, "username", "(sender)")
    return update


async def _dumper_raw(event):
    await event.client.queue.put(_dumper_mask_update(event))


async def _dumper_update(event):
    if isinstance(event, list):
        for e in event:
            await event.client.queue.put(_dumper_mask_update(e))
    else:
        await event.client.queue.put(_dumper_mask_update(event))


async def dumper(config: dict, specs=["message"]):
    type_handler = {
        "message": (events.NewMessage, _dumper_update),
        "edited_message": (events.MessageEdited, _dumper_update),
        "raw": (events.Raw, _dumper_raw),
    }
    queue = asyncio.Queue()
    async with ClientsSession.from_config(config) as clients:
        async for tg in clients:
            tg.queue = queue
            for s in specs:
                try:
                    t, c = s.split("@")
                    c = [int(i.strip()) for i in c.split(",")]
                except ValueError:
                    t = s
                    c = []
                if t == "all":
                    handlers = [(type_handler[t][0], type_handler[t][1]) for t in ["message", "edited_message"]]
                    for event, handler in handlers:
                        tg.add_event_handler(handler, event(chats=c) if c else event())
                    break
                else:
                    try:
                        event, handler = type_handler[t]
                    except KeyError:
                        log.warning(f'更新类型 {t} 不可用, 请选择: {", ".join(list(type_handler.keys()))}')
                        continue
                    tg.add_event_handler(handler, event(chats=c) if c else event())
            log.info(f'开始监控账号: "{tg.me.first_name}" 中的更新.')
        while True:
            update = str(await queue.get())
            print(update, flush=True)


async def _saver_raw(event):
    await event.client.saver_queue.put(event)


async def _saver_dumper(queue, output):
    async with aiofiles.open(output, "w+", buffering=1, encoding="utf-8") as f:
        while True:
            update = await queue.get()
            await f.write(str(update) + "\n")


async def saver(config: dict):
    async with ClientsSession.from_config(config) as clients:
        tasks = []
        async for tg in clients:
            tg.saver_queue = queue = asyncio.Queue()
            output = f"{tg.me.phone}.updates.json"
            tg.add_event_handler(_saver_raw, events.Raw())
            tasks.append(_saver_dumper(queue, output))
        await asyncio.gather(*tasks)


class IndentDumper(yaml.Dumper):
    """输出带缩进的 YAML."""

    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


async def analyzer(config: dict, chats, keywords, timerange, limit=10000, outputs=1000):
    """历史消息分析工具入口函数."""

    from rich.progress import MofNCompleteColumn, Progress, SpinnerColumn

    def render_page(progress, texts):
        page = Table.grid()
        page.add_row(Panel(progress))
        if texts:
            msgs = sorted(texts.items(), key=operator.itemgetter(1), reverse=True)
            columns = flatten([[Column(max_width=15, no_wrap=True), Column(min_width=2)] for _ in range(4)])
            table = Table(*columns, show_header=False, box=box.SIMPLE)
            cols = []
            for col in batch(msgs, 12):
                col = [(t.split()[0], str(c)) for t, c in col]
                col += [("", "")] * (12 - len(col))
                cols.append(col)
                if len(cols) >= 4:
                    break
            for row in map(list, zip(*cols)):
                table.add_row(*flatten(row))
            page.add_row(table)
        return page

    texts = {}
    if timerange:
        start, end = (parser.parse(t).time() for t in timerange)
    async with ClientsSession.from_config(config) as clients:
        async for tg in clients:
            target = f"{tg.me.phone}.msgs.yaml"
            log.info(f'开始分析账号: "{tg.me.first_name}", 结果将写入"{target}".')
            pcs = list(Progress.get_default_columns())
            pcs.insert(0, SpinnerColumn())
            pcs.insert(3, MofNCompleteColumn(table_column=Column(justify="center")))
            p = Progress(*pcs, transient=True)
            with Live(render_page(p, texts)) as live:
                updates = 0
                pchats = p.add_task("[red]会话: ", total=len(chats))
                for c in chats:
                    c = c.rsplit("/", 1)[-1]
                    pmsgs = p.add_task("[red]记录: ", total=limit)
                    m: Message
                    async for m in tg.get_chat_history(c, limit=limit):
                        if m.text:
                            if m.from_user and not m.from_user.is_bot:
                                if (not keywords) or any(s in m.text for s in keywords):
                                    if (not timerange) or time_in_range(start, end, m.date.time()):
                                        if m.text in texts:
                                            texts[m.text] += 1
                                        else:
                                            texts[m.text] = 1
                                        updates += 1
                                        if updates % 200 == 0:
                                            live.update(render_page(p, texts))
                        p.advance(pmsgs)
                    p.update(pmsgs, visible=False)
                    p.advance(pchats)
            with open(target, "w+", encoding="utf-8") as f:
                yaml.dump(
                    {
                        "messages": [
                            str(t) for t, _ in sorted(texts.items(), key=operator.itemgetter(1), reverse=True)
                        ][:outputs]
                    },
                    f,
                    default_flow_style=False,
                    encoding="utf-8",
                    allow_unicode=True,
                    Dumper=IndentDumper,
                )
