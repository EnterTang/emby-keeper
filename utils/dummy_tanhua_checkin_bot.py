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

main_photo = Path(__file__).parent / "data/main.png"
main_reply_markup = InlineKeyboardMarkup([
    [
        Button.inline("️👥个人信息", data="info 1000000000"),
        Button.inline("🌐线路信息", data="line 1000000000"),
        Button.inline("😵重置密码", data="reset 1000000000"),
    ],
    [
        Button.inline("🫣隐藏部分分类(当前: 关)", data="hide 1000000000"),
    ],
])

info_reply_markup = InlineKeyboardMarkup([
    [
        Button.inline("🎊签到", data="checkin 1000000000"),
        Button.inline("🏠返回主菜单", data="main 1000000000"),
    ],
])

result_reply_markup = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            Button.inline("🏠返回主菜单", data="main 1000000000"),
        ],
    ]
)


async def dump(client: Client, message: Message):
    if message.text:
        logger.debug(f"<- {message.text}")


async def start(client: Client, message: Message):
    content = dedent(
        """
    ✨ 只有你想见我的时候我们的相遇才有意义

    Jellyfin 当前用户量: 1000

    开放注册状态: 关

    🍉你好鸭 XX 请选择功能👇
    """.strip()
    )
    await client.send_photo(
        message.chat.id,
        main_photo,
        caption=content,
        reply_markup=main_reply_markup,
    )


async def callback_info(client: Client, callback: CallbackQuery):
    content = dedent(
        """
    用户名称: XXX
    绑定 tg id: 1000000000
    部分分类状态: 显示
    探花TV 启用状态: 正常
    bot 绑定时间: Thu Nov 14 10:46:20 CST 2024
    最后登录时间: 2024-01-01T00:00:00.00000Z
    最后活动时间: 2024-01-01T00:00:00.000000Z
    最后观看时间: Mon Jan 1 00:00:00 CST 2024
    积分: 0
    保号规则: 14 内有观看记录(每周五自助解封/150 积分解封)
    """
    ).strip()
    await callback.message.edit_caption(caption=content, reply_markup=info_reply_markup)
    await callback.answer()


async def callback_checkin(client: Client, callback: CallbackQuery):
    if signed.get(callback.from_user.id, None):
        await callback.message.edit_caption(caption="今日已签到", reply_markup=result_reply_markup)
    else:
        await callback.message.edit_caption(
            caption="签到获得积分: 1\n当前积分: 2", reply_markup=result_reply_markup
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
        await bot.add_handler(CallbackQueryHandler(callback_checkin, filters.regex("checkin.*")))
        await bot.add_handler(CallbackQueryHandler(callback_info, filters.regex("info.*")))
        await bot.set_bot_commands(
            [
                BotCommand("start", "Start the bot"),
            ]
        )
        logger.info(f"Started listening for commands: @{bot.me.username}.")
        await asyncio.Event().wait()


if __name__ == "__main__":
    app()
