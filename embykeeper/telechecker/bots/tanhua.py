from telethon.tl.custom import Message
from telethon import Button

from embykeeper.utils import to_iterable

from ._base import BotCheckin


class TanhuaCheckin(BotCheckin):
    name = "探花"
    bot_username = "TanhuaTvBot"
    additional_auth = ["prime"]
    bot_checkin_cmd = ["/start"]
    templ_panel_keywords = ["请选择功能", "用户面板", "用户名称"]
    bot_use_captcha = False

    async def message_handler(self, client, message: Message):
        text = message.caption or message.text
        if (
            text
            and any(keyword in text for keyword in to_iterable(self.templ_panel_keywords))
            and message.buttons
        ):
            buttons = [b for row in message.buttons for b in row]
            for button in buttons:
                if isinstance(button, Button.Inline):
                    if "个人信息" in button.text:
                        try:
                            await message.click(data=button.data)
                        except TimeoutError:
                            pass
                        return
                    if "签到" in button.text or "簽到" in button.text:
                        try:
                            await message.click(data=button.data)
                        except TimeoutError:
                            self.log.debug(f"点击签到按钮无响应, 可能按钮未正确处理点击回复. 一般来说不影响签到.")
                        return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()

        if message.text and "请先加入聊天群组和通知频道" in message.text:
            self.log.warning(f"签到失败: 账户错误.")
            return await self.fail()

        await super().message_handler(client, message)
