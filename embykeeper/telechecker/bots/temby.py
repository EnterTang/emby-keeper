import re
from urllib.parse import parse_qs, urlparse
from typing import Optional, Union

from telethon import TelegramClient, events, types, functions, Button
from aiohttp import ClientSession, TCPConnector
from aiohttp_socks import ProxyConnector, ProxyTimeoutError, ProxyError, ProxyType
from faker import Faker

from ..link import Link
from ._base import BotCheckin

__ignore__ = True


class TembyCheckin(BotCheckin):
    name = "Temby"
    bot_username = "HiEmbyBot"
    bot_checkin_cmd = "/hi"
    bot_success_pat = r".*(\d+)"
    max_retries = 1
    additional_auth = ["captcha"]
    bot_account_fail_keywords = ["需要邀请码"]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._session = None
        self._connector = None
        
    async def cleanup(self):
        """清理资源"""
        if self._session:
            await self._session.close()
        if self._connector:
            await self._connector.close()

    async def message_handler(self, client: TelegramClient, message: types.Message):
        try:
            if not message.text:
                return await super().message_handler(client, message)
                
            if message.text == "请在一分钟内点击下方按钮完成签到":
                if message.buttons:
                    buttons = [b for row in message.buttons for b in row]
                    if len(buttons) == 1:
                        button = buttons[0]
                        if isinstance(button, Button.Url) and button.text == "签到":
                            url = await self.get_app_url(button.url)
                            if not url:
                                self.log.warning("无法获取签到 URL")
                                return
                                
                            result = await self.solve_captcha(url)
                            if result:
                                await self.on_text(message, result)
                                return
                            else:
                                self.log.error("签到失败: 验证码解析失败, 正在重试.")
                                await self.retry()
                                return

            return await super().message_handler(client, message)
            
        except Exception as e:
            self.log.error(f"处理消息时发生错误: {str(e)}")
            await self.retry()

    async def get_app_url(self, url: str) -> Optional[str]:
        """获取应用 URL"""
        try:
            match = re.search(r"t\.me/(\w+)/(\w+)\?startapp=(\w+)", url)
            if not match:
                return None
                
            bot_username, app_short_name, start_param = match.groups()
            
            # 获取 bot 实体
            bot = await self.client._client.get_entity(bot_username)
            
            # 获取应用信息
            app_result = await self.client._client(functions.messages.GetBotAppRequest(
                bot=bot,
                app=types.InputBotAppShortName(
                    bot_id=bot.id,
                    short_name=app_short_name
                ),
                hash=0
            ))
            
            if not isinstance(app_result, types.BotApp):
                return None
                
            # 请求 web view
            result = await self.client._client(functions.messages.RequestAppWebViewRequest(
                peer=bot,
                app=types.InputBotAppID(
                    id=app_result.id,
                    access_hash=app_result.access_hash
                ),
                start_param=start_param,
                platform="ios"
            ))
            
            return result.url if isinstance(result, types.AppWebViewResultUrl) else None
            
        except Exception as e:
            self.log.error(f"获取应用 URL 时发生错误: {str(e)}")
            return None

    async def solve_captcha(self, url: str) -> Optional[str]:
        """解决验证码"""
        try:
            token = await Link(self.client).captcha("temby")
            if not token:
                return None
                
            # 解析 URL 参数
            scheme = urlparse(url)
            params = parse_qs(scheme.query)
            messageid = params.get("tgWebAppStartParam", [None])[0]
            url_submit = scheme._replace(query="", fragment="").geturl()
            
            # 设置代理
            if self.proxy:
                self._connector = ProxyConnector(
                    proxy_type=ProxyType[self.proxy["scheme"].upper()],
                    host=self.proxy["hostname"],
                    port=self.proxy["port"],
                    username=self.proxy.get("username"),
                    password=self.proxy.get("password"),
                    rdns=True
                )
            else:
                self._connector = TCPConnector(verify_ssl=False)
                
            # 准备请求
            useragent = Faker().safari()
            headers = {
                "Referer": url,
                "User-Agent": useragent,
            }
            params = {
                "messageid": messageid,
                "url": "",
                "cf-turnstile-response": token,
            }
            
            # 发送请求
            self._session = ClientSession(connector=self._connector)
            async with self._session.get(
                url_submit, 
                headers=headers, 
                params=params,
                timeout=30
            ) as resp:
                result = await resp.text()
                match = re.search(r"<h1>(.*)</h1>", result)
                return match.group(1) if match else None
                
        except (ProxyTimeoutError, ProxyError) as e:
            self.log.error(f"代理连接错误: {str(e)}")
            return None
        except asyncio.TimeoutError:
            self.log.error("请求超时")
            return None
        except Exception as e:
            self.log.error(f"解析验证码时发生错误: {str(e)}")
            return None
        finally:
            await self.cleanup()
