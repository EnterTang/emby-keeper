import asyncio
from io import BytesIO
from pathlib import Path
import random
import string

from loguru import logger
import tomli as tomllib
from telethon import events, Button
from telethon.tl.types import Message, User
from telethon.tl.custom import InlineKeyboardMarkup

from embykeeper.utils import AsyncTyper
from embykeeper.telechecker.tele import Client

user_states = {}

app = AsyncTyper()


async def dump(client: Client, message: Message):
    if message.text:
        logger.debug(f"<- {message.text}")


async def start(client: Client, message: Message):
    await client.send_message(message.sender_id, "你好! 请使用命令进行签到测试!")


async def check_answer(client: Client, callback):
    if callback.sender_id not in user_states:
        await callback.answer("未知用户")
    else:
        await callback.answer()
        del user_states[callback.sender_id]
        if user_states[callback.sender_id] == int(callback.data):
            await callback.message.reply("成功")
        else:
            await callback.message.reply("失败")


async def send_question(client: Client, message: Message):
    a = random.randint(10, 100)
    b = random.randint(10, 100)
    user_states[message.sender_id] = a + b
    answers = []
    while len(answers) < 3:
        w = random.randint(10, 100)
        if w == a + b:
            continue
        else:
            answers.append(w)
    answers.append(a + b)
    random.shuffle(answers)
    markup = InlineKeyboardMarkup([
        [Button.inline(str(w), data=str(w)) for w in answers]
    ])
    await message.reply(f"{a} + {b} = ?", buttons=markup)


@app.async_command()
async def main(config: Path):
    with open(config, "rb") as f:
        config = tomllib.load(f)
    bot = Client(
        name="test_bot",
        bot_token=config["bot"]["token"],
        proxy=config.get("proxy", None),
        workdir=Path(__file__).parent,
    )
    async with bot:
        await bot.add_handler(MessageHandler(dump), group=1)
        await bot.add_handler(MessageHandler(start, filters.command("start")))
        await bot.add_handler(MessageHandler(send_question, filters.command("checkin")))
        await bot.add_handler(CallbackQueryHandler(check_answer))
        await bot.set_bot_commands(
            [
                BotCommand("start", "Start the bot"),
                BotCommand("checkin", "Checkin session for test"),
            ]
        )
        logger.info(f"Started listening for commands: @{bot.me.username}.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    app()
