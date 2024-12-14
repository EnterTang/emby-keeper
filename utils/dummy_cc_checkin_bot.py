import asyncio
from pathlib import Path
import random
from textwrap import dedent

from loguru import logger
import tomli as tomllib
from telethon import events, Button
from telethon.tl.types import Message, User
from telethon.tl.custom import InlineKeyboardMarkup

from embykeeper.utils import AsyncTyper
from embykeeper.telechecker.tele import Client, API_KEY

app = AsyncTyper()

states = {}
signed = {}

main_photo = Path(__file__).parent / "data/cc/main.jpg"
main_reply_markup = InlineKeyboardMarkup([
    [Button.inline("🕹️签到", data="checkin")],
    [
        Button.inline("🔱账号", data="account"),
        Button.inline("🔖百宝箱", data="redeem_menu"),
    ],
    [
        Button.inline("💌服务器", data="server_info"),
        Button.inline("🛠️帮助", data="help_mention"),
        Button.inline("❌ 关闭", data="close"),
    ],
])


async def dump(client: Client, message: Message):
    if message.text:
        logger.debug(f"<- {message.text}")


async def start(client: Client, message: Message):
    content = dedent(
        """
    🍉欢迎使用 **Cc** Bot!

    📠请在下方选择您要使用的功能!
    """.strip()
    )
    await client.send_photo(
        message.chat.id,
        main_photo,
        caption=content,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_reply_markup,
    )


async def callback_checkin(client: Client, callback: CallbackQuery):
    captchas_dir_path = Path(__file__).parent / "data/cc/captchas"
    captchas_paths = list(captchas_dir_path.glob("*.jpg"))
    captcha_names = [p.stem for p in captchas_paths]
    selected_image: Path = random.choice(captchas_paths)
    selected_filename = selected_image.stem
    other_filenames = random.sample([n for n in captcha_names if not n == selected_filename], 3)
    filenames = [selected_filename, *other_filenames]
    random.shuffle(filenames)
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=filename, callback_data="yzm_" + filename)] for filename in filenames]
    )
    states[callback.from_user.id] = selected_filename
    await client.send_photo(
        chat_id=callback.message.chat.id,
        photo=open(selected_image, "rb"),
        caption="请选择正确验证码",
        reply_markup=keyboard,
    )
    await callback.answer()


async def callback_yzm(client: Client, callback: CallbackQuery):
    yzm = callback.data.split("_")[1]
    if yzm == states.get(callback.from_user.id, None):
        if signed.get(callback.from_user.id, None):
            content = dedent(
                """
                您今天已经签到过了
                ⚖️ 累计签到：1
                💰 当前积分:1
                🪙 当前Cc币:1
                """.strip()
            )
            await client.send_photo(
                callback.message.chat.id,
                main_photo,
                caption=content,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_reply_markup,
            )
        else:
            signed[callback.from_user.id] = True
            content = dedent(
                """
                🎉 签到成功，获得了 1积分
                💰总积分：1
                """.strip()
            )
            await client.send_photo(
                callback.message.chat.id,
                main_photo,
                caption=content,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=main_reply_markup,
            )
    else:
        content = dedent(
            """
        🎉 签到失败, 验证码错误
        """.strip()
        )
        await client.send_photo(
            callback.message.chat.id,
            main_photo,
            caption=content,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_reply_markup,
        )
    await callback.answer()


@app.async_command()
async def main(config: Path):
    with open(config, "rb") as f:
        config = tomllib.load(f)
    for k in API_KEY.values():
        api_id = k["api_id"]
        api_hash = k["api_hash"]
    bot = Client(
        name="test_bot",
        bot_token=config["bot"]["token"],
        proxy=config.get("proxy", None),
        workdir=Path(__file__).parent,
        api_id=api_id,
        api_hash=api_hash,
        in_memory=True,
    )
    async with bot:
        await bot.add_handler(MessageHandler(dump), group=1)
        await bot.add_handler(MessageHandler(start, filters.command("start")))
        await bot.add_handler(CallbackQueryHandler(callback_checkin, filters.regex("checkin")))
        await bot.add_handler(CallbackQueryHandler(callback_yzm, filters.regex("^yzm_.*")))
        await bot.set_bot_commands(
            [
                BotCommand("start", "Start the bot"),
            ]
        )
        logger.info(f"Started listening for commands: @{bot.me.username}.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    app()
