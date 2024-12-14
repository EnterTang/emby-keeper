from telethon.tl.custom import Message
from telethon import Button

from ._base import BotCheckin


class MagicCheckin(BotCheckin):
    name = "魔法Emby"
    bot_username = "Magic_EmbyBot"
    bot_checkin_cmd = "/start"
    bot_captcha_len = 2
    bot_checkin_caption_pat = "请输入验证码"

    async def message_handler(self, client, message: Message):
        if message.caption and "欢迎使用" in message.caption and message.buttons:
            buttons = [b for row in message.buttons for b in row]
            for button in buttons:
                if isinstance(button, Button.Inline) and "签到" in button.text:
                    try:
                        await message.click(data=button.data)
                    except TimeoutError:
                        pass
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
        await super().message_handler(client, message)
