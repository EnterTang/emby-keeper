import asyncio
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientSession, TCPConnector
from aiohttp_socks import ProxyConnector, ProxyTimeoutError, ProxyError, ProxyType
from telethon import functions, types
from faker import Faker

from ...utils import remove_prefix
from ..link import Link
from ._base import BaseBotCheckin

__ignore__ = True


class NebulaCheckin(BaseBotCheckin):
    name = "Nebula"
    bot_username = "Nebula_Account_bot"

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.failed = False
        self.timeout *= 3
        self._session = None
        self._connector = None

    async def fail(self):
        self.failed = True
        self.finished.set()
        await self._cleanup()

    async def _cleanup(self):
        """清理资源"""
        if self._session:
            await self._session.close()
        if self._connector:
            await self._connector.close()

    async def start(self):
        try:
            try:
                await asyncio.wait_for(self._checkin(), self.timeout)
            except asyncio.TimeoutError:
                self.log.warning("签到超时")
                return False
        except OSError as e:
            self.log.info(f'发生错误: "{e}".')
            return False
        finally:
            await self._cleanup()
            
        if not self.finished.is_set():
            self.log.warning("无法在时限内完成签到.")
            return False
        return not self.failed

    async def _checkin(self):
        try:
            # 获取 bot 信息
            bot = await self.client._client.get_entity(self.bot_username)
            self.log.info(f"开始执行签到: [green]{bot.first_name}[/] [gray50](@{bot.username})[/].")
            
            # 获取 bot 完整信息
            bot_full = await self.client._client(functions.users.GetFullUserRequest(bot))
            url = bot_full.full_user.bot_info.menu_button.url
            
            # 请求 web view
            result = await self.client._client(functions.messages.RequestWebViewRequest(
                peer=bot,
                bot=bot,
                platform="ios",
                url=url
            ))
            url_auth = result.url
            
            self.log.debug(f"请求面板: {url_auth}")
            
            # 解析 URL
            scheme = urlparse(url_auth)
            data = remove_prefix(scheme.fragment, "tgWebAppData=")
            url_base = scheme._replace(
                path="/api/proxy/userCheckIn", 
                query=f"data={data}", 
                fragment=""
            ).geturl()
            
            scheme = urlparse(url_base)
            query = parse_qs(scheme.query, keep_blank_values=True)
            query = {k: v for k, v in query.items() if not k.startswith("tgWebApp")}
            
            # 获取验证码
            token = await Link(self.client).captcha("nebula")
            if not token:
                self.log.warning("签到失败: 无法获得验证码.")
                return await self.fail()
                
            # 准备请求
            useragent = Faker().safari()
            query["token"] = token
            url_checkin = scheme._replace(query=urlencode(query, True)).geturl()
            
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
                
            # 发送签到请求
            self._session = ClientSession(connector=self._connector)
            async with self._session.get(
                url_checkin, 
                headers={"User-Agent": useragent},
                timeout=30
            ) as resp:
                results = await resp.json()
                
            # 处理响应
            message = results["message"]
            if any(s in message for s in ("未找到用户", "权限错误")):
                self.log.info("签到失败: 账户错误.")
                await self.fail()
            elif "失败" in message:
                self.log.info("签到失败.")
                await self.fail()
            elif "已经" in message:
                self.log.info("今日已经签到过了.")
                self.finished.set()
            elif "成功" in message:
                self.log.info(
                    f"[yellow]签到成功[/]: + {results['data']['get_credit']} 分 -> {results['data']['credit']} 分."
                )
                self.finished.set()
            else:
                self.log.warning(f"接收到异常返回信息: {message}")
                
        except (ProxyTimeoutError, ProxyError) as e:
            self.log.info(f"签到失败: 代理连接错误 - {str(e)}")
            await self.fail()
        except asyncio.TimeoutError:
            self.log.info("签到失败: 请求超时")
            await self.fail()
        except Exception as e:
            self.log.error(f"签到失败: {str(e)}")
            await self.fail()
