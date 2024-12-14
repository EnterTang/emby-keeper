import asyncio
import random
import string

from thefuzz import process
from telethon import types, events, Button

from ..link import Link
from ._base import BotCheckin


class CCCheckin(BotCheckin):
    name = "CC公益"
    bot_username = "EmbyCc_bot"
    bot_checkin_cmd = "/start"
    bot_checked_keywords = ["已经签到过了"]
    bot_checkin_caption_pat = "请选择正确验证码"
    max_retries = 1
    additional_auth = ["ocr"]

    async def message_handler(self, client, message: types.Message):
        if message.caption and "欢迎使用" in message.caption and message.buttons:
            keys = [k.text for row in message.buttons for k in row]
            for k in keys:
                if "签到" in k:
                    try:
                        await message.click(data=k)
                    except TimeoutError:
                        pass
                    return
            else:
                self.log.warning(f"签到失败: 账户错误.")
                return await self.fail()
        await super().message_handler(client, message)

    async def on_photo(self, message: types.Message):
        """分析分析传入的验证码图片并返回验证码."""
        if not message.buttons:
            return
            
        # 获取图片文件ID
        if message.photo:
            photo = message.photo
            file_id = photo.id
        else:
            return
            
        for i in range(3):
            result: str = await Link(self.client).ocr(file_id)
            if result:
                self.log.debug(f"远端已解析答案: {result}.")
                break
            else:
                self.log.warning(f"远端解析失败, 正在重试解析 ({i + 1}/3).")
        else:
            self.log.warning(f"签到失败: 验证码识别错误.")
            return await self.fail()
            
        options = [k.text for row in message.buttons for k in row]
        result = result.translate(str.maketrans("", "", string.punctuation)).replace(" ", "")
        captcha, score = process.extractOne(result, options)
        
        if score < 50:
            self.log.warning(f"远端答案难以与可用选项相匹配 (分数: {score}/100).")
            
        self.log.debug(f"[gray50]接收验证码: {captcha}.[/]")
        await asyncio.sleep(random.uniform(2, 4))
        
        try:
            await message.click(data=captcha)
        except TimeoutError:
            pass
